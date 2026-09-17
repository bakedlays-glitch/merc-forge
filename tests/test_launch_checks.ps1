$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$helper = Join-Path $repoRoot "launch_checks.ps1"
. $helper

$tempRoot = Join-Path $PSScriptRoot ("_tmp_launch_" + [guid]::NewGuid().ToString("N"))
$frontendSources = Join-Path $tempRoot "frontend-src"
$shellSources = Join-Path $tempRoot "shell-src"
$frontendIndex = Join-Path $tempRoot "frontend-dist-index.html"
$exe = Join-Path $tempRoot "mercwizard.exe"

try {
    New-Item -ItemType Directory -Path $frontendSources,$shellSources -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $frontendSources "app.ts") -Value "frontend"
    Set-Content -LiteralPath (Join-Path $shellSources "main.rs") -Value "shell"
    Set-Content -LiteralPath $frontendIndex -Value "dist"
    Set-Content -LiteralPath $exe -Value "exe"

    $base = [datetime]::SpecifyKind([datetime]"2026-01-01T00:00:00", "Utc")
    (Get-Item (Join-Path $frontendSources "app.ts")).LastWriteTimeUtc = $base
    (Get-Item (Join-Path $shellSources "main.rs")).LastWriteTimeUtc = $base
    (Get-Item $exe).LastWriteTimeUtc = $base.AddMinutes(1)
    (Get-Item $frontendIndex).LastWriteTimeUtc = $base.AddMinutes(2)

    $distOnlyStale = Test-TauriRebuildNeeded @($frontendSources) $frontendIndex @($shellSources) $exe
    if (-not $distOnlyStale) {
        throw "Expected a newer frontend dist to make the embedded executable stale."
    }

    (Get-Item $exe).LastWriteTimeUtc = $base.AddMinutes(3)
    $fullyCurrent = Test-TauriRebuildNeeded @($frontendSources) $frontendIndex @($shellSources) $exe
    if ($fullyCurrent) {
        throw "Expected no rebuild when the executable is newer than sources and dist."
    }

    (Get-Item (Join-Path $frontendSources "app.ts")).LastWriteTimeUtc = $base.AddMinutes(4)
    $sourceStale = Test-TauriRebuildNeeded @($frontendSources) $frontendIndex @($shellSources) $exe
    if (-not $sourceStale) {
        throw "Expected a newer frontend source to request a rebuild."
    }

    Write-Host "PASS: Tauri freshness checks cover source, dist, and executable edges."
}
finally {
    $resolvedTests = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
    $resolvedTemp = [IO.Path]::GetFullPath($tempRoot)
    if ($resolvedTemp.StartsWith($resolvedTests, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedTemp).StartsWith("_tmp_launch_")) {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
