"""비중 이탈(threshold) 리밸런싱 — runner.drifted_weights · run(drift=...).

RB_THRESHOLD_5 "목표 비중에서 5%p 이상 벌어진 종목이 생기면 되돌린다"(db/seeds/05_bbl.sql).
TS 러너(lib/backtest.ts driftedWeights · runBacktest drift)와 같은 식인지는 실데이터로
tests/test_backtest_parity.py 가 본다. 여기서는 합성 가격으로 판정 규칙만 본다.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.requires_backtest
pd = pytest.importorskip("pandas")
pytest.importorskip("vectorbt")

from app.backtest.runner import DRIFT_THRESHOLD, drifted_weights, max_drift, run  # noqa: E402

TICKERS = ["A", "B"]
TARGET = {"A": 0.5, "B": 0.4}  # 현금 0.1
HOLDINGS = [
    {"ticker": t, "grade": "G4", "asset_group": "EQUITY", "sector_group": "SECTOR_OTHER",
     "country_group": "COUNTRY_KR", "min_raw": 0.0, "max_raw": 1.0}
    for t in TICKERS
]


def _fixed_weigh(d, signals):
    # 매번 같은 목표 — 이탈은 가격으로만 생긴다
    return dict(TARGET), dict(TARGET), 0.1, []


def _prices(b_path: list[float], dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"A": [100.0] * len(dates), "B": b_path}, index=dates)


def test_drifted_weights_follow_the_price_ratio_and_keep_cash_still():
    w = drifted_weights(TARGET, {"A": 100.0, "B": 10.0}, {"A": 100.0, "B": 13.0}, TICKERS)

    # B 0.4 × 1.3 = 0.52, 분모 0.5 + 0.52 + 0.1 = 1.12
    assert w["A"] == pytest.approx(0.5 / 1.12)
    assert w["B"] == pytest.approx(0.52 / 1.12)
    assert max_drift(TARGET, {"A": 100.0, "B": 10.0}, {"A": 100.0, "B": 13.0}, TICKERS) == pytest.approx(
        0.52 / 1.12 - 0.4
    )


def test_missing_or_nonpositive_price_counts_as_unchanged():
    nan = float("nan")
    w = drifted_weights(TARGET, {"A": nan, "B": 10.0}, {"A": 120.0, "B": 0.0}, TICKERS)

    assert w == pytest.approx(TARGET)


def test_threshold_is_the_block_value():
    assert DRIFT_THRESHOLD == 0.05


def test_rebalances_only_when_some_ticker_drifts_by_the_threshold_or_more():
    dates = ["2024-01-05", "2024-01-12", "2024-01-19", "2024-01-26", "2024-02-02"]
    # B: 1.1 배는 이탈 0.4·1.1/1.04 − 0.4 = 0.023 → 안 함. 1.3 배는 0.064 → 함. 그 뒤 제자리
    px = _prices([10.0, 11.0, 13.0, 13.0, 13.0], dates)

    got = run(px, HOLDINGS, 4, dates, dates, 1_000_000, use_signals=False,
              weigh=_fixed_weigh, drift=(DRIFT_THRESHOLD, 5))

    assert got["rebalance_dates"] == ["2024-01-05", "2024-01-19"]
    assert got["skipped_rebalance_dates"] == []
    # 이탈 기준은 직전 "리밸런싱일" 가격이다 — 01-19 이후 B 가 그대로라 다시 안 한다
    assert [d["date"] for d in got["decisions"]] == got["rebalance_dates"]


def test_drift_within_min_interval_is_skipped_and_rechecked_later():
    dates = ["2024-01-05", "2024-01-08", "2024-01-12"]
    px = _prices([10.0, 13.0, 13.0], dates)

    got = run(px, HOLDINGS, 4, dates, dates, 1_000_000, use_signals=False,
              weigh=_fixed_weigh, drift=(DRIFT_THRESHOLD, 5))

    # 01-08 은 이탈했지만 3일 간격이라 건너뛰고, 01-12(7일)에 다시 판정해 되돌린다
    assert got["skipped_rebalance_dates"] == ["2024-01-08"]
    assert got["rebalance_dates"] == ["2024-01-05", "2024-01-12"]


def test_without_drift_every_given_date_rebalances_as_before():
    dates = ["2024-01-05", "2024-01-12", "2024-01-19"]
    px = _prices([10.0, 10.0, 10.0], dates)

    got = run(px, HOLDINGS, 4, dates, dates, 1_000_000, use_signals=False, weigh=_fixed_weigh)

    assert got["rebalance_dates"] == dates and got["skipped_rebalance_dates"] == []
