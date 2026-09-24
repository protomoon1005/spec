"""universe.csv 에 이미 고정된 종목의 일별 OHLCV 만 다시 받아 data/prices.csv 를 쓴다.

## 왜 build_universe.py 와 따로 두는가 (2026-09-24 결정)

build_universe.py 는 종목을 **뽑는** 스크립트다. 그쪽 START 를 당기면 상장일 필터
(START+45일)에 현 유니버스 60종목 중 26개가 걸려 다른 ETF 로 바뀌고, risk_tag
변동성 구간도 달라진다. 유니버스와 risk_tag 는 그대로 두고 시장분석 학습 이력만
늘리려고 가격 수집을 여기로 분리했다.

- data/prices.csv 를 쓰는 곳은 이 스크립트 하나다. build_universe.py 는 쓰지 않는다.
- universe.csv 는 읽기만 한다. meta.json(선정·risk_tag 파라미터)은 건드리지 않고,
  가격 쪽 설명은 data/prices.meta.json 에 따로 쓴다.
- 종목마다 PRICE_START 또는 상장일 중 늦은 날부터 받는다.
- 종료일은 build_universe.AS_OF 를 그대로 쓴다 — 두 곳에 두면 갈라진다.
- 한 종목이라도 실패하면 prices.csv 를 쓰지 않는다. 일부만 바뀐 CSV 는
  어느 종목이 언제 받은 값인지 알 수 없게 만든다.

사용:
    python scripts/fetch_prices.py
    DATABASE_URL=... python scripts/ingest_prices.py --start 2019-01-02
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import UTC, date, datetime

import FinanceDataReader as fdr
import pandas as pd
from build_universe import AS_OF, OUT

PRICE_START = date(2019, 1, 2)
UNIVERSE = OUT / "universe.csv"
PRICES = OUT / "prices.csv"
PRICES_META = OUT / "prices.meta.json"


def main() -> int:
    with UNIVERSE.open(encoding="utf-8", newline="") as handle:
        tickers = [row["ticker"] for row in csv.DictReader(handle) if row.get("ticker")]
    print(f"[fetch_prices] {len(tickers)}종목 · {PRICE_START} ~ {AS_OF}")

    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    frames: list[pd.DataFrame] = []
    first_dates: dict[str, str] = {}
    failed: list[str] = []
    for ticker in tickers:
        try:
            df = fdr.DataReader(ticker, PRICE_START.isoformat(), AS_OF.isoformat())
        except Exception as exc:  # 실패 목록을 다 모아서 한 번에 보고한다
            print(f"[fetch_prices] {ticker} 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed.append(ticker)
            continue
        if df is None or df.empty or "Close" not in df:
            print(f"[fetch_prices] {ticker} 빈 응답", file=sys.stderr)
            failed.append(ticker)
            continue
        df = df[df.index.date <= AS_OF]

        # build_universe.py 와 같은 열·같은 형식으로 쓴다 — ingest_prices.py 가 그대로 읽는다.
        bars = df.reindex(columns=["Open", "High", "Low", "Close", "Volume"])
        frames.append(
            pd.DataFrame(
                {
                    "ticker": ticker,
                    "date": bars.index.date,
                    "open": bars["Open"].astype(float).values,
                    "high": bars["High"].astype(float).values,
                    "low": bars["Low"].astype(float).values,
                    "close": bars["Close"].astype(float).values,
                    "volume": bars["Volume"].astype(float).values,
                }
            )
        )
        first_dates[ticker] = bars.index[0].date().isoformat()
        print(f"[fetch_prices] {ticker} {bars.index[0].date()} ~ {bars.index[-1].date()} {len(bars)}행")

    if failed:
        print(f"[fetch_prices] {len(failed)}종목 실패 — prices.csv 를 쓰지 않았다: {failed}", file=sys.stderr)
        return 1

    prices = pd.concat(frames, ignore_index=True)
    prices.to_csv(PRICES, index=False, encoding="utf-8")
    meta = {
        "start": PRICE_START.isoformat(),
        "end": AS_OF.isoformat(),
        "rows": int(len(prices)),
        "first_dates": first_dates,
        "fetched_at": fetched_at,
        "source": "FinanceDataReader",
        "price_field": "OHLCV (시장가격, NAV 아님)",
    }
    PRICES_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[fetch_prices] 저장 {PRICES} — {len(prices)}행 / {PRICES_META}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
