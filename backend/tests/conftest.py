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
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
