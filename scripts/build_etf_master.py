#!/usr/bin/env python3
"""종목 원장 행을 data/listing.csv 에서 분류해 만든다 (기반 정리 3단계, docs/pivot-plan.md 결정 2).

국내 상장 보통주와 ETF 전체를 담는다. 파일로 쓰지 않는다 — scripts/load_etf_master.py 가 build_rows()
를 불러 바로 적재한다. 혼자 돌리면 분류 집계만 보여 준다.

## 재료

  data/listing.csv                 종목 목록 · 시장 · ETF 분류 · 보통주 업종
                                   (scripts/fetch_prices.py 가 만든다)
  db/seeds/industry_sector_map.csv 보통주 업종(KSIC) → 업종 묶음. 표에 없는 업종은 기타
  db/seeds/etf_curated.csv         예전 ETF 76종목을 사람이 분류한 값. 이 종목들은 규칙 대신 이 값을 쓴다

같은 재료면 같은 행이 나온다(종목코드 순).

## 분류 규칙

종목 원장의 세 축 — 자산군(주식 · 채권 · 원자재), 업종 묶음 6개 + 기타, 국가(한국 · 미국 · 기타).
전략서 만들기가 "채권 위주", "반도체" 같은 요청을 이 칸으로 고른다.

- 보통주: 주식 · 한국. 업종은 industry_sector_map.csv. 레버리지 아님
- ETF (etf_curated 에 없는 것)
  - 자산군: 목록 분류가 채권이면 채권, 원자재면 원자재. 분류가 "기타" 이면서 이름이 머니마켓 · CD/KOFR/SOFR
    금리 · 단기채 · 통안채이면 채권. 나머지(주식, 채권혼합, TDF, 리츠, 통화선물 등)는 주식 —
    자산군이 셋뿐이라 근사다
  - 국가: 이름에 미국 지수 · 미국 종목 표지가 있으면 미국, 해외를 가리키는 말이 있거나 분류가 해외 주식이면
    기타, 아니면 한국
  - 업종: 주식 ETF 만, 이름 키워드로. 걸리지 않으면 기타
  - 레버리지: 이름에 레버리지 · 인버스 · 2X · 3X
- 위험등급(risk_tag) · 변동성 칸은 비운다. 성향과 프리셋이 없어졌다(결정 3)
- 상장폐지 종목은 넣지 않는다(목록이 받는 날 상장 종목). 모두 active

사용:
    python /repo/scripts/build_etf_master.py      # 분류 집계 확인
"""
from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LISTING = ROOT / "data" / "listing.csv"
INDUSTRY_MAP = ROOT / "db" / "seeds" / "industry_sector_map.csv"
CURATED = ROOT / "db" / "seeds" / "etf_curated.csv"

# load_etf_master.py 가 읽는 칸 순서
COLUMNS = [
    "ticker", "name", "sector", "group_id", "risk_tag", "mdd_3y", "volatility_1y", "expense_ratio",
    "listed_date", "delisted_date", "is_leveraged", "active", "country", "kind",
]

# 업종 묶음 → load_etf_master.py 가 받는 한글 값 (db/seeds/03_asset_groups.sql 의 label)
SECTOR_LABEL = {
    "SECTOR_SEMICONDUCTOR": "반도체",
    "SECTOR_BATTERY": "2차전지",
    "SECTOR_BIOHEALTH": "바이오와헬스케어",
    "SECTOR_FINANCE": "금융",
    "SECTOR_INTERNET_PLATFORM": "인터넷과플랫폼",
    "SECTOR_CONSUMER": "소비재",
    "SECTOR_OTHER": "기타",
}

ETF_BOND_LIKE = re.compile(r"머니마켓|CD\d*년?금리|CD금리|KOFR|SOFR|금리액티브|단기채|통안채|단기통안")
ETF_LEVERAGED = re.compile(r"레버리지|인버스|2X|3X", re.IGNORECASE)
US = re.compile(
    r"미국|S&P|나스닥|NASDAQ|다우|필라델피아|테슬라|애플|엔비디아|팔란티어|빅테크|FANG|MSCI US", re.IGNORECASE
)
FOREIGN = re.compile(
    r"중국|차이나|일본|인도|베트남|유럽|글로벌|선진국|신흥국|대만|홍콩|항셍|니케이|독일|브라질|멕시코|인도네시아|월드|아시아",
)
# 순서가 우선순위다(반도체 2차전지 → … → 소비재). 둘 이상 걸리면 앞의 것
ETF_SECTOR_KEYWORDS = (
    ("SECTOR_SEMICONDUCTOR", re.compile(r"반도체|메모리|HBM")),
    ("SECTOR_BATTERY", re.compile(r"2차전지|이차전지|배터리")),
    ("SECTOR_BIOHEALTH", re.compile(r"바이오|헬스케어|제약|의료")),
    ("SECTOR_FINANCE", re.compile(r"은행|증권|보험|금융")),
    ("SECTOR_INTERNET_PLATFORM", re.compile(r"인터넷|플랫폼|소프트웨어")),
    ("SECTOR_CONSUMER", re.compile(r"소비재|필수소비|경기소비|화장품|음식료|K-뷰티|뷰티")),
)


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def classify_etf(row: dict) -> dict:
    name, category = row["name"], row["category"]
    if category == "채권" or (category == "기타" and ETF_BOND_LIKE.search(name)):
        asset = "BOND"
    elif category == "원자재":
        asset = "COMMODITY"
    else:
        asset = "EQUITY"

    if US.search(name):
        country = "미국"
    elif FOREIGN.search(name) or category == "해외 주식":
        country = "기타"
    else:
        country = "한국"

    sector = "SECTOR_OTHER"
    if asset == "EQUITY":
        for group, pattern in ETF_SECTOR_KEYWORDS:
            if pattern.search(name):
                sector = group
                break

    return {
        "sector": SECTOR_LABEL[sector],
        "group_id": asset,
        "country": country,
        "is_leveraged": "true" if ETF_LEVERAGED.search(name) else "false",
    }


def build_rows() -> list[dict]:
    listing = read_csv(LISTING)
    industry_map = {r["industry"]: r["sector_group_id"] for r in read_csv(INDUSTRY_MAP)}
    unknown_groups = sorted(set(industry_map.values()) - set(SECTOR_LABEL))
    if unknown_groups:
        raise SystemExit(f"[build_etf_master] industry_sector_map.csv 에 모르는 묶음: {unknown_groups}")
    curated = {r["ticker"]: r for r in read_csv(CURATED)}

    out: list[dict] = []
    used_curated = 0
    for row in sorted(listing, key=lambda r: r["ticker"]):
        base = {c: "" for c in COLUMNS}
        base.update(ticker=row["ticker"], name=row["name"], active="true", kind=row["kind"])
        if row["kind"] == "stock":
            sector = industry_map.get(row["industry"], "SECTOR_OTHER")
            base.update(
                sector=SECTOR_LABEL[sector], group_id="EQUITY", country="한국", is_leveraged="false",
                listed_date=row["listing_date"],
            )
        elif row["ticker"] in curated:
            c = curated[row["ticker"]]
            base.update(
                sector=c["sector"],
                group_id=c["group_id"],
                country=c["country"],
                is_leveraged=c["is_leveraged"],
            )
            used_curated += 1
        else:
            base.update(classify_etf(row))
        out.append(base)

    missing_curated = sorted(set(curated) - {r["ticker"] for r in listing})
    if missing_curated:
        print(
            f"[build_etf_master] 사람 분류 중 목록에 없는 종목 {len(missing_curated)}: {missing_curated}",
            file=sys.stderr,
        )
    print(f"[build_etf_master] {len(out):,}종목 분류 (사람 분류 {used_curated} 유지)")
    return out


def main() -> int:
    out = build_rows()
    kinds = Counter(r["kind"] for r in out)
    print(f"  보통주 {kinds['stock']:,} · ETF {kinds['etf']:,}")
    print("  자산군", dict(Counter(r["group_id"] for r in out)))
    print("  업종  ", dict(Counter(r["sector"] for r in out)))
    print("  국가  ", dict(Counter(r["country"] for r in out)))
    print("  레버리지", sum(r["is_leveraged"] == "true" for r in out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
