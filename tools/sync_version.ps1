[CmdletBinding(DefaultParameterSetName = "Check")]
param(
    [Parameter(Mandatory = $true, ParameterSetName = "Check")]
    [switch]$Check,

    [Parameter(Mandatory = $true, ParameterSetName = "Write")]
    [switch]$Write
)

$ErrorActionPreference = "Stop"
$rootDir = Split-Path -Parent $PSScriptRoot
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Read-Utf8([string]$path) {
    return [IO.File]::ReadAllText($path)
}

function Write-Utf8([string]$path, [string]$content) {
    [IO.File]::WriteAllText($path, $content, $utf8NoBom)
}

$versionPath = Join-Path $rootDir "VERSION"
if (-not (Test-Path -LiteralPath $versionPath -PathType Leaf)) {
    throw "Missing release authority: $versionPath"
}

$versionText = Read-Utf8 $versionPath
if ($versionText -notmatch '^([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)\r?\n?$') {
    throw "VERSION must contain exactly one SemVer line."
}
$version = $Matches[1]

# Each pattern captures prefix, current product version, and suffix. Context is
# deliberately specific so dependency versions are never rewritten.
$targets = @(
    @{ Path = "frontend/package.json"; Pattern = '(?s)(\A\s*\{\s*"name"\s*:\s*"mercwizard-frontend",\s*"private"\s*:\s*true,\s*"version"\s*:\s*")([^"]+)(")' },
    @{ Path = "frontend/package-lock.json"; Pattern = '(?s)(\A\s*\{\s*"name"\s*:\s*"mercwizard-frontend",\s*"version"\s*:\s*")([^"]+)(")' },
    @{ Path = "frontend/package-lock.json"; Pattern = '(?s)("packages"\s*:\s*\{\s*""\s*:\s*\{\s*"name"\s*:\s*"mercwizard-frontend",\s*"version"\s*:\s*")([^"]+)(")' },
    @{ Path = "shell/tauri.conf.json"; Pattern = '(?s)("productName"\s*:\s*"Merc Forge",\s*"version"\s*:\s*")([^"]+)(")' },
    @{ Path = "shell/Cargo.toml"; Pattern = '(?ms)(^\[package\].*?^name\s*=\s*"mercwizard"\s*^version\s*=\s*")([^"]+)(")' },
    @{ Path = "shell/Cargo.lock"; Pattern = '(?ms)(^\[\[package\]\]\s*^name\s*=\s*"mercwizard"\s*^version\s*=\s*")([^"]+)(")' },
    @{ Path = "sidecar/pyproject.toml"; Pattern = '(?ms)(^\[project\].*?^name\s*=\s*"mercwizard-core"\s*^version\s*=\s*")([^"]+)(")' },
    @{ Path = "sidecar/mercwizard_core/__init__.py"; Pattern = '(__version__\s*=\s*")([^"]+)(")' },
    @{ Path = "sidecar/main.py"; Pattern = '(?s)(FastAPI\(.*?version\s*=\s*")([^"]+)(")' },
    @{ Path = "sidecar/routes/health.py"; Pattern = '("tool_version"\s*:\s*")([^"]+)(")' },
    @{ Path = "sidecar/mercwizard_core/bundle/manifest.py"; Pattern = '(tool_version:\s*str\s*=\s*")([^"]+)(")' },
    @{ Path = "frontend/src/components/settings/AboutPanel.tsx"; Pattern = '(Merc Forge v)([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)(\s)' },
    @{ Path = "README.md"; Pattern = '(Merc Forge_)([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)(_x64-setup\.exe)' },
    @{ Path = "docs/WMERC_FORMAT.md"; Pattern = '("tool_version"\s*:\s*")([^"]+)(")' },
    @{ Path = "docs/TESTING_MATRIX.md"; Pattern = '(Sidecar version:\s*)([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)(" appears)' }
)

$drift = [System.Collections.Generic.List[string]]::new()
$writes = [System.Collections.Generic.Dictionary[string,string]]::new()

foreach ($target in $targets) {
    $path = Join-Path $rootDir $target.Path
    $text = if ($writes.ContainsKey($path)) { $writes[$path] } else { Read-Utf8 $path }
    $matches = [regex]::Matches($text, $target.Pattern)
    if ($matches.Count -ne 1) {
        throw "$($target.Path): expected exactly one product-version field, found $($matches.Count)."
    }

    $current = $matches[0].Groups[2].Value
    if ($current -ne $version) {
        $drift.Add("$($target.Path): $current -> $version")
        if ($Write) {
            $replacement = $matches[0].Groups[1].Value + $version + $matches[0].Groups[3].Value
            $text = [regex]::Replace($text, $target.Pattern, [Text.RegularExpressions.MatchEvaluator]{ param($match) $replacement }, 1)
            $writes[$path] = $text
        }
    }
}

if ($Write) {
    foreach ($entry in $writes.GetEnumerator()) {
        Write-Utf8 $entry.Key $entry.Value
    }
    if ($drift.Count -eq 0) {
        Write-Host "Version manifests already match VERSION ($version)."
    } else {
        Write-Host "Synchronized $($drift.Count) product-version fields to $version."
    }
    exit 0
}

$installerDir = Join-Path $rootDir "shell/target/release/bundle/nsis"
if (Test-Path -LiteralPath $installerDir -PathType Container) {
    $installer = Get-ChildItem -LiteralPath $installerDir -Filter "Merc Forge_*_x64-setup.exe" -File |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if ($null -ne $installer) {
        if ($installer.Name -notmatch '^Merc Forge_([^_]+)_x64-setup\.exe$' -or $Matches[1] -ne $version) {
            $drift.Add("local installer filename: $($installer.Name) -> Merc Forge_${version}_x64-setup.exe")
        }
        if ([string]$installer.VersionInfo.FileVersion -ne $version) {
            $drift.Add("local installer FileVersion: $($installer.VersionInfo.FileVersion) -> $version")
        }
        if ([string]$installer.VersionInfo.ProductVersion -ne $version) {
            $drift.Add("local installer ProductVersion: $($installer.VersionInfo.ProductVersion) -> $version")
        }
    }
}

if ($drift.Count -gt 0) {
    Write-Error ("Product-version drift from VERSION ($version):`n - " + ($drift -join "`n - "))
    exit 1
}

Write-Host "All product-version surfaces match VERSION ($version)."
