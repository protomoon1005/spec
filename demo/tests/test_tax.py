"""세율 표 (config/tax_rates.yaml, v0.2 "세금과 배당"). 거래한 날짜의 세율을 쓴다."""
from datetime import date

import pytest

from conftest import cmp, ind, make_ohlcv, strategy_dict
from core.backtest import run_backtest
from core.schema import Strategy
from core.tax import gain_tax_rate, load_tax_rates, sell_tax_rate


@pytest.mark.parametrize("category,day,rate", [
    ("KOSPI_STOCK", date(2023, 6, 1), 0.0020),
    ("KOSPI_STOCK", date(2024, 6, 3), 0.0018),     # 2024 매도 0.18%
    ("KOSPI_STOCK", date(2024, 12, 30), 0.0018),
    ("KOSPI_STOCK", date(2025, 1, 2), 0.0015),     # 거래세 0 + 농특세 0.15
    ("KOSPI_STOCK", date(2026, 3, 3), 0.0020),     # 2026 매도 0.20%
    ("KOSDAQ_STOCK", date(2023, 6, 1), 0.0020),
    ("KOSDAQ_STOCK", date(2024, 6, 3), 0.0018),
    ("KOSDAQ_STOCK", date(2025, 6, 2), 0.0015),
    ("KOSDAQ_STOCK", date(2026, 3, 3), 0.0020),
    ("ETF_DOMESTIC_EQUITY", date(2024, 6, 3), 0.0),
    ("ETF_OTHER", date(2024, 6, 3), 0.0),          # ETF 는 팔 때 거래세 없음
])
def test_sell_tax_rate_by_trade_date(category, day, rate):
    assert sell_tax_rate(category, day) == pytest.approx(rate)


def test_gain_tax_only_for_other_etf():
    assert gain_tax_rate("ETF_OTHER", date(2024, 6, 3)) == pytest.approx(0.154)
    assert gain_tax_rate("ETF_DOMESTIC_EQUITY", date(2024, 6, 3)) == 0.0
    assert gain_tax_rate("KOSPI_STOCK", date(2024, 6, 3)) == 0.0


def test_date_outside_table_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        sell_tax_rate("KOSPI_STOCK", date(2022, 6, 1))
    with pytest.raises(ValueError):
        sell_tax_rate("NO_SUCH_CATEGORY", date(2024, 6, 3))


def test_table_rows_do_not_overlap_and_have_basis():
    rows = load_tax_rates()
    assert rows, "세율 표가 비어 있다"
    for r in rows:
        assert r["legal_basis"]
    keyed: dict = {}
    for r in rows:
        keyed.setdefault((r["applies_to"], r["tax_kind"]), []).append(r)
    for key, rs in keyed.items():
        rs = sorted(rs, key=lambda r: r["valid_from"])
        for a, b in zip(rs, rs[1:]):
            assert a["valid_to"] is not None and a["valid_to"] < b["valid_from"], key


def _sell_in(year: int, category: str):
    df = make_ohlcv([10_500, 9_900, 10_200, 12_100, 12_000], opens=[10_500, 10_400, 10_000, 10_300, 11_000],
                    start=f"{year}-03-03")
    d = strategy_dict(rules={"target": "each",
                             "entry": cmp(ind("close"), "cross_below", 10_000),
                             "exit": cmp(ind("close"), "cross_above", 12_000)},
                      backtest={"initial_cash": 1_000_000})
    res = run_backtest(Strategy.model_validate(d), data={"TEST01": df}, instruments={"TEST01": category})
    sell = res.fills[1]
    return sell["tax"], sell["shares"] * sell["price"]


def test_backtest_uses_rate_of_the_sell_date():
    tax24, gross24 = _sell_in(2024, "KOSPI_STOCK")
    tax26, gross26 = _sell_in(2026, "KOSPI_STOCK")
    assert tax24 == pytest.approx(gross24 * 0.0018)
    assert tax26 == pytest.approx(gross26 * 0.0020)
    tax_etf, _ = _sell_in(2024, "ETF_DOMESTIC_EQUITY")
    assert tax_etf == 0.0


def test_other_etf_gain_tax_only_on_profit():
    """그 밖의 ETF: 차익 × 15.4%, 손해 본 거래는 세금 없음 (다른 거래와 상계하지 않음)."""
    df = make_ohlcv([10_500, 9_900, 10_200, 12_100, 12_000], opens=[10_500, 10_400, 10_000, 10_300, 9_000])
    d = strategy_dict(rules={"target": "each",
                             "entry": cmp(ind("close"), "cross_below", 10_000),
                             "exit": cmp(ind("close"), "cross_above", 12_000)},
                      backtest={"initial_cash": 1_000_000})
    res = run_backtest(Strategy.model_validate(d), data={"TEST01": df}, instruments={"TEST01": "ETF_OTHER"})
    assert res.fills[1]["tax"] == 0.0
