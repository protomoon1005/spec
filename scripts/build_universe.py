"""국내 ETF 유니버스와 일별 시세를 받아 data/ 아래에 캐시한다.

pykrx 를 쓰지 않는 이유: 2026년 현재 pykrx 는 KRX 로그인(KRX_ID / KRX_PW
환경변수)을 요구해서 계정 없이는 한 줄도 못 받는다. FinanceDataReader 는
인증 없이 동작하는 것을 확인했다(2026-09-11 실측).

산출물
  data/universe.csv    선별된 종목과 분류 (risk_tag 산정 근거 포함)
  data/prices.csv      long format 일별 종가 (ticker, date, close)
  data/meta.json       수집 시점·파라미터. 재현용

실행:  python scripts/build_universe.py   (출력: frontend/data/)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import FinanceDataReader as fdr
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "data"
OUT.mkdir(exist_ok=True)

AS_OF = date(2026, 9, 11)
YEARS = 3
START = AS_OF - timedelta(days=365 * YEARS + 30)  # 여유를 두고 받아 뒤에서 자른다
TARGET_COUNT = 200
CANDIDATE_COUNT = 320  # 3년 미달로 탈락하는 종목이 있어 넉넉히 잡는다
MIN_COVERAGE = 0.95  # 거래일 대비 데이터 보유 비율 하한

# --- 제외 규칙 ---------------------------------------------------------------
# 레버리지·인버스는 유니버스에서 제외한다(팀 결정). 이름만으로 거르되 패턴을
# 넓게 잡는다 — 2X/3X, 곱, 인버스, 선물매도 계열을 모두 포함한다.
EXCLUDE_PATTERNS = [
    r"레버리지", r"인버스", r"２Ｘ", r"2X", r"3X", r"-1X", r"양매도",
    r"곱", r"숏", r"short", r"bear", r"ultra",
]
EXCLUDE_RE = re.compile("|".join(EXCLUDE_PATTERNS), re.IGNORECASE)

# --- 분류 규칙 ---------------------------------------------------------------
# group_id 는 현서님 db/seeds/03_asset_groups.sql 의 식별자를 그대로 쓴다.
SECTOR_RULES = [
    ("SECTOR_SEMICONDUCTOR", [r"반도체", r"시스템반도체", r"AI반도체", r"semicon"]),
    ("SECTOR_BATTERY", [r"2차전지", r"이차전지", r"배터리", r"battery", r"전기차"]),
    ("SECTOR_BIOHEALTH", [r"바이오", r"헬스케어", r"제약", r"의료", r"health", r"bio"]),
    ("SECTOR_FINANCE", [r"은행", r"금융", r"증권", r"보험", r"bank", r"financ"]),
    ("SECTOR_INTERNET_PLATFORM", [r"인터넷", r"플랫폼", r"게임", r"소프트웨어", r"IT", r"테크", r"tech"]),
    ("SECTOR_CONSUMER", [r"소비재", r"화장품", r"음식료", r"유통", r"консум", r"consumer"]),
]

BOND_RE = re.compile(r"채권|국고채|회사채|통안|크레딧|단기채|금리|bond|만기매칭|CD금리|KOFR|머니마켓|MMF", re.IGNORECASE)
COMMODITY_RE = re.compile(r"금현물|골드|은선물|원유|WTI|구리|농산물|commodity|gold|silver|oil", re.IGNORECASE)
US_RE = re.compile(r"미국|S&P|나스닥|다우|russell|필라델피아|미국채", re.IGNORECASE)
KR_RE = re.compile(r"코스피|KOSPI|코스닥|KOSDAQ|200|한국|국고채|K-", re.IGNORECASE)


def classify(name: str) -> tuple[str, str, str]:
    """(asset_group, sector_group, country_group) 을 종목명에서 정한다."""
    if BOND_RE.search(name):
        asset = "BOND"
    elif COMMODITY_RE.search(name):
        asset = "COMMODITY"
    else:
        asset = "EQUITY"

    sector = "SECTOR_OTHER"
    if asset == "EQUITY":
        for sid, pats in SECTOR_RULES:
            if any(re.search(p, name, re.IGNORECASE) for p in pats):
                sector = sid
                break

    if US_RE.search(name):
        country = "COUNTRY_US"
    elif COMMODITY_RE.search(name):
        country = "COUNTRY_OTHER"
    elif KR_RE.search(name) or asset == "BOND":
        country = "COUNTRY_KR"
    else:
        country = "COUNTRY_OTHER"

    return asset, sector, country


# --- 위험등급 ----------------------------------------------------------------
# 공시된 산정 규칙: "설정 3년 경과 펀드는 최근 3년간 일간수익률을 토대로 위험등급을
# 분류한다". 여기서는 연환산 변동성 구간으로 G1~G6 을 매긴다.
#
# 경계값은 팀이 정한 프로젝트 기준이지 금융투자협회 공시 수치가 아니다.
# 실제 공시값과 대조해 고칠 수 있도록 risk_tag_method 로 근거를 남긴다.
VOL_BANDS = [
    (0.25, "G1"),  # 연변동성 25% 초과
    (0.20, "G2"),
    (0.15, "G3"),
    (0.10, "G4"),
    (0.05, "G5"),
    (0.00, "G6"),
]


def risk_tag_from_vol(ann_vol: float) -> str:
    for threshold, tag in VOL_BANDS:
        if ann_vol > threshold:
            return tag
    return "G6"


def main() -> int:
    print(f"[1/4] ETF 목록 조회 (기준 {AS_OF})")
    listing = fdr.StockListing("ETF/KR")
    print(f"      전체 {len(listing)}종목")

    listing = listing.dropna(subset=["Symbol", "Name"])
    before = len(listing)
    listing = listing[~listing["Name"].str.contains(EXCLUDE_RE, na=False)]
    print(f"      레버리지·인버스 제외: {before} -> {len(listing)}")

    listing = listing.sort_values("MarCap", ascending=False).head(CANDIDATE_COUNT)
    print(f"      순자산 상위 {len(listing)}종목을 후보로 삼음")

    print(f"[2/4] 일별 시세 수집 ({START} ~ {AS_OF})")
    frames: list[pd.DataFrame] = []
    kept: list[dict] = []
    for i, row in enumerate(listing.itertuples(index=False), 1):
        ticker, name = row.Symbol, row.Name
        try:
            df = fdr.DataReader(ticker, START.isoformat(), AS_OF.isoformat())
        except Exception as exc:  # 개별 종목 실패가 전체를 멈추게 하지 않는다
            print(f"      [{i:3}/{len(listing)}] {ticker} {name[:20]} 실패: {type(exc).__name__}")
            continue

        if df is None or df.empty or "Close" not in df:
            continue
        df = df[df.index.date <= AS_OF]
        if df.empty:
            continue

        # 3년 전체 구간을 덮는 종목만 남긴다. 신규 상장은 백테스트가 불가능하다.
        first = df.index[0].date()
        if first > START + timedelta(days=45):
            continue

        closes = df["Close"].astype(float)
        rets = closes.pct_change().dropna()
        if len(rets) < 250 * YEARS * MIN_COVERAGE:
            continue

        ann_vol = float(rets.std() * np.sqrt(252))
        asset, sector, country = classify(name)

        kept.append(
            {
                "ticker": ticker,
                "name": name,
                "risk_tag": risk_tag_from_vol(ann_vol),
                "risk_tag_method": "3y_daily_volatility",
                "ann_vol": round(ann_vol, 4),
                "asset_group": asset,
                "sector_group": sector,
                "country_group": country,
                "marcap_eok": int(row.MarCap) if not pd.isna(row.MarCap) else None,
                "first_date": first.isoformat(),
            }
        )
        frames.append(pd.DataFrame({"ticker": ticker, "date": closes.index.date, "close": closes.values}))

        if i % 25 == 0:
            print(f"      [{i:3}/{len(listing)}] 수집중... 통과 {len(kept)}종목")
        if len(kept) >= TARGET_COUNT:
            print(f"      목표 {TARGET_COUNT}종목 도달, 중단")
            break

    if not kept:
        print("!! 통과한 종목이 없다")
        return 1

    print(f"[3/4] 저장 — {len(kept)}종목")
    uni = pd.DataFrame(kept)
    uni.to_csv(OUT / "universe.csv", index=False, encoding="utf-8")

    prices = pd.concat(frames, ignore_index=True)
    prices = prices[prices["ticker"].isin(uni["ticker"])]
    prices.to_csv(OUT / "prices.csv", index=False, encoding="utf-8")

    meta = {
        "as_of": AS_OF.isoformat(),
        "start": START.isoformat(),
        "source": "FinanceDataReader",
        "source_note": "pykrx 는 KRX 로그인(KRX_ID/KRX_PW)을 요구해 사용하지 않았다",
        "price_field": "Close (시장가격, NAV 아님)",
        "leverage_inverse_excluded": True,
        "risk_tag_method": "3y_daily_volatility",
        "risk_tag_bands": {tag: f">{t:.0%}" for t, tag in VOL_BANDS},
        "risk_tag_caveat": "경계값은 팀 프로젝트 기준이며 금융투자협회 공시 수치가 아니다",
        "ticker_count": int(len(uni)),
        "price_rows": int(len(prices)),
    }
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[4/4] 요약")
    print(uni["risk_tag"].value_counts().sort_index().to_string())
    print(uni["asset_group"].value_counts().to_string())
    print(uni["sector_group"].value_counts().to_string())
    print(uni["country_group"].value_counts().to_string())
    print(f"\n저장: {OUT/'universe.csv'} / {OUT/'prices.csv'} / {OUT/'meta.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
