# JA2 Launcher — one-shot build + install into a modpack folder.
#
# Usage:
#   .\build.ps1                                  # build only, no install
#   .\build.ps1 -Install -ModpackFolder "C:\path"  # build + copy JA2Launcher.exe into the modpack folder
#   .\build.ps1 -Install -Dev -ModpackFolder "C:\path"  # skip release build, use the debug exe (faster)

[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Dev,
    # Target modpack folder for -Install. No default — pass your own install path.
    [string]$ModpackFolder = ""
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "[ja2-launcher] Working in $here"

# 1. Frontend deps
$frontendNodeModules = Join-Path $here "frontend\node_modules"
if (-not (Test-Path $frontendNodeModules)) {
    Write-Host "[ja2-launcher] First-time npm install for frontend..."
    npm --prefix "$here\frontend" install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
}

# 2. Copy icons from MercWizard2/shell/icons if not already in place
$shellIcons = Join-Path $here "shell\icons"
$mwIcons = Join-Path (Split-Path -Parent $here) "shell\icons"
if (-not (Test-Path $shellIcons) -and (Test-Path $mwIcons)) {
    Write-Host "[ja2-launcher] Copying icons from MercWizard2/shell/icons/"
    Copy-Item -Recurse $mwIcons $shellIcons
}

# 3. Build
if ($Dev) {
    Write-Host "[ja2-launcher] DEV build (debug, faster)"
    npm --prefix "$here\frontend" run tauri -- build --debug
} else {
    Write-Host "[ja2-launcher] RELEASE build"
    npm --prefix "$here\frontend" run tauri -- build
}
if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }

# 4. Find built exe (Tauri 2.x emits to shell/target/{release,debug}/)
$builtExe = if ($Dev) {
    Join-Path $here "shell\target\debug\ja2launcher.exe"
} else {
    Join-Path $here "shell\target\release\ja2launcher.exe"
}
if (-not (Test-Path $builtExe)) { throw "Built exe not found at: $builtExe" }
$sz = [math]::Round((Get-Item $builtExe).Length / 1MB, 1)
Write-Host "[ja2-launcher] Built: $builtExe ($sz MB)"

# 5. Install into modpack folder
if ($Install) {
    if ([string]::IsNullOrWhiteSpace($ModpackFolder)) {
        throw "Pass -ModpackFolder ""C:\path\to\your\modpack"" when using -Install"
    }
    if (-not (Test-Path $ModpackFolder)) {
        throw "Modpack folder not found: $ModpackFolder"
    }
    $dest = Join-Path $ModpackFolder "JA2Launcher.exe"
    Copy-Item -Force $builtExe $dest
    Write-Host "[ja2-launcher] Installed -> $dest"
} else {
    Write-Host "[ja2-launcher] Pass -Install -ModpackFolder ""C:\path"" to copy into your modpack folder"
}
