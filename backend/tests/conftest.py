"""테스트 공통 픽스처.

테스트는 테스트 전용 DB(기본 spec_test)만 쓴다. 개발 DB 가 테스트 찌꺼기로 더러워지지 않게
하려는 것이다(docs/pivot-plan.md 3.2, 기반 정리 2단계).

- 접속 정보: TEST_DATABASE_URL 이 있으면 그것을, 없으면 DATABASE_URL(없으면 로컬
  docker-compose postgres)에서 DB 이름만 `<이름>_test` 로 바꾼 것을 쓴다
- 테스트 DB 는 빈 볼륨에서 postgres 가 처음 뜰 때 db/init/03_test_db.sh 가 같은 스키마로 만든다
- 테스트 DB 에 기준 시드(분류표 · 종목 원장 · 전략 요소 사전)가 없으면 세션 시작 때
  scripts/seed_all.py 로 한 번 넣는다. 가격(price_daily)은 넣지 않는다 — requires_backfill
  테스트를 돌리려면 테스트 DB 에 가격을 따로 적재한다(CLAUDE.md 명령 절)
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy.engine import make_url

_DEV_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://spec:spec_dev_password@localhost:5432/spec",
)


def _test_database_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        url = make_url(explicit)
    else:
        dev = make_url(_DEV_DATABASE_URL)
        url = dev.set(database=f"{dev.database}_test")
    if not (url.database or "").endswith("_test"):
        raise RuntimeError(f"테스트 DB 이름은 _test 로 끝나야 한다: {url.database}")
    return url.render_as_string(hide_password=False)


# setdefault 가 아니라 강제로 넣는다. api 컨테이너는 .env 에서 개발 DB 주소를 받아 오므로
# setdefault 면 테스트가 개발 DB 에 쓴다. app 을 import 하기 전(아래)에 둬야 get_engine() 이 이 값을 읽는다.
os.environ["DATABASE_URL"] = _test_database_url()
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
# 워커 컨테이너 없이(docker compose worker 미기동) 단위 테스트를 돌리기 위해
# eager 모드를 켠다 — .delay()가 브로커 없이 그 자리에서 동기 실행된다.
# setdefault 가 아니라 강제로 넣는다. api 컨테이너는 .env 에서 false 를 받아 오므로
# setdefault 면 무시되고, 테스트가 떠 있는 워커(옛 코드일 수 있다)로 새어 나간다.
# app 을 import 하기 전(아래)에 둬야 get_settings()·celery_app 이 이 값을 읽는다.
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"

# 위의 환경변수를 app import 전에 넣어야 해서 import 가 뒤에 온다.
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import get_engine  # noqa: E402


def _seed_script() -> Path | None:
    # 호스트에서는 저장소 루트의 scripts/, api 컨테이너에서는 /repo/scripts/ 에 있다.
    for root in (Path(__file__).resolve().parents[2], Path("/repo")):
        script = root / "scripts" / "seed_all.py"
        if script.exists():
            return script
    return None


@pytest.fixture(scope="session", autouse=True)
def _seeded_test_database():
    """테스트 DB 에 기준 시드가 없으면 한 번 넣는다.

    DB 에 붙지 못하면 아무것도 하지 않는다 — DB 가 필요 없는 순수 함수 테스트는 그대로 돈다.
    """
    try:
        with get_engine().connect() as conn:
            empty = conn.execute(text("SELECT NOT EXISTS (SELECT 1 FROM asset_groups)")).scalar()
    except Exception:  # noqa: BLE001 — DB 없이 도는 테스트를 막지 않는다
        return
    if not empty:
        return
    script = _seed_script()
    if script is None:
        pytest.exit("scripts/seed_all.py 를 찾지 못해 테스트 DB 에 시드를 넣을 수 없다", returncode=1)
    result = subprocess.run([sys.executable, str(script)], env=os.environ.copy())  # noqa: S603
    if result.returncode != 0:
        pytest.exit("테스트 DB 시드 적재 실패 (scripts/seed_all.py)", returncode=1)


@pytest.fixture(scope="session")
def engine():
    return get_engine()


@pytest.fixture
def make_user(engine):
    """() -> (email, user_id) 를 만드는 팩토리. 테스트 후 정리한다."""
    created_ids: list[int] = []

    def _make() -> tuple[str, int]:
        suffix = uuid.uuid4().hex[:8]
        email = f"auth-test-{suffix}@example.com"
        with engine.begin() as conn:
            user_id = conn.execute(
                text("INSERT INTO users (email, password_hash) VALUES (:email, '') RETURNING user_id"),
                {"email": email},
            ).scalar_one()
        created_ids.append(user_id)
        return email, user_id

    yield _make

    with engine.begin() as conn:
        for user_id in created_ids:
            conn.execute(text("DELETE FROM users WHERE user_id = :user_id"), {"user_id": user_id})


@pytest.fixture
def make_draft_spec(engine, make_user):
    """사용자 하나와 그 사람의 draft 전략서를 만드는 팩토리.

    (universe, *, rebalance, name, signal_rules, constraint) -> strategy_id. universe 는
    (ticker, weight_min, weight_max) 목록이고 요청 범위(raw)도 같은 값으로 넣는다.
    사용자 이메일은 make_draft_spec.email 에 있다. 전략서 · 검증 기록 · 실행 기록을 뒤에서 지운다.
    """
    from app.repositories import specs

    email, user_id = make_user()

    def _make(universe, *, rebalance, name, signal_rules, constraint) -> str:
        return specs.insert_spec(
            strategy_id=specs.new_strategy_id(), user_id=user_id, strategy_version="0.1", name=name,
            input_prompt=None, rebalance=rebalance, signal_rules=signal_rules, constraint=constraint,
            universe=[
                {"ticker": t, "weight_min": lo, "weight_max": hi,
                 "weight_min_raw": lo, "weight_max_raw": hi, "was_adjusted": False}
                for t, lo, hi in universe
            ],
        ).strategy_id

    _make.email = email
    yield _make

    with engine.begin() as conn:
        mine = "(SELECT strategy_id FROM strategies WHERE user_id = :u)"
        runs = f"(SELECT run_id FROM backtest_runs WHERE strategy_id IN {mine})"
        conn.execute(text(f"DELETE FROM backtest_metrics WHERE run_id IN {runs}"), {"u": user_id})
        for table in ("backtest_runs", "validation_logs", "strategy_assets"):
            conn.execute(text(f"DELETE FROM {table} WHERE strategy_id IN {mine}"), {"u": user_id})
        conn.execute(text("DELETE FROM strategies WHERE user_id = :u"), {"u": user_id})


@pytest.fixture
def blocked_etfs(engine):
    """후보 선정 규칙을 확인할 종목을 잠깐 넣어 준다 — 레버리지 하나, 상장폐지 하나.

    상장폐지 종목은 막혀야 하고, 레버리지 종목은 지목하면 담겨야 한다(시스템 상한이 없다).
    시드로 넣어 두지 않는 이유: 테스트 데이터는 테스트 안에 두는 편이 원장이 바뀌어도 안 깨진다.

    {"leveraged": 종목코드, "delisted": 종목코드} 를 돌려준다.
    """
    suffix = uuid.uuid4().hex[:4].upper()
    rows = [
        {
            "ticker": f"LV-{suffix}",
            "name": f"테스트 레버리지 {suffix}",
            "risk_tag": "G1",
            "is_leveraged": True,
            "active": True,
        },
        {
            "ticker": f"DL-{suffix}",
            "name": f"테스트 상장폐지 {suffix}",
            "risk_tag": "G3",
            "is_leveraged": False,
            "active": False,
        },
    ]
    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                text(
                    "INSERT INTO instruments"
                    " (ticker, name, sector, group_id, risk_tag, is_leveraged, active)"
                    " VALUES (:ticker, :name, 'SECTOR_OTHER', 'EQUITY',"
                    " :risk_tag, :is_leveraged, :active)"
                ),
                row,
            )

    yield {"leveraged": rows[0]["ticker"], "delisted": rows[1]["ticker"],
           "leveraged_name": rows[0]["name"], "delisted_name": rows[1]["name"]}

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM instruments WHERE ticker = ANY(:tickers)"),
            {"tickers": [row["ticker"] for row in rows]},
        )


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
