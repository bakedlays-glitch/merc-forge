[CmdletBinding()]
param(
    [switch]$AllowRemoteDivergence,
    [switch]$SkipTests,
    [string]$ManifestDirectory
)

$ErrorActionPreference = "Stop"
$rootDir = Split-Path -Parent $PSScriptRoot
$parentDir = Split-Path -Parent $rootDir
$version = ([IO.File]::ReadAllText((Join-Path $rootDir "VERSION"))).Trim()
$sourceCommit = (& git -C $rootDir rev-parse HEAD).Trim()
$failures = [System.Collections.Generic.List[string]]::new()
$checks = [System.Collections.Generic.List[object]]::new()

function Add-Check([string]$name, [bool]$passed, [string]$detail) {
    $checks.Add([ordered]@{ name = $name; passed = $passed; detail = $detail })
    if (-not $passed) { $failures.Add("${name}: $detail") }
}

function Invoke-NativeCheck([string]$name, [scriptblock]$command) {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $command 2>&1 | Out-String
        $code = $LASTEXITCODE
    } catch {
        $output = $_ | Out-String
        $code = 1
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    Add-Check $name ($code -eq 0) (($output.Trim()) -replace "`r?`n", " | ")
}

function New-Artifact([string]$name, [string]$path, [string]$kind) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Add-Check "artifact $name" $false "missing: $path"
        return $null
    }
    $item = Get-Item -LiteralPath $path
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    return [ordered]@{
        name = $name
        kind = $kind
        path = [IO.Path]::GetFullPath($path)
        sha256 = $hash
        bytes = [int64]$item.Length
        build_time_utc = $item.LastWriteTimeUtc.ToString("o")
        version = $version
        source_commit = $sourceCommit
    }
}

Push-Location $rootDir
try {
    $trackedStatus = (& git status --porcelain --untracked-files=no) -join "`n"
    Add-Check "clean tracked tree" ([string]::IsNullOrWhiteSpace($trackedStatus)) $(
        if ([string]::IsNullOrWhiteSpace($trackedStatus)) { "clean" } else { $trackedStatus }
    )

    Invoke-NativeCheck "version sync" {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $rootDir "tools\sync_version.ps1") -Check
    }

    if ($SkipTests) {
        $checks.Add([ordered]@{ name = "full tests"; passed = $null; detail = "skipped by explicit switch" })
    } else {
        Invoke-NativeCheck "full tests" {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $rootDir "run_tests.ps1")
        }
    }

    $corpus = Join-Path $rootDir "sidecar\mercwizard_core\mapforge\corpus\generator_corpus.json"
    $coverage = Join-Path $rootDir "sidecar\mercwizard_core\mapforge\corpus\coverage.json"
    $validator = Join-Path $parentDir "Headless_Compiler\map_corpus\validate_generator_corpus.py"
    $validatorPython = Join-Path $parentDir "Headless_Compiler\python\python.exe"
    if ((Test-Path -LiteralPath $validator) -and (Test-Path -LiteralPath $validatorPython)) {
        Invoke-NativeCheck "corpus validator" { & $validatorPython $validator $corpus $coverage }
    } else {
        Add-Check "corpus validator" $false "canonical validator or bundled Python missing"
    }

    . (Join-Path $rootDir "launch_checks.ps1")
    $sidecarSources = @(
        "sidecar\main.py", "sidecar\mercwizard_core", "sidecar\routes",
        "sidecar\ja2py", "sidecar\mercwizard_core.spec"
    ) | ForEach-Object { Join-Path $rootDir $_ }
    $frontendSources = @(
        "frontend\src", "frontend\package.json", "frontend\tsconfig.json", "frontend\vite.config.ts"
    ) | ForEach-Object { Join-Path $rootDir $_ }
    $shellSources = @(
        "shell\src", "shell\Cargo.toml", "shell\build.rs", "shell\tauri.conf.json"
    ) | ForEach-Object { Join-Path $rootDir $_ }

    $sidecarDist = Join-Path $rootDir "sidecar\dist\mercwizard_core.exe"
    $sidecarBundle = Join-Path $rootDir "shell\binaries\mercwizard_core-x86_64-pc-windows-msvc.exe"
    $sidecarRuntime = Join-Path $rootDir "shell\target\release\mercwizard_core.exe"
    $shellExe = Join-Path $rootDir "shell\target\release\mercwizard.exe"
    $frontendIndex = Join-Path $rootDir "frontend\dist\index.html"
    Add-Check "sidecar freshness" (-not (Newer-Than-Artifact $sidecarSources $sidecarDist)) "sidecar source/package data must not be newer than PyInstaller output"
    Add-Check "shell freshness" (-not (Test-TauriRebuildNeeded $frontendSources $frontendIndex $shellSources $shellExe)) "frontend dist and shell EXE must be newer than their inputs"

    # Use the pyi-archive-viewer implementation through the stable bundled
    # Python. The venv's generated .exe launcher embeds its original absolute
    # Python path and can break after a Python repair/reinstall.
    $archivePython = $validatorPython
    $pyiSitePackages = Join-Path $rootDir "sidecar\.venv\Lib\site-packages"
    $archiveReader = "import sys; sys.path.insert(0, sys.argv[1]); from PyInstaller.archive.readers import CArchiveReader; print('\n'.join(CArchiveReader(sys.argv[2]).toc))"
    if ((Test-Path -LiteralPath $archivePython) -and
        (Test-Path -LiteralPath $pyiSitePackages) -and
        (Test-Path -LiteralPath $sidecarDist)) {
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $archiveListing = (& $archivePython -c $archiveReader $pyiSitePackages $sidecarDist 2>&1 | Out-String)
            $archiveCode = $LASTEXITCODE
        } catch {
            $archiveListing = $_ | Out-String
            $archiveCode = 1
        } finally {
            $ErrorActionPreference = $previousPreference
        }
        $requiredCorpus = @("generator_corpus.json", "coverage.json")
        $missingCorpus = @($requiredCorpus | Where-Object { $archiveListing -notmatch [regex]::Escape($_) })
        Add-Check "sidecar archive contents" ($archiveCode -eq 0 -and $missingCorpus.Count -eq 0) $(
            if ($archiveCode -ne 0) { "pyi-archive_viewer exited $archiveCode" }
            elseif ($missingCorpus.Count -gt 0) { "missing: $($missingCorpus -join ', ')" }
            else { "generator_corpus.json and coverage.json embedded" }
        )
    } else {
        Add-Check "sidecar archive contents" $false "archive viewer or sidecar EXE missing"
    }

    $artifacts = [System.Collections.Generic.List[object]]::new()
    foreach ($artifact in @(
        (New-Artifact "sidecar-dist" $sidecarDist "sidecar"),
        (New-Artifact "sidecar-tauri-external" $sidecarBundle "sidecar"),
        (New-Artifact "sidecar-runtime" $sidecarRuntime "sidecar"),
        (New-Artifact "shell-release" $shellExe "shell")
    )) { if ($null -ne $artifact) { $artifacts.Add($artifact) } }

    $sidecarHashes = @($artifacts | Where-Object { $_.kind -eq "sidecar" } | ForEach-Object { $_.sha256 } | Select-Object -Unique)
    Add-Check "three sidecar hashes" ($sidecarHashes.Count -eq 1 -and (@($artifacts | Where-Object { $_.kind -eq "sidecar" })).Count -eq 3) ($sidecarHashes -join ", ")

    $installerDir = Join-Path $rootDir "shell\target\release\bundle\nsis"
    $installer = Get-ChildItem -LiteralPath $installerDir -Filter "Merc Forge_*_x64-setup.exe" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    if ($null -eq $installer) {
        Add-Check "installer" $false "missing NSIS installer"
    } else {
        $expectedInstallerName = "Merc Forge_${version}_x64-setup.exe"
        $metadataMatch = ([string]$installer.VersionInfo.FileVersion -eq $version) -and
                         ([string]$installer.VersionInfo.ProductVersion -eq $version)
        Add-Check "installer" ($installer.Name -eq $expectedInstallerName -and $metadataMatch) "$($installer.Name); FileVersion=$($installer.VersionInfo.FileVersion); ProductVersion=$($installer.VersionInfo.ProductVersion)"
        $installerArtifact = New-Artifact "nsis-installer" $installer.FullName "installer"
        if ($null -ne $installerArtifact) { $artifacts.Add($installerArtifact) }
    }

    $megapackDir = Join-Path $parentDir "MercForge_Megapack"
    foreach ($zipName in @("MercForge_Full.zip", "MercForge_Lite.zip", "MercForge_Faces.zip")) {
        $zipArtifact = New-Artifact $zipName (Join-Path $megapackDir $zipName) "megapack"
        if ($null -ne $zipArtifact) { $artifacts.Add($zipArtifact) }
    }

    $countsText = (& git rev-list --left-right --count origin/main...main).Trim()
    $countParts = $countsText -split '\s+'
    $behind = if ($countParts.Count -ge 1) { [int]$countParts[0] } else { -1 }
    $ahead = if ($countParts.Count -ge 2) { [int]$countParts[1] } else { -1 }
    $remoteClean = ($behind -eq 0 -and $ahead -eq 0)
    Add-Check "remote divergence" ($remoteClean -or $AllowRemoteDivergence) "origin/main...main behind=$behind ahead=$ahead; acknowledged=$([bool]$AllowRemoteDivergence)"

    if ($failures.Count -gt 0) {
        throw "Release verification failed:`n - $($failures -join "`n - ")"
    }

    if ([string]::IsNullOrWhiteSpace($ManifestDirectory)) {
        $ManifestDirectory = Join-Path $rootDir "release"
    }
    New-Item -ItemType Directory -Path $ManifestDirectory -Force | Out-Null
    $manifestPath = Join-Path $ManifestDirectory "integrity-manifest-${version}.json"
    $manifest = [ordered]@{
        schema_version = 1
        product = "Merc Forge"
        version = $version
        source_commit = $sourceCommit
        generated_at_utc = [DateTime]::UtcNow.ToString("o")
        remote = [ordered]@{
            url = (& git remote get-url origin).Trim()
            behind = $behind
            ahead = $ahead
            divergence_acknowledged = [bool]$AllowRemoteDivergence
        }
        checks = $checks
        artifacts = $artifacts
    }
    $json = $manifest | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($manifestPath, $json + "`n", [Text.UTF8Encoding]::new($false))
    $manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
    $signaturePath = "$manifestPath.sha256"
    [IO.File]::WriteAllText($signaturePath, "$manifestHash  $([IO.Path]::GetFileName($manifestPath))`n", [Text.UTF8Encoding]::new($false))
    Write-Host "Release verification passed for Merc Forge $version at $sourceCommit."
    Write-Host "Integrity manifest: $manifestPath"
    Write-Host "Detached SHA-256: $signaturePath"
} finally {
    Pop-Location
}
