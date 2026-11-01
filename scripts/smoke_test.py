#!/usr/bin/env python3
"""전 스택 수용 기준 검증.

docker compose up 으로 스택이 떠 있는 상태에서 호스트 또는 컨테이너 안에서 실행한다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

import httpx  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://spec:spec_dev_password@localhost:5432/spec"
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
FRONTEND_BASE_URL = os.environ.get(
    "FRONTEND_BASE_URL", "http://frontend:3000" if Path("/.dockerenv").exists() else "http://localhost:3000"
)

os.environ.setdefault("DATABASE_URL", DATABASE_URL)
os.environ.setdefault("REDIS_URL", REDIS_URL)

# CI 러너에는 Ollama 가 없다 (매 PR마다 qwen3:8b 5GB를 받는 것도 비현실적이다) —
# LLM 백엔드 판정만 완화할 수 있게 한다. 로컬(기본값)에서는 그대로 필수다.
REQUIRE_LLM_HEALTHY = os.environ.get("SMOKE_REQUIRE_LLM_HEALTHY", "true").lower() != "false"

_IN_CONTAINER = Path("/.dockerenv").exists()


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
        if _IN_CONTAINER:
            return CheckResult(name, "SKIP", "컨테이너 안에서는 docker CLI가 없어 생략")
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
    name = "GET /health 200 + DB/Redis/LLM 각각 보고"
    try:
        response = httpx.get(f"{API_BASE_URL}/health", timeout=10.0)
    except httpx.HTTPError as exc:
        return CheckResult(name, "FAIL", f"요청 실패: {exc}")

    if response.status_code != 200:
        return CheckResult(name, "FAIL", f"HTTP {response.status_code}")

    body = response.json()
    required_keys = ["database", "redis"]
    if REQUIRE_LLM_HEALTHY:
        required_keys.append("llm_backend")
    down = [k for k in required_keys if body.get(k) != "ok"]
    if down:
        return CheckResult(name, "FAIL", f"down: {down} (전체 응답: {body})")
    note = "" if REQUIRE_LLM_HEALTHY else " (llm_backend는 SMOKE_REQUIRE_LLM_HEALTHY=false라 판정에서 제외)"
    return CheckResult(name, "PASS", str(body) + note)


# ---------------------------------------------------------------------------
# 4. 스키마와 시점 규약
# ---------------------------------------------------------------------------


def check_hypertables() -> CheckResult:
    name = "하이퍼테이블 전환 (price_daily)"
    engine = _engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT hypertable_name FROM timescaledb_information.hypertables")
        ).all()
    engine.dispose()
    found = {r[0] for r in rows}
    expected = {"price_daily"}
    missing = expected - found
    if missing:
        return CheckResult(name, "FAIL", f"누락: {missing}")
    return CheckResult(name, "PASS", f"found={sorted(found)}")


def check_asof_boundary_tests() -> CheckResult:
    name = "as_of 경계 테스트 통과"
    python_exe = sys.executable
    proc = subprocess.run(
        [python_exe, "-m", "pytest", "tests/test_price_daily_repo.py", "-q"],
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
# 5. 프론트 /health (HTTP로만 판정 — 브라우저 자동화 없이)
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
    check_hypertables,
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
