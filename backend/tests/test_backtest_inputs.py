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

from app.repositories import specs

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
def make_spec(make_draft_spec):
    """(universe, rebalance) -> spec_id. 성향 4(스크립트 기본값)로 draft 전략서를 만든다."""
    def _make(universe, rebalance) -> str:
        return make_draft_spec(universe, rebalance=rebalance, name="백테스트 입력 테스트",
                               signal_rules={}, constraint={})

    return _make


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
        # BBL 에 없는 조합
        {"trigger": {"type": "calendar", "freq": "monthly", "day": 10}, "min_interval_days": 20},
        {"trigger": {"type": "calendar", "freq": "quarterly", "day": 15}, "min_interval_days": 60},
        {"trigger": {"type": "threshold", "freq": "weekly"}, "min_interval_days": 5},
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


# ── BBL 리밸런싱 블록별 일정 (DB 없음) ───────────────────────────────
# 블록의 params_schema 는 db/seeds/05_bbl.sql 그대로다.

def _rule(type_: str, freq: str, day: int | None, interval: int) -> dict:
    trigger = {"type": type_, "freq": freq} if day is None else {"type": type_, "freq": freq, "day": day}
    return {"trigger": trigger, "min_interval_days": interval}


BBL_RULES = {
    "RB_MONTHLY_FIRST": _rule("calendar", "monthly", 1, 20),
    "RB_MONTHLY_MID": _rule("calendar", "monthly", 15, 20),
    "RB_QUARTERLY": _rule("calendar", "quarterly", 1, 60),
    "RB_WEEKLY": _rule("calendar", "weekly", 1, 5),
    "RB_THRESHOLD_5": _rule("threshold", "daily", None, 5),
    "RB_SIGNAL": _rule("signal", "daily", None, 5),
}
# 2023-01~2023-07 의 주 마지막 거래일 일부(설 연휴·공휴일 주 포함 모양)
WEEKS = [
    "2023-01-06", "2023-01-13", "2023-01-20", "2023-01-27", "2023-02-03", "2023-02-10",
    "2023-02-17", "2023-02-24", "2023-03-03", "2023-03-10", "2023-03-17", "2023-03-24",
    "2023-03-31", "2023-04-07", "2023-04-14", "2023-04-21", "2023-04-28", "2023-05-04",
    "2023-05-12", "2023-05-19", "2023-05-26", "2023-06-02", "2023-06-09", "2023-06-16",
    "2023-06-23", "2023-06-30", "2023-07-07",
]


def test_bbl_seed_rebalance_blocks_match_the_rules_here():
    # 여기 표가 시드와 어긋나면 아래 일정 테스트가 다른 규칙을 보고 있는 것이다
    seed = (ROOT / "db" / "seeds" / "05_bbl.sql").read_text(encoding="utf-8")
    import json
    import re

    found = {
        m.group(1): json.loads(m.group(2))
        for m in re.finditer(r"\('(RB_[A-Z_0-9]+)', 'rebalance',.*?'(\{.*?\})'\)", seed, re.S)
    }
    assert set(found) == set(BBL_RULES)
    for block_id, rule in BBL_RULES.items():
        assert found[block_id] == rule, block_id


def test_monthly_mid_takes_the_first_week_on_or_after_the_15th():
    got = bi.plan_schedule(BBL_RULES["RB_MONTHLY_MID"], WEEKS)

    assert got.dates == ["2023-01-20", "2023-02-17", "2023-03-17", "2023-04-21", "2023-05-19", "2023-06-16"]
    assert got.skipped == [] and got.drift is None and "15일" in got.note


def test_monthly_mid_skips_a_month_that_ends_before_the_15th():
    # 구간이 달 중간에 끝나면 그 달은 리밸런싱이 없다(15일 전 날짜로 당기지 않는다)
    short = ["2023-01-20", "2023-02-03", "2023-02-10"]
    assert bi.plan_schedule(BBL_RULES["RB_MONTHLY_MID"], short).dates == ["2023-01-20"]


def test_quarterly_takes_the_first_week_of_each_quarter():
    got = bi.plan_schedule(BBL_RULES["RB_QUARTERLY"], WEEKS)

    assert got.dates == ["2023-01-06", "2023-04-07", "2023-07-07"]
    assert got.skipped == [] and got.drift is None and got.signal_interval is None


def test_quarterly_min_interval_drops_a_quarter_start_too_close():
    # 확정 간격이 분기보다 길면 다음 분기 첫 평가일을 건너뛴다
    got = bi.plan_schedule(BBL_RULES["RB_QUARTERLY"], WEEKS, min_interval_days=100)

    assert got.dates == ["2023-01-06", "2023-07-07"] and got.skipped == ["2023-04-07"]


def test_monthly_first_and_weekly_are_unchanged():
    assert bi.plan_schedule(BBL_RULES["RB_MONTHLY_FIRST"], WEEKS).dates == monthly_first(WEEKS)
    assert bi.plan_schedule(BBL_RULES["RB_WEEKLY"], WEEKS).dates == WEEKS


def test_threshold_hands_every_week_to_the_runner_with_the_block_threshold():
    got = bi.plan_schedule(BBL_RULES["RB_THRESHOLD_5"], WEEKS)

    # 간격은 미리 거르지 않는다 — 직전 "실제" 리밸런싱일을 러너만 안다
    assert got.dates == WEEKS and got.skipped == []
    assert got.drift == (0.05, 5) and got.signal_interval is None
    assert bi.plan_schedule(BBL_RULES["RB_THRESHOLD_5"], WEEKS, min_interval_days=9).drift == (0.05, 9)


def test_signal_hands_every_week_to_the_runner_with_the_interval():
    got = bi.plan_schedule(BBL_RULES["RB_SIGNAL"], WEEKS, min_interval_days=7)

    assert got.dates == WEEKS and got.skipped == []
    assert got.signal_interval == 7 and got.drift is None


def test_min_interval_keeps_the_first_and_drops_what_is_too_close():
    kept, skipped = bi._apply_min_interval(["2024-12-27", "2024-12-30", "2025-01-03", "2025-01-10"], 5)

    assert kept == ["2024-12-27", "2025-01-03", "2025-01-10"]
    assert skipped == ["2024-12-30"]  # 건너뛴 뒤 간격은 남긴 날짜부터 다시 잰다


# ── 검증 결과 쓰기 ────────────────────────────────────────────────────
# 어떤 확정값을 쓸지는 app/backtest/m2_chain.resolve_policy 가 정한다(tests/test_m2_chain.py).
# 여기서는 조립이 그 값(간격)을 받아 쓰는지와, 실행 기록용 확정 범위를 읽는지만 본다.

WEEKLY = {"trigger": {"type": "calendar", "freq": "weekly", "day": 1}, "min_interval_days": 5}
CONFIRMED = [
    {"ticker": "069500", "weight_min": 0.05, "weight_max": 0.30, "was_adjusted": True},
    {"ticker": "273130", "weight_min": 0.10, "weight_max": 0.25, "was_adjusted": False},
]


def _logs(*, cash_target: float = 0.12) -> list[dict]:
    """record_validation 에 넣을 4단 통과 기록. 검증기를 돌리지 않고 결과 모양만 만든다."""
    return [
        {"stage": 1, "passed": True, "violations": []},
        {"stage": 2, "passed": True, "violations": []},
        {"stage": 3, "passed": True, "violations": [],
         "adjusted_bounds": {"case": "B", "cash_target": cash_target}},
        {"stage": 4, "passed": True, "violations": [], "clamped_fields": []},
    ]


def test_final_ranges_read_the_confirmed_columns_and_holdings_keep_raw(make_spec):
    spec_id = make_spec([("069500", 0.05, 0.20), ("273130", 0.10, 0.25)], WEEKLY)
    assert _build(spec_id).final_ranges == {"069500": (0.05, 0.20), "273130": (0.10, 0.25)}  # 검증 전 = _raw

    specs.record_validation(spec_id, bounds=CONFIRMED, logs=_logs())
    inp = _build(spec_id)

    assert inp.final_ranges == {"069500": (0.05, 0.30), "273130": (0.10, 0.25)}
    # 원래 범위는 그대로 남는다(실행 기록의 weight_*_raw · 데모 스크립트와 같은 holdings 모양)
    raw = {h["ticker"]: (h["min_raw"], h["max_raw"]) for h in inp.holdings}
    assert raw["069500"] == (0.05, 0.20)


@pytest.mark.requires_backfill
def test_confirmed_interval_replaces_the_requested_one(make_spec):
    spec_id = make_spec([("069500", 0.05, 0.20)], WEEKLY)

    requested = _build(spec_id)
    clamped = bi.build_inputs(spec_id, period_start=PERIOD_START, period_end=PERIOD_END, min_interval_days=7)

    assert requested.skipped_rebalance_dates == ["2024-12-30", "2025-12-30"]
    # 간격 7일이 적용됐다 — 남은 리밸런싱일끼리는 7일 이상 떨어져 있다
    days = [date.fromisoformat(d) for d in clamped.rebalance_dates]
    assert all((b - a).days >= 7 for a, b in zip(days, days[1:]))
    assert len(clamped.rebalance_dates) < len(requested.rebalance_dates)


@pytest.mark.requires_backfill
def test_m2_chain_holds_targets_inside_confirmed_ranges_and_cash(make_spec):
    """확정 범위 · 현금을 M2 체인에 넘기면 러너가 그 안에서 비중을 낸다. 신호 없는 대조군으로 본다."""
    from app.backtest.m2_chain import AtrSource, ConfirmedPolicy, M2Weigher
    from app.backtest.policy import CAP_EXEMPT_GROUPS, caps_for
    from app.backtest.runner import run

    spec_id = make_spec([("069500", 0.05, 0.20), ("273130", 0.10, 0.25)], WEEKLY)
    inp = _build(spec_id)
    confirmed = ConfirmedPolicy(
        source="recorded", hardcap_version="v0.1",
        bounds={"069500": (0.02, 0.08), "273130": (0.03, 0.09)},
        cash_target=0.40, max_loss_per_trade=0.05, min_interval_days=5,
    )
    weigh = M2Weigher(
        inp.holdings, confirmed, caps=caps_for(inp.risk_level), exempt=CAP_EXEMPT_GROUPS, horizon_days=5,
        atr=AtrSource.load([h["ticker"] for h in inp.holdings], as_of=PERIOD_END),
    )

    out = run(
        inp.prices_wide, inp.holdings, inp.risk_level, inp.valuation_dates, inp.rebalance_dates,
        1_000_000, use_signals=False, weigh=weigh,
    )

    for decision in out["decisions"]:
        assert 0.02 - 1e-9 <= decision["target"]["069500"] <= 0.08 + 1e-9
        assert 0.03 - 1e-9 <= decision["target"]["273130"] <= 0.09 + 1e-9
        assert decision["cash"] >= 0.40 - 1e-9


# ── 신호형(RB_SIGNAL) 전략서를 service 경로로 끝까지 ─────────────────────


@pytest.mark.requires_backfill
def test_signal_rule_spec_runs_through_the_m2_chain_and_repeats(make_draft_spec, monkeypatch):
    from app.backtest import service
    from app.views import bridge

    monkeypatch.setattr(bridge, "SCORERS", bridge.MOCK_SCORERS)  # LightGBM 없이 결정적으로
    spec_id = make_draft_spec(
        [("069500", 0.05, 0.20), ("273130", 0.10, 0.25)], rebalance=BBL_RULES["RB_SIGNAL"],
        name="신호형 픽스처", signal_rules={"market_analysis": {"indicators": ["ret_20"]}},
        constraint={"max_weight_per_asset": 0.30, "min_weight_per_asset": 0.0, "cash_min": 0.05,
                    "max_loss_per_trade": 0.05, "max_drawdown": 0.25},
    )
    specs.record_validation(spec_id, bounds=CONFIRMED, logs=_logs())  # 4단 통과 기록 → M2 체인 경로

    def once():
        return service.run_spec_backtest(spec_id, period_start=PERIOD_START, period_end=PERIOD_END,
                                         seed_money=10_000_000)

    first, second = once(), once()
    w = first["window_results"]

    assert w["weight_path"] == service.M2_CHAIN_PATH
    assert w["m2"]["risk_sizer"]["horizon_days"] == w["m2"]["min_interval_days"]  # 신호형 h = 최소 간격
    dates = w["schedule"]["rebalance_dates"]
    assert dates == [d["date"] for d in w["decisions"]] and len(dates) >= 2
    # 대조군은 전략 계열이 리밸런싱한 날짜를 그대로 쓴다
    assert [d["date"] for d in w["control_decisions"]] == dates
    assert first == second
