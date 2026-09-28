"""spec_id → 러너 입력 조립(app/backtest/inputs.py).

조립 결과가 scripts/run_backtest_vbt.py 의 데모 7종목 입력과 같은 형태인지 본다.
pandas 가 필요해 requires_backtest 다. 데모 비교는 DB 에 적재된 실종가를 읽으므로
requires_backfill 도 붙는다. 로컬에서는
  docker compose exec -w /repo/backend api pytest -m requires_backtest
로 돈다(-w 가 없으면 저장소 루트 data/ 를 못 찾아 CSV 대조가 건너뛰어진다).
"""
from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text

from app.repositories import presets, profiles, specs

pytestmark = pytest.mark.requires_backtest
pd = pytest.importorskip("pandas")

from app.backtest import inputs as bi  # noqa: E402
from app.backtest.runner import monthly_first, weekly_dates  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data"

# scripts/run_backtest_vbt.py 의 SPEC_UNIVERSE
DEMO_UNIVERSE = [
    ("069500", 0.05, 0.35),
    ("091160", 0.00, 0.25),
    ("360750", 0.05, 0.30),
    ("133690", 0.00, 0.25),
    ("273130", 0.10, 0.40),
    ("357870", 0.00, 0.30),
    ("411060", 0.00, 0.15),
]
# 스크립트의 holdings 가 갖는 키
HOLDING_KEYS = {
    "ticker", "name", "grade", "asset_group", "sector_group", "country_group", "min_raw", "max_raw",
}
MONTHLY_FIRST = {"trigger": {"type": "calendar", "freq": "monthly", "day": 1}, "min_interval_days": 20}
PERIOD_START = date(2023, 1, 1)
PERIOD_END = date(2025, 12, 31)


def _build(spec_id: str):
    return bi.build_inputs(spec_id, period_start=PERIOD_START, period_end=PERIOD_END)


@pytest.fixture
def make_spec(engine, make_user):
    """(universe, rebalance) -> spec_id. 성향 4(스크립트 기본값)로 draft 전략서를 만든다."""
    _email, user_id = make_user("retail")
    version = presets.active_preset_version()
    profile = profiles.insert_profile(
        user_id=user_id,
        risk_level=4,
        preset_version=version,
        defaults=profiles.get_profile_defaults(version, 4),
        provenance={},
    )
    bounds = presets.get_asset_bounds(4)
    hardcap = presets.get_active_hardcap()["hardcap_version"]

    def _make(universe, rebalance) -> str:
        tickers = [t for t, _lo, _hi in universe]
        with engine.connect() as conn:
            tags = dict(
                conn.execute(
                    text("SELECT ticker, risk_tag FROM etf_master WHERE ticker = ANY(:t)"), {"t": tickers}
                ).all()
            )
        rows = [
            {
                "ticker": t,
                "preset_id": bounds[tags[t]].preset_id,
                "weight_min": lo,
                "weight_max": hi,
                "weight_min_raw": lo,
                "weight_max_raw": hi,
                "was_adjusted": False,
            }
            for t, lo, hi in universe
        ]
        return specs.insert_spec(
            spec_id=specs.new_spec_id(),
            user_id=user_id,
            profile_id=profile.profile_id,
            hardcap_version=hardcap,
            spec_version="0.1",
            name="백테스트 입력 테스트",
            input_prompt=None,
            rebalance=rebalance,
            signal_rules={},
            constraint={},
            universe=rows,
        ).spec_id

    yield _make

    with engine.begin() as conn:
        mine = "(SELECT spec_id FROM strategy_specs WHERE user_id = :u)"
        conn.execute(text(f"DELETE FROM spec_universe WHERE spec_id IN {mine}"), {"u": user_id})
        conn.execute(text("DELETE FROM strategy_specs WHERE user_id = :u"), {"u": user_id})
        conn.execute(text("DELETE FROM risk_profiles WHERE user_id = :u"), {"u": user_id})


@pytest.fixture
def unpriced_etf(engine):
    """가격이 한 행도 없는 종목 하나를 잠깐 넣는다."""
    ticker = f"TEST_{uuid.uuid4().hex[:6].upper()}"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO etf_master (ticker, name, group_id, risk_tag,"
                " asset_group_id, sector_group_id, country_group_id)"
                " VALUES (:t, :t, 'EQUITY', 'G3', 'EQUITY', 'SECTOR_OTHER', 'COUNTRY_KR')"
            ),
            {"t": ticker},
        )
    yield ticker
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM spec_universe WHERE ticker = :t"), {"t": ticker})
        conn.execute(text("DELETE FROM etf_master WHERE ticker = :t"), {"t": ticker})


# ── 데모 7종목과 같은 형태 ───────────────────────────────────────────


@pytest.mark.requires_backfill
def test_demo_spec_assembles_into_the_script_shape(make_spec):
    got = _build(make_spec(DEMO_UNIVERSE, MONTHLY_FIRST))

    assert got.risk_level == 4
    assert [h["ticker"] for h in got.holdings] == sorted(t for t, _lo, _hi in DEMO_UNIVERSE)
    for h in got.holdings:
        assert set(h) == HOLDING_KEYS
    raw = {t: (lo, hi) for t, lo, hi in DEMO_UNIVERSE}
    assert {h["ticker"]: (h["min_raw"], h["max_raw"]) for h in got.holdings} == raw

    wide = got.prices_wide
    assert all(isinstance(d, str) and len(d) == 10 and d[4] == "-" for d in wide.index)
    assert wide.index.is_monotonic_increasing
    assert wide.index[0] < "2023-01-01"  # 앞 이력은 워밍업으로 남는다
    assert not wide.reindex(got.valuation_dates).isna().any().any()  # ffill 로 평가 시점에 빈칸이 없다

    market_dates = [d for d in wide[bi.MARKET_TICKER].dropna().index if d >= "2023-01-01"]
    assert got.valuation_dates == weekly_dates(market_dates)
    assert got.rebalance_dates == monthly_first(got.valuation_dates)
    assert got.skipped_rebalance_dates == []  # 월초 간격은 27일 이상이라 20일에 걸리지 않는다
    assert got.data_snapshot_asof == got.valuation_dates[-1] <= PERIOD_END.isoformat()


@pytest.mark.requires_backfill
def test_demo_spec_matches_the_script_inputs_built_from_csv(make_spec):
    # 스크립트와 같은 방법으로 data/ CSV 에서 만든 입력과 값까지 같아야 한다.
    if not (SRC / "prices.csv").exists():
        pytest.skip(f"{SRC} 가 없다 — -w /repo/backend 로 돌려야 한다")
    got = _build(make_spec(DEMO_UNIVERSE, MONTHLY_FIRST))

    uni = pd.read_csv(SRC / "universe.csv", dtype={"ticker": str}).set_index("ticker")
    prices = pd.read_csv(SRC / "prices.csv", dtype={"ticker": str}, parse_dates=["date"])
    wide = prices.pivot(index="date", columns="ticker", values="close").ffill()
    wide.index = wide.index.strftime("%Y-%m-%d")
    weekly = weekly_dates([d for d in wide[bi.MARKET_TICKER].dropna().index.tolist() if d >= "2023-01-01"])

    assert got.valuation_dates == weekly
    assert got.rebalance_dates == monthly_first(weekly)
    for h in got.holdings:
        row = uni.loc[h["ticker"]]
        assert (h["grade"], h["asset_group"], h["sector_group"], h["country_group"]) == (
            row["risk_tag"], row["asset_group"], row["sector_group"], row["country_group"]
        )
    tickers = [h["ticker"] for h in got.holdings]
    pd.testing.assert_frame_equal(
        got.prices_wide.reindex(weekly)[tickers], wide.reindex(weekly)[tickers], check_names=False
    )


@pytest.mark.requires_backfill
def test_weekly_rule_skips_dates_closer_than_min_interval(make_spec):
    weekly_rule = {"trigger": {"type": "calendar", "freq": "weekly", "day": 1}, "min_interval_days": 5}
    got = _build(make_spec(DEMO_UNIVERSE, weekly_rule))

    # 러너의 주간 구분이 해를 넘는 주를 둘로 나눠 연말에 3~4일 간격이 생긴다.
    assert got.skipped_rebalance_dates == ["2024-12-30", "2025-12-30"]
    assert set(got.rebalance_dates) | set(got.skipped_rebalance_dates) == set(got.valuation_dates)


# ── 멈추는 경우 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "rebalance",
    [
        {"trigger": {"type": "calendar", "freq": "monthly", "day": 15}, "min_interval_days": 20},
        {"trigger": {"type": "calendar", "freq": "quarterly", "day": 1}, "min_interval_days": 60},
        {"trigger": {"type": "threshold", "freq": "daily"}, "min_interval_days": 5},
        {"trigger": {"type": "signal", "freq": "daily"}, "min_interval_days": 5},
        {"trigger": {"type": "calendar", "freq": "monthly", "day": 1}},  # 최소 간격 없음
    ],
)
def test_unsupported_rebalance_rule_stops_without_guessing(make_spec, rebalance):
    spec_id = make_spec(DEMO_UNIVERSE[:1], rebalance)

    with pytest.raises(bi.InputError, match="지원하지 않는 리밸런싱 규칙"):
        _build(spec_id)


def test_ticker_without_any_price_stops_and_is_named(make_spec, unpriced_etf):
    spec_id = make_spec([(unpriced_etf, 0.0, 0.3)], MONTHLY_FIRST)

    with pytest.raises(bi.InputError, match=unpriced_etf):
        _build(spec_id)


def test_unknown_spec_and_reversed_period_stop():
    with pytest.raises(bi.InputError, match="전략서가 없다"):
        _build("STR-없는전략서")
    with pytest.raises(bi.InputError, match="거꾸로"):
        bi.build_inputs("STR-없는전략서", period_start=date(2025, 1, 1), period_end=date(2024, 1, 1))


def test_min_interval_keeps_the_first_and_drops_what_is_too_close():
    kept, skipped = bi._apply_min_interval(["2024-12-27", "2024-12-30", "2025-01-03", "2025-01-10"], 5)

    assert kept == ["2024-12-27", "2025-01-03", "2025-01-10"]
    assert skipped == ["2024-12-30"]  # 건너뛴 뒤 간격은 남긴 날짜부터 다시 잰다
