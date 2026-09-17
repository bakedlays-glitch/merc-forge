$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$verifierPath = Join-Path $repoRoot "tools\verify_release.ps1"
$verifier = Get-Content -LiteralPath $verifierPath -Raw

$requiredSignals = @(
    "git status --porcelain",
    "sync_version.ps1",
    "run_tests.ps1",
    "validate_generator_corpus.py",
    "pyi-archive_viewer",
    "generator_corpus.json",
    "coverage.json",
    "Get-FileHash",
    "Test-TauriRebuildNeeded",
    "Merc Forge_",
    "rev-list",
    "AllowRemoteDivergence",
    "integrity-manifest"
)

foreach ($signal in $requiredSignals) {
    if (-not $verifier.Contains($signal)) {
        throw "verify_release.ps1 is missing required release signal: $signal"
    }
}

Write-Host "PASS: release verifier structurally covers every Task 8 gate."
