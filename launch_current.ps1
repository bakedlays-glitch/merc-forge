# launch_current.ps1 -- one-click "rebuild what's stale + launch" for MercWizard 2.
#
# What it does:
#   1. Compares mtimes of sidecar/frontend/shell sources against their build artifacts.
#   2. Rebuilds only the parts that have changed (PyInstaller for sidecar, cargo
#      tauri build for shell -- tauri's beforeBuildCommand handles the frontend).
#   3. Launches shell/target/release/Merc Wizard.exe.
#
# Designed for the workflow "I just want to test the current version" -- point
# the Start Menu .lnk at this script and clicking it always gives you whatever
# the source tree says, with incremental rebuild times when nothing has changed.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

function Write-Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Skip($msg) { Write-Host "    [skip] $msg" -ForegroundColor DarkGray }
function Write-Done($msg) { Write-Host "    [done] $msg" -ForegroundColor Green }
function Fail($msg) {
    Write-Host ""
    Write-Host "!!! $msg" -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to close this window"
    exit 1
}

# Paths
# Use `mercwizard.exe` (cargo's package-named output) rather than
# `Merc Wizard.exe` because the productName rename only happens during the
# Tauri bundle step (NSIS installer creation). We pass `--no-bundle` to
# skip that step for testing speed, so only the cargo-named .exe is fresh.
# The window title still says "Merc Wizard" (from tauri.conf.json), so the
# user experience is identical.
$exe            = Join-Path $root "shell\target\release\mercwizard.exe"
$sidecarBin     = Join-Path $root "shell\binaries\mercwizard_core-x86_64-pc-windows-msvc.exe"
$sidecarRuntime = Join-Path $root "shell\target\release\mercwizard_core.exe"
$pyDist         = Join-Path $root "sidecar\dist\mercwizard_core.exe"
$frontendIndex  = Join-Path $root "frontend\dist\index.html"
$venvPy         = Join-Path $root "sidecar\.venv\Scripts\python.exe"
$tauriCli       = Join-Path $root "frontend\node_modules\.bin\tauri.cmd"

# Source-set definitions. Anything under these paths newer than the artifact
# triggers a rebuild.
$sidecarSources = @(
    "sidecar\main.py", "sidecar\mercwizard_core", "sidecar\routes",
    "sidecar\ja2py", "sidecar\mercwizard_core.spec"
) | ForEach-Object { Join-Path $root $_ }

$frontendSources = @(
    "frontend\src", "frontend\package.json", "frontend\tsconfig.json", "frontend\vite.config.ts"
) | ForEach-Object { Join-Path $root $_ }

$shellSources = @(
    "shell\src", "shell\Cargo.toml", "shell\build.rs", "shell\tauri.conf.json"
) | ForEach-Object { Join-Path $root $_ }

function Newer-Than-Artifact($paths, $artifact) {
    if (-not (Test-Path -LiteralPath $artifact)) { return $true }
    $artTime = (Get-Item -LiteralPath $artifact).LastWriteTimeUtc
    foreach ($p in $paths) {
        if (-not (Test-Path -LiteralPath $p)) { continue }
        $hit = Get-ChildItem -LiteralPath $p -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTimeUtc -gt $artTime } |
            Select-Object -First 1
        if ($hit) { return $true }
    }
    return $false
}

$rebuildSidecar = Newer-Than-Artifact $sidecarSources $sidecarRuntime
$rebuildTauri   = (Newer-Than-Artifact $frontendSources $frontendIndex) -or
                  (Newer-Than-Artifact $shellSources $exe)

if (-not ($rebuildSidecar -or $rebuildTauri)) {
    Write-Step "No source changes since last build -- launching existing build."
} else {
    Write-Step "Stopping any running Merc Wizard processes..."
    Get-Process -Name "Merc Wizard","mercwizard","mercwizard_core" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 600

    if ($rebuildSidecar) {
        Write-Step "Rebuilding sidecar (PyInstaller)..."
        Push-Location (Join-Path $root "sidecar")
        try {
            # NOTE: do NOT pipe `2>&1` to a PowerShell consumer here. PyInstaller
            # writes its progress lines to stderr, and Windows PowerShell 5.1
            # wraps every native-command stderr line in a NativeCommandError
            # ErrorRecord. With $ErrorActionPreference = "Stop", the first such
            # line aborts the script. Letting stderr stream natively to the
            # console is correct -- we still check $LASTEXITCODE for failures.
            & $venvPy -m PyInstaller mercwizard_core.spec --clean --noconfirm
            if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed (exit $LASTEXITCODE)" }
            if (-not (Test-Path -LiteralPath $pyDist)) { Fail "PyInstaller succeeded but $pyDist is missing" }
            Copy-Item -LiteralPath $pyDist -Destination $sidecarBin -Force
            Copy-Item -LiteralPath $pyDist -Destination $sidecarRuntime -Force
            Write-Done "sidecar binary updated in shell/binaries/ and shell/target/release/"
        } finally { Pop-Location }
    } else {
        Write-Skip "sidecar binary is current"
    }

    if ($rebuildTauri) {
        Write-Step "Rebuilding Tauri shell (cargo + frontend)..."
        Push-Location (Join-Path $root "shell")
        try {
            # --no-bundle skips the NSIS installer (we don't need it for testing).
            # Tauri's beforeBuildCommand in tauri.conf.json automatically runs
            # `npm --prefix frontend run build` so the frontend gets rebuilt too.
            # No `2>&1` for the same NativeCommandError reason documented above.
            & $tauriCli build --no-bundle
            if ($LASTEXITCODE -ne 0) { Fail "Tauri build failed (exit $LASTEXITCODE)" }
            Write-Done "Merc Wizard.exe updated"
        } finally { Pop-Location }
    } else {
        Write-Skip "Tauri shell is current"
    }
}

if (-not (Test-Path -LiteralPath $exe)) {
    Fail "$exe is missing. Something is wrong with the build."
}

Write-Step "Launching Merc Wizard..."
# Use [System.IO.Path]::GetDirectoryName instead of Split-Path because
# PS 5.1's Split-Path can't resolve `-LiteralPath` + `-Parent` in the same
# parameter set, which gives an AmbiguousParameterSet error.
$exeDir = [System.IO.Path]::GetDirectoryName($exe)
Start-Process -FilePath $exe -WorkingDirectory $exeDir
# Brief pause so the user sees the launch line before the console closes.
Start-Sleep -Milliseconds 400
