#!/usr/bin/env python3
"""fetch_prices.py 가 만든 data/prices.csv 를 price_daily 에 적재한다 (질의 8번 답, 2026-09-19).

## 왜 FinanceDataReader 를 여기서 다시 부르지 않는가

ingest_index.py · ingest_macro.py 는 외부 API 를 직접 때리므로 분기 청크와
--resume 이 필요했다. 여기는 다르다. **종목 선정은 build_universe.py 에서, 수집은
fetch_prices.py 에서 이미 끝나 있다.** 60종목이 어떤 기준으로 뽑혔는지는
data/meta.json 에, 가격을 언제 어느 구간으로 받았는지는 data/prices.meta.json 에
남으므로, 적재가 같은 수집을 다시 하면 산출물이 갈라진다. 그래서 이 스크립트는 CSV 만 읽는다.

시세를 다시 받으려면 fetch_prices.py 를 다시 돌린다. 그게 재현 단위다.
--start 를 주지 않으면 prices.csv 전체를 적재한다(2026-09-24).

## atr_14 · nav 는 채우지 않는다

atr_14 는 app/views/market/features.py 가 계산하는 값이라 DB 에 두면 정의가
두 곳이 된다. nav 는 FinanceDataReader 가 주지 않는다(시장가격만 준다).

사용:
    python scripts/build_universe.py                       # 종목 선정
    python scripts/fetch_prices.py                         # 가격 CSV
    DATABASE_URL=... python scripts/ingest_prices.py       # 그 다음 적재
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.repositories.price_daily import upsert_price_bars  # noqa: E402

DEFAULT_PRICES = ROOT / "data" / "prices.csv"
DEFAULT_UNIVERSE = ROOT / "data" / "universe.csv"

# build_universe.py 가 내는 컬럼. close 만 필수다 — 구버전 CSV(종가만 저장하던
# 시절)도 읽히게 두되, 그 경우 atr_14_pct·volume_ratio_20 이 영구 결측이 된다는
# 경고를 띄운다.
_REQUIRED = ("ticker", "date", "close")
_OPTIONAL = ("open", "high", "low", "volume")


def load_universe(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [row["ticker"] for row in csv.DictReader(handle) if row.get("ticker")]


def load_prices(path: Path, *, start: date | None, end: date) -> tuple[dict[str, list[dict]], set[str]]:
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    present: set[str] = set()

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in _REQUIRED if column not in (reader.fieldnames or ())]
        if missing:
            raise SystemExit(f"[ingest_prices] {path} 에 필요한 컬럼이 없다: {missing}")
        present = {column for column in _OPTIONAL if column in (reader.fieldnames or ())}

        for row in reader:
            trade_date = date.fromisoformat(row["date"])
            if (start is not None and trade_date < start) or trade_date > end:
                continue
            bar: dict = {"trade_date": trade_date, "close": _number(row.get("close"))}
            for column in present:
                bar[column] = _number(row.get(column))
            by_ticker[row["ticker"]].append(bar)

    return by_ticker, present


def _number(raw: str | None) -> float | None:
    if raw is None or raw == "" or raw.lower() in ("nan", "none"):
        return None
    return float(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", default=str(DEFAULT_PRICES))
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    parser.add_argument("--start", default=None, help="생략하면 prices.csv 전체")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--dry-run", action="store_true", help="적재하지 않고 집계만 보여준다")
    args = parser.parse_args()

    prices_path = Path(args.prices)
    universe_path = Path(args.universe)
    for path in (prices_path, universe_path):
        if not path.exists():
            raise SystemExit(
                f"[ingest_prices] {path} 가 없다. build_universe.py → fetch_prices.py 를 먼저 돌려라"
            )

    tickers = load_universe(universe_path)
    by_ticker, present = load_prices(
        prices_path,
        start=date.fromisoformat(args.start) if args.start else None,
        end=date.fromisoformat(args.end),
    )

    lacking = sorted(set(_OPTIONAL) - present)
    if lacking:
        print(
            f"[ingest_prices] 경고: CSV 에 {lacking} 이 없다. "
            "atr_14_pct(고가·저가) · volume_ratio_20(거래량) 이 영구 결측이 된다. "
            "fetch_prices.py 가 OHLCV 를 저장하는지 확인하라.",
            file=sys.stderr,
        )

    total = skipped = 0
    for ticker in tickers:
        bars = by_ticker.get(ticker, [])
        if not bars:
            print(f"[ingest_prices] {ticker}: 구간 내 행 없음 — 건너뜀", file=sys.stderr)
            skipped += 1
            continue
        bars.sort(key=lambda bar: bar["trade_date"])
        if args.dry_run:
            span = f"{bars[0]['trade_date']}~{bars[-1]['trade_date']}"
            print(f"[ingest_prices] {ticker}: {len(bars)}행 ({span})")
            total += len(bars)
            continue
        total += upsert_price_bars(ticker, bars=bars)

    unknown = sorted(set(by_ticker) - set(tickers))
    if unknown:
        print(f"[ingest_prices] universe 에 없는 종목 {len(unknown)}개는 적재하지 않았다", file=sys.stderr)

    verb = "적재 예정" if args.dry_run else "적재"
    print(f"[ingest_prices] 종목 {len(tickers) - skipped}개 · {verb} {total}행 · 건너뜀 {skipped}개")
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
