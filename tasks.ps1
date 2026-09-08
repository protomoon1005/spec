# Windows PowerShell 편의 명령. Makefile 대신 사용한다.
#
# 사용법:
#   .\tasks.ps1 up                       # 전 스택 (cpu 프로파일 없이) 기동
#   .\tasks.ps1 up -Services postgres,redis,minio
#   .\tasks.ps1 up -Profile gpu          # vllm 포함 (GPU 팀원 전용)
#   .\tasks.ps1 down
#   .\tasks.ps1 migrate                  # TODO(3단계 Alembic 작성 후 동작)
#   .\tasks.ps1 smoke                    # TODO(10단계 smoke_test.py 작성 후 동작)
#   .\tasks.ps1 logs -Services api -Follow

param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet('up', 'down', 'migrate', 'smoke', 'logs')]
    [string]$Command,

    [ValidateSet('gpu', 'cpu')]
    [string]$Profile,

    [string[]]$Services,

    [switch]$Follow
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path '.env')) {
    Write-Host "[tasks.ps1] .env 가 없다. .env.example을 복사한다." -ForegroundColor Yellow
    Copy-Item '.env.example' '.env'
}

function Invoke-Compose {
    param([string[]]$ComposeArgs)

    $prefixArgs = @()
    if ($Profile) { $prefixArgs += @('--profile', $Profile) }

    $fullArgs = $prefixArgs + $ComposeArgs
    Write-Host "[tasks.ps1] docker compose $($fullArgs -join ' ')" -ForegroundColor Cyan
    & docker compose @fullArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($fullArgs -join ' ') 실패 (exit $LASTEXITCODE)"
    }
}

switch ($Command) {
    'up' {
        $upArgs = @('up', '-d')
        if ($Services) { $upArgs += $Services }
        Invoke-Compose -ComposeArgs $upArgs
        Invoke-Compose -ComposeArgs @('ps')
    }
    'down' {
        Invoke-Compose -ComposeArgs @('down')
    }
    'migrate' {
        # TODO(3단계): db/migrations 작성 후 동작. 지금은 api 컨테이너/alembic이 없어 실패한다.
        Invoke-Compose -ComposeArgs @('exec', 'api', 'alembic', 'upgrade', 'head')
    }
    'smoke' {
        # TODO(10단계): scripts/smoke_test.py 작성 후 동작.
        Invoke-Compose -ComposeArgs @('exec', 'api', 'python', 'scripts/smoke_test.py')
    }
    'logs' {
        $logArgs = @('logs')
        if ($Follow) { $logArgs += '-f' }
        if ($Services) { $logArgs += $Services }
        Invoke-Compose -ComposeArgs $logArgs
    }
}
