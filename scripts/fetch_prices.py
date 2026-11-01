#!/usr/bin/env python3
"""국내 상장 보통주와 ETF 의 일봉을 받아 가격 스냅샷을 만든다 (기반 정리 3단계, docs/pivot-plan.md 3.2).

## 무엇을 만드나

  data/listing.csv                       종목 목록 (git) — 원장 시드(04_etf_master.csv)의 재료
  data/snapshot/prices-<버전>.parquet    가격 스냅샷 (git 밖, GitHub Release 로 공유)
  data/prices_snapshot.json              스냅샷 메타 (git) — 버전 · 소스 · 기간 · 종목 수 · 해시 · 정리 규칙

가격을 API 에서 받는 것은 스냅샷을 만드는 이 스크립트 한 번뿐이다. 나머지 사람은 스냅샷을 받아
빌드 때 적재만 한다(scripts/ingest_prices.py). 그래야 모두 같은 가격을 본다.

## 소스 (2026-11-02 결정)

네이버 금융 차트 API(fchart.stock.naver.com) — FinanceDataReader 가 국내 종목에 쓰는 소스를 그대로
호출하되 연결 재사용 · 병렬 · 재시도를 더했다. **수정주가다** (에코프로 2024-04 5:1 분할 전후가 이어짐).
종목 목록은 FinanceDataReader 의 KRX 상장 목록과 ETF 목록, 업종은 KRX 상장법인 업종(KRX-DESC).

## 받는 범위

- 보통주: KRX 상장 종목에서 우선주 · 스팩 · 리츠 · 인프라펀드를 뺀다
- ETF: 국내 상장 ETF 전체
- 기간: 받는 날부터 5년 (앞 2년은 이동평균 같은 지표의 준비 기간, 2026-11-02 결정)
- **상장폐지 종목은 없다.** 목록이 "받는 날 상장된 종목" 이라 그 사이 폐지된 종목이 빠진다(생존편향).
  이번 스냅샷은 빼기로 했다(2026-11-02 결정). 메타의 excluded 에 적는다

## 정리 규칙

- 거래정지일은 원천이 시가 · 고가 · 저가 0, 거래량 0, 종가는 직전 값으로 준다. 0 을 그대로 두면 고가 · 저가를
  쓰는 지표가 망가지므로 **시가 · 고가 · 저가를 빈 값으로** 바꾼다. 종가 · 거래량은 그대로 둔다
- 행은 (종목, 거래일) 순으로 정렬한다. 같은 입력이면 같은 파일이 나와야 해시로 확인할 수 있다

## 해시 두 개

- file_sha256: 받은 파일이 깨지지 않았는지 (빌드가 내려받은 뒤 확인)
- content_sha256: 행 내용의 해시. 적재한 DB 의 price_daily 를 같은 규칙으로 계산해 맞춰 본다 —
  "두 사람이 빌드한 DB 의 가격이 같다" 를 이 값 하나로 확인한다(price_content_sha256)

사용 (api 컨테이너 안, 저장소 루트가 /repo):
    python /repo/scripts/fetch_prices.py                          # 처음부터 받기 (수십 분)
    python /repo/scripts/fetch_prices.py --from-raw /repo/data/raw --version 2026-11-02
        # 이미 받은 원천 CSV(보통주_목록 · 보통주_일봉 · ETF_목록 · ETF_일봉,
        # KRX-DESC 가 있으면 krx_desc.csv)로 만들기
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.repositories.price_daily import price_content_sha256  # noqa: E402

DATA = ROOT / "data"
LISTING = DATA / "listing.csv"
META = DATA / "prices_snapshot.json"
SNAPSHOT_DIR = DATA / "snapshot"

YEARS = 5
COUNT = 1400  # 5년(약 1,230거래일) + 첫날 등락 계산 여유
WORKERS = 4
URL = "https://fchart.stock.naver.com/sise.nhn?timeframe=day&count={count}&requestType=0&symbol={code}"
SOURCE = "네이버 금융 차트 API(fchart.stock.naver.com, FinanceDataReader 가 국내 종목에 쓰는 소스) · 수정주가"
ETF_CATEGORY = {
    1: "국내 시장지수", 2: "국내 업종/테마", 3: "국내 파생", 4: "해외 주식",
    5: "원자재", 6: "채권", 7: "기타",
}

LISTING_COLUMNS = ["ticker", "name", "kind", "market", "category", "industry", "listing_date", "marcap"]
PRICE_COLUMNS = ["ticker", "trade_date", "open", "high", "low", "close", "volume"]

_local = threading.local()


# --- 목록 ------------------------------------------------------------------------


def common_stock_list():
    """KRX 상장 종목에서 우선주 · 스팩 · 리츠 · 인프라펀드를 뺀 보통주."""
    import FinanceDataReader as fdr

    krx = fdr.StockListing("KRX")
    krx["Market"] = krx["Market"].replace({"KOSDAQ GLOBAL": "KOSDAQ"})
    name, dept = krx["Name"], krx["Dept"].fillna("")
    is_pref = krx["Code"].str[-1] != "0"  # 단축코드 끝자리 0 = 보통주
    is_spac = name.str.contains("스팩|기업인수목적") | dept.str.startswith("SPAC")
    is_reit = name.str.endswith("리츠") | (name == "이리츠코크렙")
    is_infra = name.isin(["맥쿼리인프라", "KB발해인프라"])
    keep = ~(is_pref | is_spac | is_reit | is_infra)
    return krx.loc[keep, ["Code", "Name", "Market", "Dept", "Marcap", "Stocks"]].reset_index(drop=True)


def etf_list():
    import FinanceDataReader as fdr

    etf = fdr.StockListing("ETF/KR").rename(columns={"Symbol": "Code", "MarCap": "MarCap_억원"})
    etf["Category"] = etf["Category"].map(ETF_CATEGORY)
    return etf[["Code", "Name", "Category", "NAV", "MarCap_억원"]]


def krx_desc():
    """KRX 상장법인 업종(KSIC 업종명 = Industry 칸)과 상장일."""
    import FinanceDataReader as fdr

    return fdr.StockListing("KRX-DESC")


def build_listing(stocks: pd.DataFrame, etfs: pd.DataFrame, desc: pd.DataFrame) -> pd.DataFrame:
    desc = desc[["Code", "Industry", "ListingDate"]].drop_duplicates("Code")
    s = stocks.merge(desc, on="Code", how="left")
    missing = s[s["Industry"].isna()]
    if not missing.empty:
        codes = missing["Code"].tolist()[:10]
        raise SystemExit(f"[fetch_prices] 업종을 못 찾은 보통주 {len(missing)}개: {codes}")
    stock_rows = pd.DataFrame(
        {
            "ticker": s["Code"],
            "name": s["Name"],
            "kind": "stock",
            "market": s["Market"],
            "category": "",
            "industry": s["Industry"],
            "listing_date": pd.to_datetime(s["ListingDate"]).dt.strftime("%Y-%m-%d"),
            "marcap": s["Marcap"],
        }
    )
    etf_rows = pd.DataFrame(
        {
            "ticker": etfs["Code"],
            "name": etfs["Name"],
            "kind": "etf",
            "market": "ETF",
            "category": etfs["Category"].fillna(""),
            "industry": "",
            "listing_date": "",
            # ETF 목록의 시가총액은 억 원 단위다. 보통주와 같은 원 단위로 맞춘다
            "marcap": (etfs["MarCap_억원"].astype("float").fillna(0) * 100_000_000).round().astype("int64"),
        }
    )
    listing = pd.concat([stock_rows, etf_rows], ignore_index=True)
    dup = listing[listing["ticker"].duplicated()]
    if not dup.empty:
        raise SystemExit(f"[fetch_prices] 보통주와 ETF 에 같은 코드가 있다: {dup['ticker'].tolist()}")
    return listing.sort_values("ticker").reset_index(drop=True)[LISTING_COLUMNS]


# --- 일봉 ------------------------------------------------------------------------


def fetch_daily(code: str, start: pd.Timestamp) -> pd.DataFrame | None:
    import requests

    session = getattr(_local, "session", None)
    if session is None:
        session = _local.session = requests.Session()
    for attempt in range(3):
        try:
            r = session.get(URL.format(count=COUNT, code=code), timeout=20)
            r.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 2:
                return None
            time.sleep(2**attempt)
    items = re.findall(r'<item data="(.*?)" />', r.text, re.DOTALL)
    if not items:
        return None
    df = pd.read_csv(
        StringIO("\n".join(items)), sep="|", header=None, dtype={"Date": str},
        names=["Date", "Open", "High", "Low", "Close", "Volume"],
    )
    df["Date"] = pd.to_datetime(df["Date"], format="%Y%m%d")
    df = df.sort_values("Date")
    df = df[df["Date"] >= start]
    df.insert(1, "Code", code)
    return df


def collect_daily(codes: list[str], start: pd.Timestamp) -> tuple[pd.DataFrame, list[str]]:
    with ThreadPoolExecutor(WORKERS) as ex:
        frames = dict(zip(codes, ex.map(lambda c: fetch_daily(c, start), codes)))
    for code in [c for c, df in frames.items() if df is None]:  # 실패분은 순차로 한 번 더
        frames[code] = fetch_daily(code, start)
    missing = [c for c, df in frames.items() if df is None or df.empty]
    prices = pd.concat([df for df in frames.values() if df is not None and not df.empty], ignore_index=True)
    return prices, missing


# --- 스냅샷 ----------------------------------------------------------------------


def clean_prices(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """원천 일봉(Date, Code, Open, High, Low, Close, Volume) → 스냅샷 칸.

    거래정지일 시가·고가·저가는 빈 값.
    """
    df = pd.DataFrame(
        {
            "ticker": raw["Code"].astype(str),
            "trade_date": pd.to_datetime(raw["Date"]).dt.date,
            "open": raw["Open"].astype("float64"),
            "high": raw["High"].astype("float64"),
            "low": raw["Low"].astype("float64"),
            "close": raw["Close"].astype("float64"),
            "volume": raw["Volume"].astype("int64"),
        }
    )
    halted = (df["open"] == 0) & (df["high"] == 0) & (df["low"] == 0)
    df.loc[halted, ["open", "high", "low"]] = float("nan")
    if df.duplicated(["ticker", "trade_date"]).any():
        raise SystemExit("[fetch_prices] 같은 (종목, 거래일)이 두 번 있다")
    if df["close"].isna().any() or (df["close"] <= 0).any():
        raise SystemExit("[fetch_prices] 종가가 비었거나 0 이하인 행이 있다")
    return df.sort_values(["ticker", "trade_date"]).reset_index(drop=True), int(halted.sum())


def write_snapshot(prices: pd.DataFrame, listing: pd.DataFrame, *, version: str, halted_rows: int,
                   fetched_at: str) -> dict:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"prices-{version}.parquet"
    path = SNAPSHOT_DIR / name
    prices.to_parquet(path, index=False, compression="zstd")

    kinds = listing.set_index("ticker")["kind"]
    in_prices = prices["ticker"].unique()
    meta = {
        "version": version,
        "file": name,
        # GitHub Release 에 올린 뒤 주소를 적는다. 비어 있으면 빌드가 data/snapshot/ 의 로컬 파일만 찾는다
        "url": "",
        "file_sha256": _file_sha256(path),
        "content_sha256": price_content_sha256(_bars(prices)),
        "bytes": path.stat().st_size,
        "rows": int(len(prices)),
        "tickers": {
            "stock": int(sum(kinds.get(t) == "stock" for t in in_prices)),
            "etf": int(sum(kinds.get(t) == "etf" for t in in_prices)),
        },
        "period": {"start": str(prices["trade_date"].min()), "end": str(prices["trade_date"].max())},
        "fetched_at": fetched_at,
        "source": SOURCE,
        "adjusted": True,
        "cleaning": [
            f"거래정지일(시가·고가·저가 0) {halted_rows}행의 시가·고가·저가를 빈 값으로 바꿨다. "
            "종가·거래량은 원천 그대로",
        ],
        "excluded": [
            "상장폐지 종목 — 받는 날 상장된 종목만 받아 기간 중 폐지된 종목이 없다"
            "(생존편향, 2026-11-02 결정)",
            "보통주의 우선주 · 스팩 · 리츠 · 인프라펀드",
        ],
        "notes": [
            "2026-07-28 ~ 07-31 시장 전체 급락 뒤 급반등(KODEX 200 -11% 뒤 +24%). 원천 그대로 두었다",
        ],
    }
    return meta


def _bars(prices: pd.DataFrame):
    for row in prices.itertuples(index=False):
        yield {
            "ticker": row.ticker,
            "trade_date": row.trade_date,
            "open": _num(row.open),
            "high": _num(row.high),
            "low": _num(row.low),
            "close": _num(row.close),
            "volume": int(row.volume),
        }


def _num(value) -> float | None:
    return None if pd.isna(value) else float(value)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- 실행 ------------------------------------------------------------------------


def _read_raw(raw_dir: Path):
    def read(name: str) -> pd.DataFrame:
        return pd.read_csv(raw_dir / name, dtype={"Code": str}, encoding="utf-8-sig")

    stocks, etfs = read("보통주_목록.csv"), read("ETF_목록.csv")
    daily = pd.concat([read("보통주_일봉.csv"), read("ETF_일봉.csv")], ignore_index=True)
    desc_path = raw_dir / "krx_desc.csv"
    desc = read("krx_desc.csv") if desc_path.exists() else krx_desc()
    return stocks, etfs, desc, daily


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--from-raw", type=Path, help="이미 받은 원천 CSV 폴더. 주면 API 를 부르지 않는다")
    parser.add_argument("--version", default=date.today().isoformat(), help="스냅샷 버전. 기본은 오늘")
    args = parser.parse_args()

    t0 = time.perf_counter()
    if args.from_raw:
        stocks, etfs, desc, daily = _read_raw(args.from_raw)
        modified = (args.from_raw / "보통주_일봉.csv").stat().st_mtime
        fetched_at = datetime.fromtimestamp(modified, UTC).isoformat()
        print(f"[fetch_prices] 원천 CSV 사용: {args.from_raw}")
    else:
        stocks, etfs, desc = common_stock_list(), etf_list(), krx_desc()
        start = pd.Timestamp.today().normalize() - pd.DateOffset(years=YEARS)
        listing_codes = stocks["Code"].tolist() + etfs["Code"].tolist()
        daily, missing = collect_daily(listing_codes, start)
        if missing:
            raise SystemExit(
                f"[fetch_prices] 가격을 못 받은 종목 {len(missing)}개 — 스냅샷을 만들지 않는다: "
                f"{missing[:10]}"
            )
        fetched_at = datetime.now(UTC).isoformat()

    listing = build_listing(stocks, etfs, desc)
    prices, halted = clean_prices(daily)
    no_price = sorted(set(listing["ticker"]) - set(prices["ticker"]))
    if no_price:
        raise SystemExit(f"[fetch_prices] 목록에는 있고 가격이 없는 종목 {len(no_price)}개: {no_price[:10]}")
    unknown = sorted(set(prices["ticker"]) - set(listing["ticker"]))
    if unknown:
        raise SystemExit(f"[fetch_prices] 가격에는 있고 목록에 없는 종목 {len(unknown)}개: {unknown[:10]}")

    meta = write_snapshot(prices, listing, version=args.version, halted_rows=halted, fetched_at=fetched_at)
    listing.to_csv(LISTING, index=False, encoding="utf-8", lineterminator="\n")
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"[fetch_prices] 보통주 {meta['tickers']['stock']:,} · ETF {meta['tickers']['etf']:,} · "
        f"{meta['rows']:,}행 · {meta['period']['start']} ~ {meta['period']['end']} · "
        f"거래정지 정리 {halted:,}행 · {meta['bytes'] / 1e6:.1f}MB · {time.perf_counter() - t0:.0f}초"
    )
    print(
        f"[fetch_prices] {SNAPSHOT_DIR / meta['file']} 를 GitHub Release 에 올리고 "
        f"{META.name} 의 url 을 채운다"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
