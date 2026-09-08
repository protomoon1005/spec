"""as_of 경계 테스트 공통 픽스처.

DATABASE_URL이 없으면 로컬 docker-compose postgres(localhost:5432)를 기본값으로
쓴다 — db/migrations/env.py 상단 주석의 호스트 실행 예시와 동일한 접속 정보다.
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://spec:spec_dev_password@localhost:5432/spec",
)

import pytest
from sqlalchemy import text

from app.core.db import get_engine


@pytest.fixture(scope="session")
def engine():
    return get_engine()


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
