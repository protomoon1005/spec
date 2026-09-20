#!/usr/bin/env python3
"""db/seeds/04_etf_master.csv 를 etf_master 테이블에 적재한다.

db/migrate/001_etf_master_group_axes.sql 이후 etf_master는 자산군/섹터/국가
세 축을 asset_group_id/sector_group_id/country_group_id 컬럼으로 나눠 받는다
(README "알려진 설계 구멍" ① 국가 컬럼 부재 — 해소됨). CSV의 group_id 컬럼값
(EQUITY/BOND/COMMODITY)은 그대로 asset_group_id로 쓰고, sector/country 컬럼의
한글값은 아래 SECTOR_GROUP_MAP/COUNTRY_GROUP_MAP으로 asset_groups.group_id에
매핑한다. 매핑에 없는 값이나 asset_groups에 실재하지 않는 group_id가 나오면
적재를 중단한다 — 모르는 분류를 임의로 새 그룹으로 만들지 않는다.

기존 group_id 컬럼은 당분간 자산군용으로 남아 있어 계속 그대로 적재한다.

ticker 기준 UPSERT다(INSERT ... ON CONFLICT (ticker) DO UPDATE) — TRUNCATE를
쓰지 않는다. price_daily 등 etf_master를 참조하는 테이블에 데이터가 쌓인
뒤에는 TRUNCATE ... CASCADE가 그 데이터를 통째로 지운다(사용자 지시,
2026-09-20). CSV에 없는 기존 행은 지우지 않고 그대로 둔다 — 이 스크립트는
갱신만 하지 삭제는 하지 않는다.

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

# etf_master 테이블 컬럼 (asset_group_id/sector_group_id/country_group_id는
# CSV에 직접 없다 — group_id/sector/country를 아래 매핑으로 변환해 채운다)
DB_COLUMNS = [
    "ticker", "name", "sector", "group_id", "risk_tag",
    "mdd_3y", "volatility_1y", "expense_ratio",
    "listed_date", "delisted_date", "is_leveraged", "active",
    "asset_group_id", "sector_group_id", "country_group_id",
]

# CSV의 sector 한글값 -> asset_groups.group_id (db/seeds/03_asset_groups.sql 레벨2 섹터)
SECTOR_GROUP_MAP = {
    "반도체": "SECTOR_SEMICONDUCTOR",
    "2차전지": "SECTOR_BATTERY",
    "바이오와헬스케어": "SECTOR_BIOHEALTH",
    "소비재": "SECTOR_CONSUMER",
    "기타": "SECTOR_OTHER",
}

# CSV의 country 한글값 -> asset_groups.group_id (db/seeds/03_asset_groups.sql 레벨2 국가)
COUNTRY_GROUP_MAP = {
    "한국": "COUNTRY_KR",
    "미국": "COUNTRY_US",
    "기타": "COUNTRY_OTHER",
}


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


def resolve_group_axes(rows: list[dict], known_group_ids: set[str]) -> list[dict]:
    """각 행의 group_id(자산군)/sector/country를 세 축 group_id로 변환한다.

    매핑에 없는 sector/country 값이거나 매핑된 값이 asset_groups에 실재하지
    않으면 즉시 중단한다 — 모르는 분류를 임의로 새 그룹으로 만들지 않는다
    (사용자 지시: "없는 값이 있으면 만들지 말고 멈춰서 보고해라").
    """
    unknown_sector = []
    unknown_country = []
    unknown_group_id = []
    resolved = []
    for row in rows:
        sector = row["sector"].strip()
        country = row["country"].strip()
        asset_group_id = row["group_id"].strip()

        sector_group_id = SECTOR_GROUP_MAP.get(sector)
        if sector_group_id is None:
            unknown_sector.append((row["ticker"], sector))

        country_group_id = COUNTRY_GROUP_MAP.get(country)
        if country_group_id is None:
            unknown_country.append((row["ticker"], country))

        for group_id in (asset_group_id, sector_group_id, country_group_id):
            if group_id is not None and group_id not in known_group_ids:
                unknown_group_id.append((row["ticker"], group_id))

        resolved.append(
            {
                **row,
                "asset_group_id": asset_group_id,
                "sector_group_id": sector_group_id,
                "country_group_id": country_group_id,
            }
        )

    if unknown_sector or unknown_country or unknown_group_id:
        print("load_etf_master: 매핑할 수 없는 분류값 발견 — 적재를 중단한다.", file=sys.stderr)
        for ticker, sector in unknown_sector:
            print(f"  - {ticker}: sector={sector!r}는 SECTOR_GROUP_MAP에 없음", file=sys.stderr)
        for ticker, country in unknown_country:
            print(f"  - {ticker}: country={country!r}는 COUNTRY_GROUP_MAP에 없음", file=sys.stderr)
        for ticker, group_id in unknown_group_id:
            print(f"  - {ticker}: group_id={group_id!r}는 asset_groups에 없음", file=sys.stderr)
        raise SystemExit(1)

    return resolved


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
        with engine.connect() as conn:
            known_group_ids = {
                r[0] for r in conn.execute(text("SELECT group_id FROM asset_groups"))
            }
        loadable = resolve_group_axes(loadable, known_group_ids)
        print("load_etf_master: 자산군/섹터/국가 매핑 확인 완료")

        with engine.begin() as conn:
            for row in loadable:
                conn.execute(
                    text(
                        "INSERT INTO etf_master "
                        "(ticker, name, sector, group_id, risk_tag, mdd_3y, volatility_1y, "
                        " expense_ratio, listed_date, delisted_date, is_leveraged, active, "
                        " asset_group_id, sector_group_id, country_group_id) "
                        "VALUES "
                        "(:ticker, :name, :sector, :group_id, :risk_tag, :mdd_3y, :volatility_1y, "
                        " :expense_ratio, :listed_date, :delisted_date, :is_leveraged, :active, "
                        " :asset_group_id, :sector_group_id, :country_group_id) "
                        "ON CONFLICT (ticker) DO UPDATE SET "
                        " name = EXCLUDED.name, "
                        " sector = EXCLUDED.sector, "
                        " group_id = EXCLUDED.group_id, "
                        " risk_tag = EXCLUDED.risk_tag, "
                        " mdd_3y = EXCLUDED.mdd_3y, "
                        " volatility_1y = EXCLUDED.volatility_1y, "
                        " expense_ratio = EXCLUDED.expense_ratio, "
                        " listed_date = EXCLUDED.listed_date, "
                        " delisted_date = EXCLUDED.delisted_date, "
                        " is_leveraged = EXCLUDED.is_leveraged, "
                        " active = EXCLUDED.active, "
                        " asset_group_id = EXCLUDED.asset_group_id, "
                        " sector_group_id = EXCLUDED.sector_group_id, "
                        " country_group_id = EXCLUDED.country_group_id"
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
                        "asset_group_id": row["asset_group_id"],
                        "sector_group_id": row["sector_group_id"],
                        "country_group_id": row["country_group_id"],
                    },
                )
    finally:
        engine.dispose()

    print(f"load_etf_master: {len(loadable)}행 upsert 완료 (etf_master)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
