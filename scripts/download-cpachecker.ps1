param([Parameter(Mandatory)][string]$Url, [Parameter(Mandatory)][long]$Size, [Parameter(Mandatory)][string]$Destination)
$ErrorActionPreference = 'Stop'
# This release server can throttle a long single transfer. Bounded byte ranges
# avoid an extra downloader dependency; the caller still verifies the whole SHA-256.
$destinationPath = [IO.Path]::GetFullPath($Destination)
$partsPath = $destinationPath + '.parts'
New-Item -ItemType Directory -Force $partsPath | Out-Null
$chunkSize = 5MB
$jobs = @()
$partFiles = @()
try {
    for ($offset = 0L; $offset -lt $Size; $offset += $chunkSize) {
        $end = [Math]::Min($Size - 1, $offset + $chunkSize - 1)
        $partPath = Join-Path $partsPath ([string]$offset)
        $partFiles += $partPath
        $jobs += Start-ThreadJob -ThrottleLimit 8 -ArgumentList $Url,$offset,$end,$partPath -ScriptBlock {
            param($url,$start,$end,$path)
            if ((Test-Path -LiteralPath $path) -and (Get-Item -LiteralPath $path).Length -eq ($end-$start+1)) { return }
            & curl.exe -fsSL --retry 3 --connect-timeout 20 --max-time 180 --range "$start-$end" -o $path $url
            if ($LASTEXITCODE -ne 0 -or (Get-Item -LiteralPath $path).Length -ne ($end-$start+1)) { throw "Failed release range $start-$end" }
        }
    }
    $jobs | Receive-Job -Wait -ErrorAction Stop
    if ($jobs.State -contains 'Failed') { throw 'Release range download failed' }
    $output = [IO.File]::Create($destinationPath + '.partial')
    try {
        foreach ($partPath in $partFiles) {
            $partStream = [IO.File]::OpenRead($partPath)
            try { $partStream.CopyTo($output) } finally { $partStream.Dispose() }
        }
    } finally { $output.Dispose() }
    Move-Item -LiteralPath ($destinationPath + '.partial') -Destination $destinationPath -Force
    foreach ($partPath in $partFiles) { Remove-Item -LiteralPath $partPath }
    Remove-Item -LiteralPath $partsPath
} finally {
    $jobs | Stop-Job -ErrorAction SilentlyContinue
    $jobs | Remove-Job -Force -ErrorAction SilentlyContinue
}
