param(
    [switch]$WithCPAchecker,
    [switch]$SkipBuild,
    [string]$BuildDns = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    $composeArgs = @('compose', '-f', 'compose.yaml')
    if ($BuildDns) {
        # A per-build override avoids altering Docker Desktop or Windows DNS.
        $resolver = 'import socket,json; hosts=["deb.debian.org","security.debian.org","pypi.org","files.pythonhosted.org","github.com","release-assets.githubusercontent.com","cpachecker.sosy-lab.org"]; print(json.dumps({h:socket.gethostbyname(h) for h in hosts}))'
        $hostsJson = & docker run --rm --dns $BuildDns python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534 python -c $resolver
        if ($LASTEXITCODE -ne 0) { throw 'Build DNS resolution failed' }
        $hostsMap = $hostsJson | ConvertFrom-Json -AsHashtable
        $services = @{}
        foreach ($service in @('runtime', 'cbmc', 'esbmc', 'cpachecker')) {
            $services[$service] = @{build = @{extra_hosts = $hostsMap}}
        }
        New-Item -ItemType Directory -Force .veriruntime | Out-Null
        @{services = $services} | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8NoBOM .veriruntime/build-network.json
        $composeArgs += @('-f', '.veriruntime/build-network.json')
    }
    if (-not $SkipBuild) {
        $targets = @('runtime', 'cbmc', 'esbmc')
        if ($WithCPAchecker) { $targets += 'cpachecker' }
        # Windows can fetch releases even when the Linux VM cannot reach GitHub.
        New-Item -ItemType Directory -Force docker/downloads | Out-Null
        $packages = (Get-Content docker/verifiers-linux.lock.json -Raw | ConvertFrom-Json).packages
        foreach ($package in $packages | Where-Object {$_.name -in $targets}) {
            $extension = if ($package.format -eq 'deb') { '.deb' } else { '.zip' }
            $archivePath = Join-Path 'docker/downloads' ($package.name + $extension)
            if (-not (Test-Path -LiteralPath $archivePath)) {
                if ($package.name -eq 'cpachecker') {
                    & "$PSScriptRoot/download-cpachecker.ps1" -Url $package.url -Size $package.size_bytes -Destination $archivePath
                } else {
                    & curl.exe -fsSL --retry 3 --connect-timeout 20 --max-time 600 -o ($archivePath + '.partial') $package.url
                    if ($LASTEXITCODE -ne 0) { throw "Download failed: $($package.name)" }
                    Move-Item -LiteralPath ($archivePath + '.partial') -Destination $archivePath
                }
            }
            if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $package.sha256) {
                throw "Checksum mismatch: $archivePath. Remove this archive and retry."
            }
        }
        & docker @composeArgs build @targets
        if ($LASTEXITCODE -ne 0) { throw 'Docker image build failed' }
    }
    & docker @composeArgs run --rm -e VRUN_BACKEND=native --entrypoint python runtime -m pytest -q -m 'not integration' --basetemp=/data/unit-tests -o cache_dir=/tmp/pytest-cache
    if ($LASTEXITCODE -ne 0) { throw 'Linux regression suite failed' }
    $acceptanceArgs = @('--export-dir', '/export')
    if ($WithCPAchecker) { $acceptanceArgs += '--with-cpachecker' }
    New-Item -ItemType Directory -Force .veriruntime/docker-acceptance | Out-Null
    $exportMount = "$projectRoot/.veriruntime/docker-acceptance:/export"
    & docker @composeArgs run --rm -v $exportMount --entrypoint python runtime scripts/docker_acceptance.py @acceptanceArgs
    if ($LASTEXITCODE -ne 0) { throw 'Real-verifier acceptance failed' }
    Write-Host 'Acceptance passed. Evidence: .veriruntime/docker-acceptance/acceptance.json'
} finally {
    Pop-Location
}
