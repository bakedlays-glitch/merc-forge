# build_sidecar.ps1 -- force-rebuild ONLY the Python sidecar binary.
#
# Run this after ANY change under sidecar/ (Python). A frontend rebuild
# (vite / `tauri build`) does NOT regenerate this PyInstaller EXE, so sidecar
# fixes won't appear in the app until you run this (or launch_current.ps1,
# which does it incrementally). See docs/BUILD_SIDECAR.md.
#
# Output is copied to the two places the shell looks for the sidecar:
#   shell\binaries\mercwizard_core-x86_64-pc-windows-msvc.exe  (Tauri externalBin -> bundled by `tauri build`)
#   shell\target\release\mercwizard_core.exe                   (dev-run copy next to the app .exe, if present)

$ErrorActionPreference = "Stop"
$root   = $PSScriptRoot
$venvPy = Join-Path $root "sidecar\.venv\Scripts\python.exe"
$pyDist = Join-Path $root "sidecar\dist\mercwizard_core.exe"
$binA   = Join-Path $root "shell\binaries\mercwizard_core-x86_64-pc-windows-msvc.exe"
$binB   = Join-Path $root "shell\target\release\mercwizard_core.exe"

if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Host "!!! sidecar venv python not found at $venvPy" -ForegroundColor Red
    Write-Host "    Create it:  cd sidecar; python -m venv .venv; .venv\Scripts\pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

# Stop any running instance so the target EXE isn't file-locked during the copy.
Get-Process -Name "Merc Wizard","mercwizard","mercwizard_core" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 600

Write-Host ""
Write-Host "==> Building sidecar (PyInstaller --onefile)..." -ForegroundColor Cyan
Push-Location (Join-Path $root "sidecar")
try {
    # Do NOT pipe `2>&1`: PyInstaller writes progress to stderr, which PS 5.1
    # wraps in NativeCommandError records that would abort under -ErrorAction Stop.
    # Let it stream to the console; we check $LASTEXITCODE for failures.
    & $venvPy -m PyInstaller mercwizard_core.spec --clean --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }
    if (-not (Test-Path -LiteralPath $pyDist)) { throw "PyInstaller reported success but $pyDist is missing" }
} finally { Pop-Location }

# Tauri externalBin location -- always needed for a full `tauri build`.
$binADir = Split-Path -Parent $binA
if (-not (Test-Path -LiteralPath $binADir)) { New-Item -ItemType Directory -Force -Path $binADir | Out-Null }
Copy-Item -LiteralPath $pyDist -Destination $binA -Force

# Dev-run copy -- only refresh if a shell release build already exists.
$binBDir = Split-Path -Parent $binB
if (Test-Path -LiteralPath $binBDir) { Copy-Item -LiteralPath $pyDist -Destination $binB -Force }

Write-Host ""
Write-Host "==> Sidecar rebuilt." -ForegroundColor Green
Write-Host "    -> $binA"
if (Test-Path -LiteralPath $binB) { Write-Host "    -> $binB" }
Write-Host ""
Write-Host "Now build/run the app to pick it up:  .\launch_current.ps1   (or a full ``tauri build``)" -ForegroundColor DarkGray
