$ErrorActionPreference = "Stop"

$rootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Read-Text([string]$relativePath) {
    return Get-Content -LiteralPath (Join-Path $rootDir $relativePath) -Raw
}

function Match-Version(
    [string]$relativePath,
    [string]$pattern,
    [string]$authority
) {
    $text = Read-Text $relativePath
    $match = [regex]::Match($text, $pattern)
    if (-not $match.Success) {
        throw "Could not read $authority version from $relativePath"
    }
    return $match.Groups[1].Value
}

$frontendPackage = Read-Text "frontend/package.json" | ConvertFrom-Json
$tauriConfig = Read-Text "shell/tauri.conf.json" | ConvertFrom-Json

$installer = Get-ChildItem `
    -LiteralPath (Join-Path $rootDir "shell/target/release/bundle/nsis") `
    -Filter "Merc Forge_*_x64-setup.exe" `
    -File `
    -ErrorAction Stop |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
if ($null -eq $installer -or $installer.Name -notmatch '^Merc Forge_([0-9]+\.[0-9]+\.[0-9]+)_x64-setup\.exe$') {
    throw "Could not read the local installer version"
}
$installerVersion = $Matches[1]
$installerInfo = $installer.VersionInfo

$versions = [ordered]@{
    "VERSION authority" = (Read-Text "VERSION").Trim()
    "frontend/package.json" = [string]$frontendPackage.version
    "frontend/package-lock.json root" = Match-Version "frontend/package-lock.json" '(?s)^\s*\{\s*"name"\s*:\s*"mercwizard-frontend",\s*"version"\s*:\s*"([^"]+)"' "package-lock root"
    "frontend/package-lock.json package" = Match-Version "frontend/package-lock.json" '(?s)"packages"\s*:\s*\{\s*""\s*:\s*\{\s*"name"\s*:\s*"mercwizard-frontend",\s*"version"\s*:\s*"([^"]+)"' "package-lock top-level package"
    "shell/tauri.conf.json" = [string]$tauriConfig.version
    "shell/Cargo.toml package" = Match-Version "shell/Cargo.toml" '(?ms)^\[package\].*?^version\s*=\s*"([^"]+)"' "Cargo package"
    "shell/Cargo.lock mercwizard" = Match-Version "shell/Cargo.lock" '(?ms)^\[\[package\]\]\s*^name\s*=\s*"mercwizard"\s*^version\s*=\s*"([^"]+)"' "Cargo lock package"
    "local installer filename" = $installerVersion
    "local installer FileVersion" = [string]$installerInfo.FileVersion
    "local installer ProductVersion" = [string]$installerInfo.ProductVersion
    "README installer" = Match-Version "README.md" 'Merc Forge_([0-9]+\.[0-9]+\.[0-9]+)_x64-setup\.exe' "README installer"
    "sidecar/pyproject.toml project" = Match-Version "sidecar/pyproject.toml" '(?ms)^\[project\].*?^version\s*=\s*"([^"]+)"' "sidecar package"
    "mercwizard_core.__version__" = Match-Version "sidecar/mercwizard_core/__init__.py" '__version__\s*=\s*"([^"]+)"' "core runtime"
    "FastAPI metadata" = Match-Version "sidecar/main.py" '(?s)FastAPI\(.*?version\s*=\s*"([^"]+)"' "FastAPI metadata"
    "GET /version" = Match-Version "sidecar/routes/health.py" '"tool_version"\s*:\s*"([^"]+)"' "GET /version"
    "Settings About" = Match-Version "frontend/src/routes/Settings.tsx" 'Merc Forge v([0-9]+\.[0-9]+\.[0-9]+)' "Settings About"
    "wmerc manifest default" = Match-Version "sidecar/mercwizard_core/bundle/manifest.py" 'tool_version:\s*str\s*=\s*"([^"]+)"' "wmerc manifest default"
    "WMERC format example" = Match-Version "docs/WMERC_FORMAT.md" '"tool_version"\s*:\s*"([^"]+)"' "WMERC format example"
    "testing matrix About" = Match-Version "docs/TESTING_MATRIX.md" 'Sidecar version:\s*([0-9]+\.[0-9]+\.[0-9]+)' "testing matrix About"
}

$groups = @($versions.GetEnumerator() | Group-Object Value | Sort-Object Name)
if ($groups.Count -ne 1) {
    $expectedDrift = [ordered]@{
        "1.0.0" = @(
            "frontend/package.json",
            "frontend/package-lock.json root",
            "frontend/package-lock.json package",
            "shell/tauri.conf.json",
            "shell/Cargo.toml package",
            "shell/Cargo.lock mercwizard"
        )
        "1.0.1" = @(
            "VERSION authority",
            "local installer filename",
            "local installer FileVersion",
            "local installer ProductVersion",
            "README installer"
        )
        "2.0.0" = @(
            "sidecar/pyproject.toml project",
            "mercwizard_core.__version__",
            "FastAPI metadata",
            "GET /version",
            "Settings About"
            "wmerc manifest default",
            "WMERC format example",
            "testing matrix About"
        )
    }

    foreach ($group in $groups) {
        $actualNames = @($group.Group.Name | Sort-Object)
        $expectedNames = @($expectedDrift[$group.Name] | Sort-Object)
        if ($null -eq $expectedDrift[$group.Name] -or
            (Compare-Object $actualNames $expectedNames)) {
            throw "Unexpected version drift at $($group.Name): $($actualNames -join ', ')"
        }
        Write-Host "$($group.Name): $($actualNames -join ', ')"
    }

    throw "Version authorities drift; expected one product version, found $($groups.Name -join ', ')"
}

Write-Host "All version authorities agree on $($groups[0].Name)."
