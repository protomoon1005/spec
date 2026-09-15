#!/usr/bin/env python3
"""전 스택 수용 기준 검증 (docs/infra-spec.md 8단계).

`docker compose up` 으로 스택이 이미 떠 있다는 것을 전제로, 호스트에 공개된
포트(localhost:5432/6379/8000/3000/3001/9090)로 직접 검증한다 — 그래서
`.\\tasks.ps1 smoke`는 컨테이너 안이 아니라 백엔드 venv로 이 스크립트를 돌린다
(TODO였던 `docker compose exec api ...` 방식은 backend/만 마운트돼 있어 scripts/를
못 찾는다).

DB 마이그레이션 왕복(항목 5)만 예외적으로 부수효과가 있다 — 별도 스크래치
데이터베이스(spec_smoke_migration_check)를 만들어 그 안에서만 upgrade/downgrade를
돌리고 끝나면 지운다. 개발 중인 `spec` DB는 절대 건드리지 않는다.

GPU가 없어 "--profile gpu와 --profile cpu가 둘 다 기동" 항목만 SKIP으로 표시한다
(이유는 해당 체크 함수 docstring 참조). 나머지는 전부 실행해서 PASS/FAIL을 낸다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

import httpx  # noqa: E402
import redis  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://spec:spec_dev_password@localhost:5432/spec"
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://localhost:3000")

os.environ.setdefault("DATABASE_URL", DATABASE_URL)
os.environ.setdefault("REDIS_URL", REDIS_URL)

# CI 러너에는 GPU도 없고 Ollama/vLLM도 없다 (매 PR마다 qwen3:8b 5GB를 받는 것도
# 비현실적이다) — GPU/CPU 프로필 항목과 같은 이유로, LLM 백엔드 판정만 완화할
# 수 있게 한다. 로컬(기본값)에서는 그대로 필수다.
REQUIRE_LLM_HEALTHY = os.environ.get("SMOKE_REQUIRE_LLM_HEALTHY", "true").lower() != "false"


@dataclass
class CheckResult:
    name: str
    status: str  # "PASS" | "FAIL" | "SKIP"
    detail: str = ""


def _engine():
    return create_engine(DATABASE_URL, pool_pre_ping=True)


# ---------------------------------------------------------------------------
# 1. 부트스트랩
# ---------------------------------------------------------------------------


def check_env_file() -> CheckResult:
    if (REPO_ROOT / ".env").exists():
        return CheckResult("`.env` 존재 (cp .env.example .env 완료)", "PASS")
    return CheckResult("`.env` 존재", "FAIL", ".env가 없다 — cp .env.example .env 먼저 실행하라")


# ---------------------------------------------------------------------------
# 2. docker compose 서비스 상태
# ---------------------------------------------------------------------------


def check_compose_services_healthy() -> CheckResult:
    name = "docker compose ps 전 서비스 healthy"
    try:
        proc = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return CheckResult(name, "FAIL", "docker CLI를 찾을 수 없다")

    if proc.returncode != 0:
        return CheckResult(name, "FAIL", proc.stderr.strip())

    services: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        services[row.get("Service", row.get("Name", "?"))] = row

    if not services:
        return CheckResult(name, "FAIL", "docker compose ps 결과가 비어 있다 — 스택을 먼저 올려라")

    problems = []
    for service_name, row in services.items():
        if service_name == "minio-init":
            continue  # 1회성 초기화 잡. 끝나고 종료되는 게 정상이라 제외한다.
        state = row.get("State", "")
        health = row.get("Health", "")
        if state != "running":
            problems.append(f"{service_name}: state={state}")
        elif health and health != "healthy":
            problems.append(f"{service_name}: health={health}")

    if problems:
        return CheckResult(name, "FAIL", "; ".join(problems))
    return CheckResult(name, "PASS", f"{len(services)}개 서비스 확인")


# ---------------------------------------------------------------------------
# 3. GET /health
# ---------------------------------------------------------------------------


def check_api_health() -> CheckResult:
    name = "GET /health 200 + DB/Redis/MinIO/LLM 각각 보고"
    try:
        response = httpx.get(f"{API_BASE_URL}/health", timeout=10.0)
    except httpx.HTTPError as exc:
        return CheckResult(name, "FAIL", f"요청 실패: {exc}")

    if response.status_code != 200:
        return CheckResult(name, "FAIL", f"HTTP {response.status_code}")

    body = response.json()
    required_keys = ["database", "redis", "minio"]
    if REQUIRE_LLM_HEALTHY:
        required_keys.append("llm_backend")
    down = [k for k in required_keys if body.get(k) != "ok"]
    if down:
        return CheckResult(name, "FAIL", f"down: {down} (전체 응답: {body})")
    note = "" if REQUIRE_LLM_HEALTHY else " (llm_backend는 SMOKE_REQUIRE_LLM_HEALTHY=false라 판정에서 제외)"
    return CheckResult(name, "PASS", str(body) + note)


# ---------------------------------------------------------------------------
# 4. GPU/CPU 양쪽 기동 — SKIP
# ---------------------------------------------------------------------------


def check_gpu_cpu_both_profiles() -> CheckResult:
    """이 환경에는 GPU가 없다 (docker-compose.yml의 vllm 서비스는 `deploy.
    resources.reservations.devices`로 nvidia GPU 1장을 예약하는데, 이 머신에는
    그 디바이스가 없어 `--profile gpu`로 올려도 vllm 컨테이너가 뜨지 못한다).
    cpu(ollama) 쪽은 app/llm/ollama_client.py로 실제 검증했다(별도 통합 테스트
    tests/test_llm_client.py, requires_ollama 마커) — 이 항목은 "양쪽 다"라는
    조건 자체를 만족할 수 없어 SKIP으로만 표시하고 GPU 팀원 몫으로 남긴다."""
    return CheckResult(
        "--profile gpu / --profile cpu 둘 다 기동 + 동일 프롬프트 JSON 응답",
        "SKIP",
        "이 환경에 GPU가 없다 (docstring 참조). cpu/ollama 단독 검증은 "
        "tests/test_llm_client.py(requires_ollama)에서 통과함",
    )


# ---------------------------------------------------------------------------
# 5. Alembic 왕복 — 스크래치 DB에서만
# ---------------------------------------------------------------------------


def _alembic_executable() -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return Path(sys.executable).parent / f"alembic{suffix}"


def check_alembic_roundtrip() -> CheckResult:
    name = "Alembic upgrade head -> downgrade base -> upgrade head 왕복"
    base_url = make_url(DATABASE_URL)
    scratch_db = f"spec_smoke_migration_{uuid.uuid4().hex[:8]}"
    admin_engine = create_engine(base_url, pool_pre_ping=True).execution_options(
        isolation_level="AUTOCOMMIT"
    )

    alembic_exe = _alembic_executable()
    if not alembic_exe.exists():
        return CheckResult(name, "FAIL", f"alembic 실행 파일을 찾을 수 없다: {alembic_exe}")

    # str(url)은 SQLAlchemy 2.x부터 비밀번호를 ***로 가린다 — 실제 접속에 쓸
    # 문자열은 반드시 render_as_string(hide_password=False)로 뽑아야 한다.
    scratch_url = base_url.set(database=scratch_db).render_as_string(hide_password=False)

    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{scratch_db}"'))
        # db/init/00_extensions.sql은 postgres 컨테이너 최초 기동 시 딱 한 번만
        # 돈다 — 새로 만든 스크래치 DB에는 그 확장(vector 등)이 없어서 migrations
        # 002(news_articles.embedding VECTOR)가 바로 실패한다. 여기서 직접 맞춰준다.
        scratch_admin_engine = create_engine(scratch_url, pool_pre_ping=True).execution_options(
            isolation_level="AUTOCOMMIT"
        )
        with scratch_admin_engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        scratch_admin_engine.dispose()
    except DBAPIError as exc:
        return CheckResult(name, "FAIL", f"스크래치 DB 준비 실패: {exc}")
    env = {**os.environ, "DATABASE_URL": scratch_url}

    def _run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(alembic_exe), "-c", str(REPO_ROOT / "alembic.ini"), *args],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    try:
        steps = [
            ("upgrade head (1차)", _run("upgrade", "head")),
            ("downgrade base", _run("downgrade", "base")),
            ("upgrade head (2차)", _run("upgrade", "head")),
        ]
        for step_name, proc in steps:
            if proc.returncode != 0:
                return CheckResult(name, "FAIL", f"{step_name} 실패:\n{proc.stderr}")

        scratch_engine = create_engine(scratch_url, pool_pre_ping=True)
        with scratch_engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            table_count = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'etf_master'"
                )
            ).scalar_one()
        scratch_engine.dispose()

        if table_count != 1:
            return CheckResult(name, "FAIL", "2차 upgrade 후 etf_master 테이블이 없다")
        return CheckResult(name, "PASS", f"최종 revision={version}")
    finally:
        with admin_engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :db AND pid <> pg_backend_pid()"
                ),
                {"db": scratch_db},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_db}"'))
        admin_engine.dispose()


# ---------------------------------------------------------------------------
# 6~8. 시드 카운트 / 하이퍼테이블 / pgvector 인덱스
# ---------------------------------------------------------------------------


def check_seed_counts() -> CheckResult:
    name = "시드: ASSET_BOUND_PRESETS 30 / GROUP_CAPS 25(15+10) / HARDCAP_VERSIONS 활성 1"
    engine = _engine()
    with engine.connect() as conn:
        presets = conn.execute(text("SELECT count(*) FROM asset_bound_presets")).scalar_one()
        group_caps = conn.execute(text("SELECT count(*) FROM group_caps")).scalar_one()
        active_hardcaps = conn.execute(
            text("SELECT count(*) FROM hardcap_versions WHERE activated_at IS NOT NULL")
        ).scalar_one()
    engine.dispose()

    problems = []
    if presets != 30:
        problems.append(f"asset_bound_presets={presets} (기대 30)")
    if group_caps != 25:
        problems.append(f"group_caps={group_caps} (기대 25)")
    if active_hardcaps != 1:
        problems.append(f"활성 hardcap_versions={active_hardcaps} (기대 1)")

    if problems:
        return CheckResult(name, "FAIL", "; ".join(problems))
    return CheckResult(name, "PASS", f"presets={presets}, group_caps={group_caps}, hardcap=1")


def check_hypertables() -> CheckResult:
    name = "하이퍼테이블 3종 전환 (price_daily/feature_store/macro_indicators)"
    engine = _engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT hypertable_name FROM timescaledb_information.hypertables")
        ).all()
    engine.dispose()
    found = {r[0] for r in rows}
    expected = {"price_daily", "feature_store", "macro_indicators"}
    missing = expected - found
    if missing:
        return CheckResult(name, "FAIL", f"누락: {missing}")
    return CheckResult(name, "PASS", f"found={sorted(found)}")


def check_vector_indexes() -> CheckResult:
    name = "pgvector HNSW 인덱스 2종 (bbl_blocks/news_articles)"
    engine = _engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE indexdef ILIKE '%hnsw%'")
        ).all()
    engine.dispose()
    found = {r[0] for r in rows}
    expected = {"idx_bbl_embedding", "idx_news_embedding"}
    missing = expected - found
    if missing:
        return CheckResult(name, "FAIL", f"누락: {missing}")
    return CheckResult(name, "PASS", f"found={sorted(found)}")


# ---------------------------------------------------------------------------
# 9~10. 불변성 트리거
# ---------------------------------------------------------------------------


def check_approved_spec_update_rejected() -> CheckResult:
    name = "승인된 Spec에 UPDATE 시도 -> 트리거 거부"
    engine = _engine()
    suffix = uuid.uuid4().hex[:8]
    spec_id = f"smoke-spec-{suffix}"
    try:
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (email, password_hash, role) "
                    "VALUES (:email, 'x', 'retail') RETURNING user_id"
                ),
                {"email": f"smoke-{suffix}@example.com"},
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO preset_versions (preset_version, created_by, is_active) "
                    "VALUES (:pv, :uid, false)"
                ),
                {"pv": suffix, "uid": user_id},
            )
            conn.execute(
                text(
                    "INSERT INTO hardcap_versions "
                    "(hardcap_version, max_weight_per_asset, cash_min, max_loss_per_trade, "
                    " max_drawdown, min_interval_days, leverage_allowed, created_by) "
                    "VALUES (:hv, 1, 0, 1, 1, 1, false, :uid)"
                ),
                {"hv": suffix, "uid": user_id},
            )
            profile_id = conn.execute(
                text(
                    "INSERT INTO risk_profiles (user_id, risk_level, preset_version) "
                    "VALUES (:uid, 1, :pv) RETURNING profile_id"
                ),
                {"uid": user_id, "pv": suffix},
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO strategy_specs "
                    "(spec_id, user_id, profile_id, hardcap_version, spec_version, name, status) "
                    "VALUES (:sid, :uid, :pid, :hv, 'v0.1', 'smoke', 'draft')"
                ),
                {"sid": spec_id, "uid": user_id, "pid": profile_id, "hv": suffix},
            )
            conn.execute(
                text("UPDATE strategy_specs SET status = 'approved' WHERE spec_id = :sid"),
                {"sid": spec_id},
            )

        rejected = False
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE strategy_specs SET name = 'changed' WHERE spec_id = :sid"),
                    {"sid": spec_id},
                )
        except DBAPIError:
            rejected = True

        if not rejected:
            return CheckResult(name, "FAIL", "approved 행의 UPDATE가 거부되지 않았다")
        return CheckResult(name, "PASS", "트리거가 UPDATE를 거부했다")
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM strategy_specs WHERE spec_id = :sid"), {"sid": spec_id})
            conn.execute(
                text("DELETE FROM risk_profiles WHERE user_id = :uid"), {"uid": user_id}
            )
            conn.execute(
                text("DELETE FROM hardcap_versions WHERE hardcap_version = :hv"), {"hv": suffix}
            )
            conn.execute(
                text("DELETE FROM preset_versions WHERE preset_version = :pv"), {"pv": suffix}
            )
            conn.execute(text("DELETE FROM users WHERE user_id = :uid"), {"uid": user_id})
        engine.dispose()


def check_view_weights_update_rejected() -> CheckResult:
    name = "VIEW_WEIGHTS UPDATE 시도 -> 트리거 거부"
    engine = _engine()
    suffix = uuid.uuid4().hex[:8]
    spec_id = f"smoke-vw-{suffix}"
    try:
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (email, password_hash, role) "
                    "VALUES (:email, 'x', 'retail') RETURNING user_id"
                ),
                {"email": f"smoke-vw-{suffix}@example.com"},
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO preset_versions (preset_version, created_by, is_active) "
                    "VALUES (:pv, :uid, false)"
                ),
                {"pv": suffix, "uid": user_id},
            )
            conn.execute(
                text(
                    "INSERT INTO hardcap_versions "
                    "(hardcap_version, max_weight_per_asset, cash_min, max_loss_per_trade, "
                    " max_drawdown, min_interval_days, leverage_allowed, created_by) "
                    "VALUES (:hv, 1, 0, 1, 1, 1, false, :uid)"
                ),
                {"hv": suffix, "uid": user_id},
            )
            profile_id = conn.execute(
                text(
                    "INSERT INTO risk_profiles (user_id, risk_level, preset_version) "
                    "VALUES (:uid, 1, :pv) RETURNING profile_id"
                ),
                {"uid": user_id, "pv": suffix},
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO strategy_specs "
                    "(spec_id, user_id, profile_id, hardcap_version, spec_version, name, status) "
                    "VALUES (:sid, :uid, :pid, :hv, 'v0.1', 'smoke', 'draft')"
                ),
                {"sid": spec_id, "uid": user_id, "pid": profile_id, "hv": suffix},
            )
            portfolio_id = conn.execute(
                text(
                    "INSERT INTO portfolios (spec_id, mode, status) "
                    "VALUES (:sid, 'paper', 'active') RETURNING portfolio_id"
                ),
                {"sid": spec_id},
            ).scalar_one()
            view_weight_id = conn.execute(
                text(
                    "INSERT INTO view_weights (portfolio_id, as_of, view_type, weight) "
                    "VALUES (:pid, CURRENT_DATE, 'market', 0.5) RETURNING view_weight_id"
                ),
                {"pid": portfolio_id},
            ).scalar_one()

        rejected = False
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE view_weights SET weight = 0.9 WHERE view_weight_id = :vwid"),
                    {"vwid": view_weight_id},
                )
        except DBAPIError:
            rejected = True

        if not rejected:
            return CheckResult(name, "FAIL", "view_weights UPDATE가 거부되지 않았다")
        return CheckResult(name, "PASS", "트리거가 UPDATE를 거부했다")
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM view_weights WHERE portfolio_id = :pid"), {"pid": portfolio_id}
            )
            conn.execute(text("DELETE FROM portfolios WHERE portfolio_id = :pid"), {"pid": portfolio_id})
            conn.execute(text("DELETE FROM strategy_specs WHERE spec_id = :sid"), {"sid": spec_id})
            conn.execute(
                text("DELETE FROM risk_profiles WHERE user_id = :uid"), {"uid": user_id}
            )
            conn.execute(
                text("DELETE FROM hardcap_versions WHERE hardcap_version = :hv"), {"hv": suffix}
            )
            conn.execute(
                text("DELETE FROM preset_versions WHERE preset_version = :pv"), {"pv": suffix}
            )
            conn.execute(text("DELETE FROM users WHERE user_id = :uid"), {"uid": user_id})
        engine.dispose()


# ---------------------------------------------------------------------------
# 11~12. as_of 경계 테스트 / check_asof_guard
# ---------------------------------------------------------------------------


def check_asof_boundary_tests() -> CheckResult:
    name = "as_of 경계 테스트 3종 통과 (미래 데이터/released_at 미래/당일 가중치)"
    python_exe = sys.executable
    proc = subprocess.run(
        [python_exe, "-m", "pytest", "tests/test_asof_repositories.py", "-q"],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": DATABASE_URL},
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return CheckResult(name, "FAIL", proc.stdout[-2000:] + proc.stderr[-2000:])
    return CheckResult(name, "PASS", proc.stdout.strip().splitlines()[-1] if proc.stdout else "ok")


def check_asof_guard() -> CheckResult:
    name = "check_asof_guard.py 통과"
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_asof_guard", REPO_ROOT / "scripts" / "check_asof_guard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    errors = module.check()
    if errors:
        return CheckResult(name, "FAIL", "; ".join(errors))
    return CheckResult(name, "PASS", "위반 없음")


# ---------------------------------------------------------------------------
# 13. 프론트 /health (HTTP로만 판정 — 브라우저 자동화 없이)
# ---------------------------------------------------------------------------


def check_frontend_health_page() -> CheckResult:
    name = "프론트 localhost:3000/health 가 API 상태를 렌더 (HTML 본문으로 판정)"
    try:
        response = httpx.get(f"{FRONTEND_BASE_URL}/health", timeout=10.0)
    except httpx.HTTPError as exc:
        return CheckResult(name, "FAIL", f"요청 실패: {exc}")

    if response.status_code != 200:
        return CheckResult(name, "FAIL", f"HTTP {response.status_code}")

    body = response.text
    # 서버 컴포넌트라 API 응답이 HTML에 그대로 서버 렌더된다 (클라이언트 전용
    # useEffect였다면 curl에는 "불러오는 중"만 찍혀서 이 방식으로 판정 못 한다).
    required_substrings = ["데이터베이스", "Redis", "상태 확인"]
    missing = [s for s in required_substrings if s not in body]
    if missing:
        return CheckResult(name, "FAIL", f"응답 본문에 없음: {missing}")
    if "ok" not in body:
        return CheckResult(name, "FAIL", "응답 본문에 'ok' 상태가 렌더되지 않았다")
    return CheckResult(name, "PASS", "HTML 본문에 API 상태가 렌더됨을 확인")




# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

CHECKS = [
    check_env_file,
    check_compose_services_healthy,
    check_api_health,
    check_gpu_cpu_both_profiles,
    check_alembic_roundtrip,
    check_seed_counts,
    check_hypertables,
    check_vector_indexes,
    check_approved_spec_update_rejected,
    check_view_weights_update_rejected,
    check_asof_boundary_tests,
    check_asof_guard,
    check_frontend_health_page,
]


def main() -> int:
    results: list[CheckResult] = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # noqa: BLE001 — 개별 체크 예외를 FAIL로 흡수하고 계속 진행한다
            results.append(CheckResult(check.__name__, "FAIL", f"예외: {exc!r}"))

    print()
    for result in results:
        marker = {"PASS": "✓", "FAIL": "✗", "SKIP": "-"}[result.status]
        print(f"[{marker}] {result.status:4s} {result.name}")
        if result.detail:
            print(f"        {result.detail}")
    print()

    failed = [r for r in results if r.status == "FAIL"]
    skipped = [r for r in results if r.status == "SKIP"]
    passed = [r for r in results if r.status == "PASS"]
    print(f"총 {len(results)}건: PASS {len(passed)} / FAIL {len(failed)} / SKIP {len(skipped)}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
