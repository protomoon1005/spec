"""as_of 경계 테스트 공통 픽스처.

DATABASE_URL이 없으면 로컬 docker-compose postgres(localhost:5432)를 기본값으로
쓴다.
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://spec:spec_dev_password@localhost:5432/spec",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
# 워커 컨테이너 없이(docker compose worker 미기동) 단위 테스트를 돌리기 위해
# 기본적으로 eager 모드를 켠다 — .delay()가 브로커 없이 그 자리에서 동기 실행된다.
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")

import pytest
from sqlalchemy import text

from app.core.db import get_engine
from app.core.security import hash_password


@pytest.fixture(scope="session")
def engine():
    return get_engine()


@pytest.fixture
def test_password() -> str:
    return "correct horse battery staple"


@pytest.fixture
def make_user(engine, test_password):
    """(role) -> (email, user_id) 를 만드는 팩토리. 테스트 후 정리한다."""
    created_ids: list[int] = []

    def _make(role: str = "retail") -> tuple[str, int]:
        suffix = uuid.uuid4().hex[:8]
        email = f"auth-test-{suffix}@example.com"
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (email, password_hash, role) "
                    "VALUES (:email, :password_hash, :role) RETURNING user_id"
                ),
                {"email": email, "password_hash": hash_password(test_password), "role": role},
            ).scalar_one()
        created_ids.append(user_id)
        return email, user_id

    yield _make

    with engine.begin() as conn:
        for user_id in created_ids:
            conn.execute(text("DELETE FROM users WHERE user_id = :user_id"), {"user_id": user_id})


@pytest.fixture
def blocked_etfs(engine):
    """후보 선정이 막아야 하는 종목을 잠깐 넣어 준다 — 레버리지 하나, 상장폐지 하나.

    시드로 넣어 두지 않는 이유: 수집 스크립트가 일부러 걸러내는 종목이라
    종목 표에 상주할 이유가 없다. 막는 규칙이 동작하는지는 여기서만 확인하면 된다.

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
                    "INSERT INTO etf_master"
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
            text("DELETE FROM etf_master WHERE ticker = ANY(:tickers)"),
            {"tickers": [row["ticker"] for row in rows]},
        )


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def portfolio_id(engine):
    """view_weights.portfolio_id가 요구하는 FK 체인
    (users -> preset_versions -> hardcap_versions -> risk_profiles
     -> strategy_specs -> portfolios) 을 최소로 채우고 테스트 후 정리한다."""
    suffix = uuid.uuid4().hex[:8]
    preset_version = f"test-{suffix}"
    hardcap_version = f"test-{suffix}"
    spec_id = f"test-spec-{suffix}"

    with engine.begin() as conn:
        user_id = conn.execute(
            text(
                "INSERT INTO users (email, password_hash, role) "
                "VALUES (:email, 'x', 'retail') RETURNING user_id"
            ),
            {"email": f"asof-test-{suffix}@example.com"},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO preset_versions (preset_version, created_by, is_active) "
                "VALUES (:pv, :uid, false)"
            ),
            {"pv": preset_version, "uid": user_id},
        )
        conn.execute(
            text(
                "INSERT INTO hardcap_versions "
                "(hardcap_version, max_weight_per_asset, cash_min, max_loss_per_trade, "
                " max_drawdown, min_interval_days, leverage_allowed, created_by) "
                "VALUES (:hv, 1, 0, 1, 1, 1, false, :uid)"
            ),
            {"hv": hardcap_version, "uid": user_id},
        )
        profile_id = conn.execute(
            text(
                "INSERT INTO risk_profiles (user_id, risk_level, preset_version) "
                "VALUES (:uid, 1, :pv) RETURNING profile_id"
            ),
            {"uid": user_id, "pv": preset_version},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO strategy_specs "
                "(spec_id, user_id, profile_id, hardcap_version, spec_version, name, status) "
                "VALUES (:sid, :uid, :pid, :hv, 'v0.1', 'asof guard test', 'draft')"
            ),
            {"sid": spec_id, "uid": user_id, "pid": profile_id, "hv": hardcap_version},
        )
        pf_id = conn.execute(
            text(
                "INSERT INTO portfolios (spec_id, mode, status) "
                "VALUES (:sid, 'paper', 'active') RETURNING portfolio_id"
            ),
            {"sid": spec_id},
        ).scalar_one()

    yield pf_id

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM view_weights WHERE portfolio_id = :pid"), {"pid": pf_id})
        conn.execute(text("DELETE FROM portfolios WHERE portfolio_id = :pid"), {"pid": pf_id})
        conn.execute(text("DELETE FROM strategy_specs WHERE spec_id = :sid"), {"sid": spec_id})
        conn.execute(text("DELETE FROM risk_profiles WHERE profile_id = :pid"), {"pid": profile_id})
        conn.execute(
            text("DELETE FROM hardcap_versions WHERE hardcap_version = :hv"), {"hv": hardcap_version}
        )
        conn.execute(
            text("DELETE FROM preset_versions WHERE preset_version = :pv"), {"pv": preset_version}
        )
        conn.execute(text("DELETE FROM users WHERE user_id = :uid"), {"uid": user_id})
