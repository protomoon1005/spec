#!/usr/bin/env python3
"""시드를 정해진 순서로 한 번에 적재한다.

## 왜 필요한가

시드가 두 가지 형식이다. 성격이 달라서 일부러 그렇게 뒀다.

  .sql  규칙표 — 팀이 회의에서 정한 값. 손으로 쓰고 잘 안 바뀐다
        (분류표)
  .csv  데이터 — 종목 원장. 사람이 스프레드시트로 분류 작업을 한다
        한글 분류값(`반도체`)을 묶음 이름(`SECTOR_SEMICONDUCTOR`)으로 옮기는
        매핑이 필요해서 전용 적재기를 쓴다 (scripts/load_etf_master.py)

형식을 한쪽으로 통일하면 둘 중 하나가 나빠진다. 그래서 **형식이 아니라 넣는
방법을 통일한다.** 쓰는 사람은 이 스크립트 하나만 알면 된다.

## 순서가 중요하다

종목(instruments)이 분류표(asset_groups)를 참조한다. 아래 순서를 바꾸면
외래키 때문에 적재가 실패한다. 그래서 디렉터리를 훑지 않고 **목록을 명시**한다 —
db/seeds/ 에는 적재 대상이 아닌 파일(예: 상장폐지 후보 목록)도 들어 있다.

## 멱등하지 않은 것이 섞여 있다

03 은 같은 값을 두 번 넣으면 기본키 충돌로 실패한다(ON CONFLICT 가 없다).
**새 데이터베이스에 한 번 돌리는 것이 전제다.** 종목 원장은 여러 번 돌려도 괜찮다.
갱신한다.

사용:
    python scripts/seed_all.py
    DATABASE_URL=... python scripts/seed_all.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEEDS_DIR = REPO_ROOT / "db" / "seeds"

# (파일명, 설명). 순서가 곧 적재 순서다.
SQL_SEEDS: tuple[tuple[str, str], ...] = (
    ("03_asset_groups.sql", "자산군·업종·국가 묶음(분류표)"),
)

# 종목 원장. 전용 적재기가 한글 분류값을 묶음 이름으로 옮긴다.
ETF_LOADER = REPO_ROOT / "scripts" / "load_etf_master.py"


def _database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://spec:spec_dev_password@localhost:5432/spec"
    )


def _run_sql(engine, name: str, description: str) -> None:
    """SQL 파일 하나를 통째로 실행한다.

    **드라이버에 파라미터를 넘기지 않는다.** 넘기는 순간 psycopg 가 본문을 훑어
    자리표시자를 찾는데, 시드 문구에 있는 `%` 와 `:` 가 거기 걸린다.
      - `허용범위 10%` 의 `%` 뒤 한글을 자리표시자로 읽다가 디코딩이 깨진다
      - `kw:배당` 의 콜론을 SQLAlchemy text() 가 바인드 이름으로 읽는다
    파라미터가 없으면 두 해석이 모두 일어나지 않는다.
    """
    path = SEEDS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"{path} 가 없다")
    sql = path.read_text(encoding="utf-8")

    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute(sql)
        raw.commit()
    finally:
        raw.close()
    print(f"seed_all: {name} — {description}")


def _run_etf_master() -> None:
    if not ETF_LOADER.exists():
        raise FileNotFoundError(f"{ETF_LOADER} 가 없다")
    result = subprocess.run(  # noqa: S603 — 저장소 안의 고정 경로다
        [sys.executable, str(ETF_LOADER)],
        cwd=str(REPO_ROOT),
        env={**os.environ, "DATABASE_URL": _database_url()},
    )
    if result.returncode != 0:
        raise RuntimeError("load_etf_master.py 가 실패했다")
    print("seed_all: 04_etf_master.csv — 종목 원장(instruments)")


def main() -> int:
    from sqlalchemy import create_engine

    engine = create_engine(_database_url(), pool_pre_ping=True)
    try:
        for name, description in SQL_SEEDS:
            _run_sql(engine, name, description)
        _run_etf_master()
    except Exception as exc:  # noqa: BLE001 — 어디서 멈췄는지만 알려 주면 된다
        message = str(exc)
        print(f"seed_all: 실패 — {message}", file=sys.stderr)
        if "duplicate key" in message or "already exists" in message:
            print(
                "seed_all: 이미 적재된 데이터베이스로 보인다. 03 은 두 번 넣을 수 없다 —"
                " 새로 깔려면 볼륨을 지우고 다시 띄울 것",
                file=sys.stderr,
            )
        else:
            print(
                "seed_all: 표가 있는지 먼저 확인할 것 (db/init/ 은 빈 볼륨에서만 돈다)",
                file=sys.stderr,
            )
        return 1
    finally:
        engine.dispose()

    print("seed_all: 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
