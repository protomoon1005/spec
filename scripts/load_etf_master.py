#!/usr/bin/env python3
"""db/seeds/04_etf_master.csv 를 etf_master 테이블에 적재한다.

CSV에는 country 컬럼이 있지만 etf_master 스키마에는 없다(README "알려진 설계
구멍" ③ — country를 정식 컬럼으로 승격하려면 마이그레이션이 필요하고 이번
범위 밖이다). 스키마를 임의로 바꾸지 않기 위해 이 스크립트는 country를 읽지도
적재하지도 않는다 — CSV에만 남긴다.

group_id가 비어 있는 행(risk_tag/group_id/sector를 사람이 아직 검토하지 않은
행)은 FK NOT NULL을 만족 못 하므로 건너뛴다 — 지금은 71행 전부 채워져 있어
해당 없지만, 앞으로 CSV에 신규 종목이 그룹 미배정 상태로 추가될 경우를 대비한
안전장치다(README "group_id를 FK NOT NULL로 둔 결과" 항목 참고).

G1 <-> is_leveraged 일치 검사: 상세설계서 1.7.2가 "레버리지·인버스 ETF는
안정투자형~위험중립형에게 생성 자체로 불가능"이라고 못박은 근거가 허용범위
프리셋의 G1 등급(허용 상한이 가장 낮은 등급)이다. CSV의 is_leveraged는 사람이
검토한 risk_tag와 별개로(종목명의 레버리지/인버스/2X/3X 키워드로) 채워진
값이라, 이 스크립트는 매 실행마다 두 값이 정확히 같은 집합을 가리키는지
재검사한다 — 둘 중 하나가 나중에 어긋나게 바뀌면(예: 새 종목 추가 시 실수로
risk_tag만 바꾸고 is_leveraged를 안 바꾸는 경우) 조용히 잘못된 데이터가
들어가는 대신 여기서 막는다.
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = REPO_ROOT / "db" / "seeds" / "04_etf_master.csv"

# etf_master 테이블 컬럼만 (country 제외 — 스키마에 없다)
DB_COLUMNS = [
    "ticker", "name", "sector", "group_id", "risk_tag",
    "mdd_3y", "volatility_1y", "expense_ratio",
    "listed_date", "delisted_date", "is_leveraged", "active",
]


def _database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://spec:spec_dev_password@localhost:5432/spec"
    )


def _to_bool(value: str) -> bool:
    return value.strip().lower() == "true"


def _to_null(value: str) -> str | None:
    value = value.strip()
    return value if value else None


def load_rows(csv_path: Path) -> list[dict]:
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def check_g1_leveraged_consistency(rows: list[dict]) -> None:
    """risk_tag='G1' 집합과 is_leveraged=true 집합이 정확히 일치하는지 검사한다.

    어긋나면 적재하지 않고 즉시 중단한다 — 사용자 지시(2026-09-16): "G1 <->
    is_leveraged 일치 검사를 넣어 나중에 어긋나면 잡히게 해라".
    """
    mismatches = []
    for row in rows:
        is_g1 = row["risk_tag"].strip() == "G1"
        is_lev = _to_bool(row["is_leveraged"])
        if is_g1 != is_lev:
            mismatches.append((row["ticker"], row["name"], row["risk_tag"], row["is_leveraged"]))

    if mismatches:
        print(
            "load_etf_master: risk_tag='G1'과 is_leveraged 불일치 발견 — 적재를 중단한다.",
            file=sys.stderr,
        )
        for ticker, name, risk_tag, is_lev in mismatches:
            print(f"  - {ticker} {name}: risk_tag={risk_tag!r} is_leveraged={is_lev!r}", file=sys.stderr)
        raise SystemExit(1)


def main() -> int:
    import argparse

    from sqlalchemy import create_engine, text

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args()

    rows = load_rows(args.csv)
    print(f"load_etf_master: {args.csv}에서 {len(rows)}행 읽음")

    check_g1_leveraged_consistency(rows)
    print("load_etf_master: G1 <-> is_leveraged 일치 확인 완료")

    skipped = [r["ticker"] for r in rows if not r["group_id"].strip()]
    if skipped:
        print(
            f"load_etf_master: group_id가 비어 있는 {len(skipped)}건은 건너뛴다: {skipped}",
            file=sys.stderr,
        )
    loadable = [r for r in rows if r["group_id"].strip()]

    engine = create_engine(_database_url(), pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            for row in loadable:
                conn.execute(
                    text(
                        "INSERT INTO etf_master "
                        "(ticker, name, sector, group_id, risk_tag, mdd_3y, volatility_1y, "
                        " expense_ratio, listed_date, delisted_date, is_leveraged, active) "
                        "VALUES "
                        "(:ticker, :name, :sector, :group_id, :risk_tag, :mdd_3y, :volatility_1y, "
                        " :expense_ratio, :listed_date, :delisted_date, :is_leveraged, :active)"
                    ),
                    {
                        "ticker": row["ticker"],
                        "name": row["name"],
                        "sector": _to_null(row["sector"]),
                        "group_id": row["group_id"],
                        "risk_tag": _to_null(row["risk_tag"]),
                        "mdd_3y": _to_null(row["mdd_3y"]),
                        "volatility_1y": _to_null(row["volatility_1y"]),
                        "expense_ratio": _to_null(row["expense_ratio"]),
                        "listed_date": _to_null(row["listed_date"]),
                        "delisted_date": _to_null(row["delisted_date"]),
                        "is_leveraged": _to_bool(row["is_leveraged"]),
                        "active": _to_bool(row["active"]),
                    },
                )
    finally:
        engine.dispose()

    print(f"load_etf_master: {len(loadable)}행 적재 완료 (etf_master)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
