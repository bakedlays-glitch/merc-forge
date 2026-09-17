function Newer-Than-Artifact($paths, $artifact) {
    if (-not (Test-Path -LiteralPath $artifact)) { return $true }
    $artTime = (Get-Item -LiteralPath $artifact).LastWriteTimeUtc
    foreach ($p in $paths) {
        if (-not (Test-Path -LiteralPath $p)) { continue }
        $item = Get-Item -LiteralPath $p -ErrorAction SilentlyContinue
        if ($item -and -not $item.PSIsContainer) {
            if ($item.LastWriteTimeUtc -gt $artTime) { return $true }
            continue
        }
        $hit = Get-ChildItem -LiteralPath $p -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTimeUtc -gt $artTime } |
            Select-Object -First 1
        if ($hit) { return $true }
    }
    return $false
}


function Test-TauriRebuildNeeded($frontendSources, $frontendIndex, $shellSources, $exe) {
    return (Newer-Than-Artifact $frontendSources $frontendIndex) -or
           (Newer-Than-Artifact $shellSources $exe) -or
           (Newer-Than-Artifact @($frontendIndex) $exe)
}
