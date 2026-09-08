# Windows PowerShell 편의 명령. Makefile 대신 사용한다.
#
# 사용법:
#   .\tasks.ps1 up                       # 전 스택 (cpu 프로파일 없이) 기동
#   .\tasks.ps1 up -Services postgres,redis,minio
#   .\tasks.ps1 up -Profile gpu          # vllm 포함 (GPU 팀원 전용)
#   .\tasks.ps1 down
#   .\tasks.ps1 migrate
#   .\tasks.ps1 seed                     # db/seeds/*.sql 적재 — 새 DB에서 한 번만
#   .\tasks.ps1 smoke
#   .\tasks.ps1 logs -Services api -Follow

param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet('up', 'down', 'migrate', 'seed', 'smoke', 'logs')]
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
        # alembic.ini / db/migrations는 repo 루트에 있고 api 컨테이너에는
        # backend/만 마운트돼 있어 `docker compose exec api alembic ...`는
        # "No 'script_location' key found"로 실패한다 — 호스트(백엔드 venv)에서
        # 돌리고 DATABASE_URL만 컨테이너 postgres의 공개 포트로 오버라이드한다.
        $env:DATABASE_URL = 'postgresql+psycopg://spec:spec_dev_password@localhost:5432/spec'
        & "$PSScriptRoot\backend\.venv\Scripts\alembic.exe" -c "$PSScriptRoot\alembic.ini" upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw "alembic upgrade head 실패 (exit $LASTEXITCODE)"
        }
    }
    'seed' {
        # db/seeds/*.sql은 docker-entrypoint-initdb.d(db/init/)와 달리 자동
        # 실행되지 않는다 — 여기서 명시적으로 psql에 stdin으로 흘려 넣는다.
        # 04_etf_master.csv는 뺀다: risk_tag/group_id가 비어 있는 종목이 대부분이라
        # (README "알려진 설계 구멍") 아직 DB에 적재할 수 있는 상태가 아니다.
        Get-ChildItem "$PSScriptRoot\db\seeds\*.sql" | Sort-Object Name | ForEach-Object {
            Write-Host "[tasks.ps1] seeding $($_.Name)" -ForegroundColor Cyan
            Get-Content $_.FullName -Raw | docker compose exec -T postgres psql -U spec -d spec -v ON_ERROR_STOP=1
            if ($LASTEXITCODE -ne 0) {
                throw "seed $($_.Name) 실패 (exit $LASTEXITCODE) — 이미 적재된 DB에 다시 돌리면 PK/UNIQUE 충돌이 정상이다"
            }
        }
    }
    'smoke' {
        # scripts/smoke_test.py는 컨테이너 안이 아니라 호스트(또는 CI 러너)에서
        # 돈다 — backend/만 api 컨테이너에 마운트돼 있어 리포지토리 루트의
        # scripts/를 컨테이너 안에서 못 찾기 때문이다. 전 서비스 포트가
        # localhost로 공개돼 있어야 한다 (.\tasks.ps1 up 으로 이미 그렇게 뜬다).
        & "$PSScriptRoot\backend\.venv\Scripts\python.exe" "$PSScriptRoot\scripts\smoke_test.py"
        if ($LASTEXITCODE -ne 0) {
            throw "smoke_test.py 실패 (exit $LASTEXITCODE)"
        }
    }
    'logs' {
        $logArgs = @('logs')
        if ($Follow) { $logArgs += '-f' }
        if ($Services) { $logArgs += $Services }
        Invoke-Compose -ComposeArgs $logArgs
    }
}
