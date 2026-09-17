$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$runner = Get-Content -LiteralPath (Join-Path $repoRoot "run_tests.ps1") -Raw

foreach ($required in @(
    "tools\sync_version.ps1",
    "-Check",
    "--basetemp",
    ".pytest_run_",
    "& npm.cmd run typecheck",
    "& npm.cmd run test"
)) {
    if (-not $runner.Contains($required)) {
        throw "run_tests.ps1 is missing required check: $required"
    }
}

if ($runner.Contains("Vitest component tests + Playwright E2E are noted as follow-ups")) {
    throw "run_tests.ps1 still claims the already-installed Vitest suite is not wired."
}

Write-Host "PASS: the single test entry point checks version sync, typecheck, and Vitest."
