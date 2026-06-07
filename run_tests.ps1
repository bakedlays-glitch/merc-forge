# MercWizard 2 — single test entry point.
#
# Runs the full automated check chain. Failures bubble up via $LASTEXITCODE
# so CI / IDE runners can detect them.
#
# Currently runs:
#   1. Sidecar pytest (Python integration + business-logic tests)
#   2. Frontend TypeScript typecheck
#
# Vitest component tests + Playwright E2E are noted as follow-ups in the
# plan; they require additional dev-dep setup not yet wired up.

$ErrorActionPreference = "Stop"
$rootDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "==> Sidecar pytest" -ForegroundColor Cyan
Push-Location (Join-Path $rootDir "sidecar")
try {
    & ".\.venv\Scripts\python.exe" -m pytest --tb=short -q
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Sidecar tests FAILED" -ForegroundColor Red
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "==> Frontend typecheck" -ForegroundColor Cyan
Push-Location (Join-Path $rootDir "frontend")
try {
    & npm run typecheck
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Frontend typecheck FAILED" -ForegroundColor Red
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "All checks passed." -ForegroundColor Green
