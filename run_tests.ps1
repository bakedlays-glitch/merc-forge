# MercWizard 2 — single test entry point.
#
# Runs the full automated check chain. Failures bubble up via $LASTEXITCODE
# so CI / IDE runners can detect them.
#
# Currently runs:
#   1. Product-version synchronization
#   2. Sidecar pytest (Python integration + business-logic tests)
#   3. Frontend TypeScript typecheck
#   4. Frontend Vitest unit/component tests
#
# Browser-driven Playwright E2E remains a separate live-app verification.

$ErrorActionPreference = "Stop"
$rootDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "==> Product version sync" -ForegroundColor Cyan
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $rootDir "tools\sync_version.ps1") -Check
if ($LASTEXITCODE -ne 0) {
    Write-Host "Product version sync FAILED" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "==> Sidecar pytest" -ForegroundColor Cyan
Push-Location (Join-Path $rootDir "sidecar")
$pytestBaseTemp = Join-Path $rootDir ("sidecar\.pytest_run_" + [guid]::NewGuid().ToString("N"))
try {
    & ".\.venv\Scripts\python.exe" -m pytest --tb=short -q --basetemp $pytestBaseTemp
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Sidecar tests FAILED" -ForegroundColor Red
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
    $sidecarRoot = [IO.Path]::GetFullPath((Join-Path $rootDir "sidecar")).TrimEnd('\') + '\'
    $resolvedBaseTemp = [IO.Path]::GetFullPath($pytestBaseTemp)
    if ($resolvedBaseTemp.StartsWith($sidecarRoot, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedBaseTemp).StartsWith(".pytest_run_")) {
        Remove-Item -LiteralPath $resolvedBaseTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "==> Frontend typecheck" -ForegroundColor Cyan
Push-Location (Join-Path $rootDir "frontend")
try {
    & npm.cmd run typecheck
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Frontend typecheck FAILED" -ForegroundColor Red
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "==> Frontend Vitest" -ForegroundColor Cyan
Push-Location (Join-Path $rootDir "frontend")
try {
    & npm.cmd run test
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Frontend Vitest FAILED" -ForegroundColor Red
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "All checks passed." -ForegroundColor Green
