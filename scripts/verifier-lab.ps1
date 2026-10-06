param([switch]$SkipBuild, [string]$BuildDns = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    $composeArgs = @('compose','-f','compose.yaml','-f','compose.experiments.yaml')
    if ($BuildDns) {
        $resolver = 'import socket,json; hosts=["deb.debian.org","security.debian.org","pypi.org","files.pythonhosted.org"]; print(json.dumps({h:socket.gethostbyname(h) for h in hosts}))'
        $hostsJson = & docker run --rm --dns $BuildDns python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534 python -c $resolver
        if ($LASTEXITCODE -ne 0) { throw 'Build DNS resolution failed' }
        $hostsMap = $hostsJson | ConvertFrom-Json -AsHashtable
        $services = @{}
        foreach ($service in @('runtime','ultimate','framac','rocq')) {$services[$service] = @{build=@{extra_hosts=$hostsMap}}}
        New-Item -ItemType Directory -Force .veriruntime | Out-Null
        @{services=$services} | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8NoBOM .veriruntime/experimental-network.json
        $composeArgs += @('-f','.veriruntime/experimental-network.json')
    }
    if (-not $SkipBuild) {
        New-Item -ItemType Directory -Force docker/downloads | Out-Null
        foreach ($package in (Get-Content docker/experiments.lock.json -Raw | ConvertFrom-Json).packages) {
            $archivePath = Join-Path docker/downloads $package.file
            if (-not (Test-Path -LiteralPath $archivePath)) {
                if ($package.size_bytes) {
                    & "$PSScriptRoot/download-cpachecker.ps1" -Url $package.url -Size $package.size_bytes -Destination $archivePath
                } else {
                    & curl.exe -fsSL --retry 3 --max-time 600 -o ($archivePath+'.partial') $package.url
                    if ($LASTEXITCODE -ne 0) {throw "Download failed: $($package.name)"}
                    Move-Item -LiteralPath ($archivePath+'.partial') -Destination $archivePath
                }
            }
            if ((Get-FileHash -LiteralPath $archivePath).Hash.ToLowerInvariant() -ne $package.sha256) {throw "Checksum mismatch: $archivePath"}
        }
        & docker @composeArgs build runtime ultimate framac rocq
        if ($LASTEXITCODE -ne 0) {throw 'Experimental image build failed'}
    }
    & docker @composeArgs run --rm --entrypoint python runtime scripts/run_verifier_lab.py
    if ($LASTEXITCODE -ne 0) {throw 'Verifier experiments failed'}
    New-Item -ItemType Directory -Force .veriruntime/verifier-lab | Out-Null
    & docker @composeArgs run --rm -v "$projectRoot/.veriruntime/verifier-lab:/export" --entrypoint python runtime -c 'import shutil; shutil.copyfile("/data/verifier-lab/latest.json","/export/lab.json")'
    if ($LASTEXITCODE -ne 0) {throw 'Lab evidence export failed'}
    Write-Host 'Verifier lab passed. Evidence: .veriruntime/verifier-lab/lab.json'
} finally {Pop-Location}
