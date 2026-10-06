#requires -Version 7.0
# PowerShell quickstart: two real runs in a fresh isolated store.
param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$task = 'examples/tasks/safe_assert_crosscheck.json'
$dataDir = '/data/quickstart-' + [guid]::NewGuid().ToString('N')
Push-Location $projectRoot
try {
    $profileJson = & docker compose run --rm runtime doctor --json
    if ($LASTEXITCODE -ne 0) { throw '工具检查失败；确认 Docker Desktop 正在使用 Linux engine。' }
    $profiles = ($profileJson -join "`n") | ConvertFrom-Json
    foreach ($tool in @('cbmc', 'esbmc')) {
        if (-not ($profiles | Where-Object {$_.name -eq $tool -and $_.available})) {
            throw "缺少 $tool 镜像；先运行 pwsh -File scripts/docker-demo.ps1 -BuildDns 1.1.1.1"
        }
    }
    & docker compose run --rm runtime validate $task
    if ($LASTEXITCODE -ne 0) { throw '示例 DSL 校验失败。' }
    $firstJson = & docker compose run --rm runtime verify $task --data-dir $dataDir --json
    if ($LASTEXITCODE -ne 0) { throw '首次验证调用失败。' }
    $first = ($firstJson -join "`n") | ConvertFrom-Json
    $goal = $first.goals[0]
    $result = $goal.report.result
    if ($result.verdict -ne 'SAFE' -or -not $result.requirement_satisfied -or
            $result.confirmations -lt 2 -or $result.cache_hit -or $goal.report.attempts.Count -lt 2) {
        throw "首次验证未达到要求：$($result | ConvertTo-Json -Depth 8 -Compress)"
    }
    Write-Host '运行时生成的物理计划：'
    Write-Host ($goal.optimization.physical_plan | ConvertTo-Json -Depth 15)
    Write-Host "首次：SAFE，确认家族 $($result.confirmations)，CACHE MISS，验证执行 $($goal.report.attempts.Count) 次"
    $repeatJson = & docker compose run --rm runtime verify $task --data-dir $dataDir --json
    if ($LASTEXITCODE -ne 0) { throw '重复验证调用失败。' }
    $repeat = ($repeatJson -join "`n") | ConvertFrom-Json
    $again = $repeat.goals[0]
    if ($again.report.result.verdict -ne 'SAFE' -or -not $again.report.result.requirement_satisfied -or
            -not $again.report.result.cache_hit -or $again.report.attempts.Count -ne 0 -or
            $again.report.result.source_execution_id -ne $result.execution_id) {
        throw '重复请求未通过缓存与来源校验。'
    }
    New-Item -ItemType Directory -Force .veriruntime/quickstart | Out-Null
    @{task=$task; data_dir=$dataDir; first=$first; repeat=$repeat} |
        ConvertTo-Json -Depth 100 | Set-Content -Encoding utf8NoBOM .veriruntime/quickstart/latest.json
    Write-Host '再次：SAFE，CACHE HIT，验证执行 0 次'
    Write-Host "证据目录：$dataDir"
    Write-Host '本机记录：.veriruntime/quickstart/latest.json'
    Write-Host "查看来源：docker compose run --rm runtime show $($result.execution_id) --data-dir $dataDir --json"
} finally { Pop-Location }
