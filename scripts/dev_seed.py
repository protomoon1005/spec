#!/usr/bin/env python3
"""개발용 테스트 데이터 시딩 — 팀 로컬 개발 편의 스크립트 (infra-spec.md 범위 밖).

역할별 테스트 계정 3개(retail/pro/admin)를 만들고, retail 계정에 위험중립형
(risk_level=3) risk_profiles 행을 하나 붙인다. 전부 멱등이다 — 이미 있으면
건너뛴다.

ENVIRONMENT=production에서는 실행을 거부한다 — 테스트 계정(비밀번호가 전부
`test1234`로 고정된)이 프로덕션 DB에 생기면 안 되기 때문이다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import Connection  # noqa: E402

from app.core.security import hash_password  # noqa: E402

TEST_PASSWORD = "test1234"
TEST_USERS: tuple[tuple[str, str], ...] = (
    ("retail@test.local", "retail"),
    ("pro@test.local", "pro"),
    ("admin@test.local", "admin"),
)
RETAIL_RISK_LEVEL = 3  # 위험중립형 (docs/infra-spec.md 4.1)


def _database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://spec:spec_dev_password@localhost:5432/spec"
    )


def _guard_not_production() -> None:
    environment = os.environ.get("ENVIRONMENT", "development").strip().lower()
    if environment == "production":
        print(
            "dev_seed.py: ENVIRONMENT=production 에서는 실행하지 않는다 "
            "(고정 비밀번호 test1234짜리 테스트 계정을 프로덕션에 만들지 않기 위한 가드다)",
            file=sys.stderr,
        )
        sys.exit(1)


def _upsert_test_user(conn: Connection, email: str, role: str) -> int:
    existing = conn.execute(
        text("SELECT user_id FROM users WHERE email = :email"), {"email": email}
    ).scalar_one_or_none()
    if existing is not None:
        print(f"  users: {email} 이미 있음 (user_id={existing}) — 건너뜀")
        return existing

    user_id = conn.execute(
        text(
            "INSERT INTO users (email, password_hash, role) "
            "VALUES (:email, :password_hash, :role) RETURNING user_id"
        ),
        {"email": email, "password_hash": hash_password(TEST_PASSWORD), "role": role},
    ).scalar_one()
    print(f"  users: {email} 생성 (user_id={user_id}, role={role})")
    return user_id


def _ensure_retail_risk_profile(conn: Connection, retail_user_id: int) -> None:
    existing = conn.execute(
        text("SELECT profile_id FROM risk_profiles WHERE user_id = :uid"),
        {"uid": retail_user_id},
    ).scalar_one_or_none()
    if existing is not None:
        print(f"  risk_profiles: user_id={retail_user_id} 이미 있음 (profile_id={existing}) — 건너뜀")
        return

    active_preset_version = conn.execute(
        text("SELECT preset_version FROM preset_versions WHERE is_active = true")
    ).scalar_one_or_none()
    if active_preset_version is None:
        raise RuntimeError(
            "preset_versions에 활성(is_active=true) 버전이 없다 — "
            "db/seeds/02_preset_v0_1.sql을 먼저 적재하라 "
            "(docker compose exec -T postgres psql -U spec -d spec < db/seeds/02_preset_v0_1.sql)"
        )

    defaults = conn.execute(
        text(
            "SELECT cash_min_default, max_drawdown_default, max_loss_per_trade_default "
            "FROM risk_profile_defaults "
            "WHERE preset_version = :pv AND risk_level = :risk_level"
        ),
        {"pv": active_preset_version, "risk_level": RETAIL_RISK_LEVEL},
    ).one_or_none()
    if defaults is None:
        raise RuntimeError(
            f"risk_profile_defaults에 (preset_version={active_preset_version!r}, "
            f"risk_level={RETAIL_RISK_LEVEL}) 행이 없다 — "
            "db/seeds/02_preset_v0_1.sql을 먼저 적재하라 "
            "(docker compose exec -T postgres psql -U spec -d spec < db/seeds/02_preset_v0_1.sql)"
        )

    profile_id = conn.execute(
        text(
            "INSERT INTO risk_profiles "
            "(user_id, risk_level, preset_version, cash_min_default, "
            " max_drawdown_default, max_loss_per_trade_default) "
            "VALUES (:uid, :risk_level, :pv, :cash_min, :max_dd, :max_loss) "
            "RETURNING profile_id"
        ),
        {
            "uid": retail_user_id,
            "risk_level": RETAIL_RISK_LEVEL,
            "pv": active_preset_version,
            "cash_min": defaults.cash_min_default,
            "max_dd": defaults.max_drawdown_default,
            "max_loss": defaults.max_loss_per_trade_default,
        },
    ).scalar_one()
    print(
        f"  risk_profiles: user_id={retail_user_id} 생성 "
        f"(profile_id={profile_id}, risk_level={RETAIL_RISK_LEVEL}, "
        f"preset_version={active_preset_version!r})"
    )


def main() -> int:
    _guard_not_production()

    engine = create_engine(_database_url(), pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            print("테스트 사용자 (비밀번호 전부 test1234):")
            user_ids = {email: _upsert_test_user(conn, email, role) for email, role in TEST_USERS}

            print("retail 위험 성향 프로필:")
            _ensure_retail_risk_profile(conn, user_ids["retail@test.local"])
    finally:
        engine.dispose()

    print("dev_seed.py 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
