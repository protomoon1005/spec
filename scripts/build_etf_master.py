"""db/seeds/04_etf_master.csv 를 pykrx로 채운다.

주의: 이 스크립트는 아직 한 번도 실행된 적이 없다. pykrx 함수 시그니처
(get_etf_ticker_list / get_etf_ticker_name / get_etf_ohlcv_by_date)는 이 세션에서
검증하지 않았다 — 실행 전에 pykrx 버전에 맞는지 직접 확인할 것.
graduation-project/CLAUDE.md 규칙: "모르는 건 지어내지 말 것".

하는 일 (사용자 지시, 2026-09-08):
  1. 오늘 시점 ETF 티커 목록을 긁어 (티커, 종목명)만 CSV에 채운다.
  2. 과거 시점(기본 2023-01-02)과 오늘 시점의 티커 목록을 비교해, 과거엔 있었는데
     오늘은 없는 티커를 "상장폐지 후보"로 찾는다.
  3. 상장폐지 후보는 delisted_date(마지막 시세 조회일로 근사 — 실제 상장폐지일과
     다를 수 있다, 사람이 검증할 것)와 active=false를 채운다.
  4. risk_tag / group_id / sector / country는 절대 자동으로 채우지 않는다.
     사람이 검토해서 채우는 컬럼이다 (infra-spec.md 4.5).
  5. 기존 CSV에 이미 있는 행(수기로 채운 4종목 등)은 덮어쓰지 않고 보존한다.

실행 방법 (지금은 실행하지 않는다 — 사용자 지시):
    python scripts/build_etf_master.py --out db/seeds/04_etf_master.csv --delisted-asof 2023-01-02
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

CSV_COLUMNS = [
    "ticker", "name", "sector", "group_id", "risk_tag",
    "mdd_3y", "volatility_1y", "expense_ratio",
    "listed_date", "delisted_date", "is_leveraged", "active", "country",
]


@dataclass
class EtfRow:
    ticker: str
    name: str
    sector: str = ""
    group_id: str = ""
    risk_tag: str = ""
    mdd_3y: str = ""
    volatility_1y: str = ""
    expense_ratio: str = ""
    listed_date: str = ""
    delisted_date: str = ""
    is_leveraged: str = ""
    active: str = ""
    country: str = ""

    def as_dict(self) -> dict[str, str]:
        return {col: getattr(self, col) for col in CSV_COLUMNS}


def load_existing(csv_path: Path) -> dict[str, EtfRow]:
    """이미 있는 CSV를 읽는다. 사람이 채운 값(risk_tag, group_id 등)을 보존하기 위함이다."""
    if not csv_path.exists():
        return {}
    rows: dict[str, EtfRow] = {}
    with csv_path.open(encoding="utf-8", newline="") as f:
        for raw in csv.DictReader(f):
            ticker = raw["ticker"].strip()
            rows[ticker] = EtfRow(**{col: raw.get(col, "") or "" for col in CSV_COLUMNS})
    return rows


def fetch_current_tickers(as_of: date) -> dict[str, str]:
    """as_of 시점의 {티커: 종목명}. pykrx.stock.get_etf_ticker_list / get_etf_ticker_name 사용.

    -- TODO: pykrx 실제 함수 시그니처를 이 세션에서 검증하지 않았다. 실행 전 확인할 것.
    """
    from pykrx import stock  # 지연 임포트: 이 모듈을 import하지 않고도 --help 등은 동작하게

    ymd = as_of.strftime("%Y%m%d")
    tickers = stock.get_etf_ticker_list(ymd)
    result: dict[str, str] = {}
    for ticker in tickers:
        try:
            result[ticker] = stock.get_etf_ticker_name(ticker)
        except Exception as exc:  # noqa: BLE001 - 개별 티커 조회 실패는 건너뛰고 계속 진행
            print(f"[build_etf_master] {ticker} 이름 조회 실패, 건너뜀: {exc}", file=sys.stderr)
    return result


def approximate_last_trade_date(ticker: str, since: date) -> str:
    """상장폐지 후보 티커의 마지막 시세일을 근사 delisted_date로 쓴다.

    실제 상장폐지 효력일이 아니라 "마지막으로 시세가 잡힌 날"이다. 근사치이므로
    사람이 검증해야 한다는 것을 CSV 자체에 남기지는 못하니 README/커밋 메시지에 남길 것.
    -- TODO: pykrx 실제 함수 시그니처를 이 세션에서 검증하지 않았다.
    """
    from pykrx import stock

    today_str = date.today().strftime("%Y%m%d")
    since_str = since.strftime("%Y%m%d")
    try:
        df = stock.get_etf_ohlcv_by_date(since_str, today_str, ticker)
    except Exception as exc:  # noqa: BLE001
        print(f"[build_etf_master] {ticker} 시세 조회 실패: {exc}", file=sys.stderr)
        return ""
    if df is None or df.empty:
        return ""
    last_date = df.index.max()
    return last_date.strftime("%Y-%m-%d") if hasattr(last_date, "strftime") else str(last_date)


def build(csv_path: Path, delisted_asof: date) -> None:
    existing = load_existing(csv_path)

    today_tickers = fetch_current_tickers(date.today())
    past_tickers = fetch_current_tickers(delisted_asof)

    # 1) 오늘 시점 목록에 있는 티커 전부: 없으면 새로 추가(ticker+name만), 있으면 보존
    for ticker, name in today_tickers.items():
        if ticker not in existing:
            existing[ticker] = EtfRow(ticker=ticker, name=name)

    # 2) 과거엔 있었는데 오늘은 없는 티커: 상장폐지 후보
    delisted_candidates = set(past_tickers) - set(today_tickers)
    for ticker in delisted_candidates:
        row = existing.get(ticker) or EtfRow(ticker=ticker, name=past_tickers[ticker])
        if not row.delisted_date:
            row.delisted_date = approximate_last_trade_date(ticker, delisted_asof)
        row.active = "false"
        existing[ticker] = row

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for ticker in sorted(existing):
            writer.writerow(existing[ticker].as_dict())

    print(
        f"[build_etf_master] {len(existing)}행 기록, "
        f"상장폐지 후보 {len(delisted_candidates)}건 ({csv_path})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("db/seeds/04_etf_master.csv"))
    parser.add_argument(
        "--delisted-asof",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=date(2023, 1, 2),
        help="이 날짜의 티커 목록과 오늘 목록을 비교해 상장폐지 후보를 찾는다",
    )
    args = parser.parse_args()
    build(args.out, args.delisted_asof)


if __name__ == "__main__":
    main()
