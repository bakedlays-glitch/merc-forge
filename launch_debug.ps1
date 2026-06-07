# launch_debug.ps1 -- rebuild + relaunch MercForge with caches disabled.
#
# Sets MERCWIZARD_DEBUG=1 so the sidecar bypasses on-disk caches for the
# atlas bake (`_build_atlas`) and the palette sheet bake
# (`_build_palette_sheet`). Every load is treated as cold — what the user
# would pay on a fresh install with no cached fingerprints.
#
# Use during perf testing + before recording the YouTube demo's cold-path
# verification. For normal dev work, prefer `launch_current.ps1` (caches
# stay enabled, faster iteration).
#
# How it works:
#   1. Sets $env:MERCWIZARD_DEBUG = '1' (process env).
#   2. Calls launch_current.ps1 in the same shell, which inherits the env.
#   3. mercwizard.exe spawns mercwizard_core.exe via Tauri's sidecar
#      plugin; child processes inherit parent env, so the sidecar sees
#      MERCWIZARD_DEBUG=1.
#   4. routes/mapforge.py reads `os.environ.get("MERCWIZARD_DEBUG")` at
#      every cache check and skips the cache when set.
#
# Console invocation (from Bash via PowerShell), run from the repo root:
#   powershell -ExecutionPolicy Bypass -File .\launch_debug.ps1
#
# Or from a PowerShell prompt:
#   cd <your MercForge repo root> ; .\launch_debug.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host ""
Write-Host "==> DEBUG MODE: caches disabled (MERCWIZARD_DEBUG=1)" -ForegroundColor Yellow
Write-Host "    Atlas + palette sheet bakes will run from scratch every time." -ForegroundColor DarkYellow
Write-Host ""

$env:MERCWIZARD_DEBUG = "1"

# Re-use launch_current.ps1's incremental-build logic. PowerShell's
# child-script invocation runs in the same process scope, so $env vars
# set here are visible to launch_current.ps1 and any process it spawns.
& (Join-Path $root "launch_current.ps1")
