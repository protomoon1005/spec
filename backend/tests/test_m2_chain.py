"""백테스트가 쓸 Validator 확정값 고르기 — app/backtest/m2_chain.resolve_policy.

표는 m2_chain 머리 주석에 있다. approved 이후는 저장값, draft 는 기록된 통과 검증 → 없으면 메모리
재검증, 막히면 실패(FALLBACK_WHEN_BLOCKED 를 켜면 러너 기본 경로). pandas 없이 돈다 — 기록된 검증만 읽는
경우는 가격이 필요 없고, 메모리 재검증은 2단 가격 대조가 실가격을 읽으므로 requires_backfill 이다.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from app.backtest import m2_chain
from app.repositories import presets, profiles, specs

AS_OF = date(2025, 12, 31)
MONTHLY = {"trigger": {"type": "calendar", "freq": "monthly", "day": 1}, "min_interval_days": 20}
WEEKLY = {"trigger": {"type": "calendar", "freq": "weekly", "day": 1}, "min_interval_days": 5}
CONSTRAINT = {
    "max_weight_per_asset": 0.30,
    "min_weight_per_asset": 0.00,
    "cash_min": 0.05,
    "max_loss_per_trade": 0.04,
    "max_drawdown": 0.28,
}
SIGNAL_RULES = {"market_analysis": {"indicators": ["ret_20"]}}
TWO = [("069500", 0.05, 0.20), ("273130", 0.10, 0.25)]


@pytest.fixture
def make_spec(engine, make_user):
    """(universe, rebalance) -> spec_id. 성향 4 draft 전략서."""
    _email, user_id = make_user("retail")
    version = presets.active_preset_version()
    profile = profiles.insert_profile(
        user_id=user_id, risk_level=4, preset_version=version,
        defaults=profiles.get_profile_defaults(version, 4), provenance={},
    )
    bounds = presets.get_asset_bounds(4)

    def _make(universe, rebalance=MONTHLY) -> str:
        with engine.connect() as conn:
            tags = dict(conn.execute(
                text("SELECT ticker, risk_tag FROM etf_master WHERE ticker = ANY(:t)"),
                {"t": [t for t, _lo, _hi in universe]},
            ).all())
        return specs.insert_spec(
            spec_id=specs.new_spec_id(), user_id=user_id, profile_id=profile.profile_id,
            hardcap_version=presets.get_active_hardcap()["hardcap_version"], spec_version="0.1",
            name="확정값 고르기 테스트", input_prompt=None, rebalance=rebalance,
            signal_rules=SIGNAL_RULES, constraint=CONSTRAINT,
            universe=[
                {"ticker": t, "preset_id": bounds[tags[t]].preset_id, "weight_min": lo, "weight_max": hi,
                 "weight_min_raw": lo, "weight_max_raw": hi, "was_adjusted": False}
                for t, lo, hi in universe
            ],
        ).spec_id

    yield _make

    with engine.begin() as conn:
        mine = "(SELECT spec_id FROM strategy_specs WHERE user_id = :u)"
        conn.execute(text(f"DELETE FROM validation_logs WHERE spec_id IN {mine}"), {"u": user_id})
        conn.execute(text(f"DELETE FROM spec_universe WHERE spec_id IN {mine}"), {"u": user_id})
        conn.execute(text("DELETE FROM strategy_specs WHERE user_id = :u"), {"u": user_id})
        conn.execute(text("DELETE FROM risk_profiles WHERE user_id = :u"), {"u": user_id})


def _logs(*, passed: bool = True, cash_target: float = 0.12, clamped: list[dict] | None = None) -> list[dict]:
    """record_validation 에 넣을 4단 기록. 검증기를 돌리지 않고 결과 모양만 만든다."""
    return [
        {"stage": 1, "passed": True, "violations": []},
        {"stage": 2, "passed": True, "violations": []},
        {"stage": 3, "passed": True, "violations": [],
         "adjusted_bounds": {"case": "B", "cash_target": cash_target}},
        {"stage": 4, "passed": passed,
         "violations": [] if passed else [{"code": "LEVERAGE_NOT_ALLOWED", "message": "레버리지"}],
         "clamped_fields": clamped or []},
    ]


def _approve(engine, spec_id: str) -> None:
    # 승인 API(app/m2/approval.py)는 근거 실행이 있어야 해서 상태만 바꾼다.
    # 트리거는 approved 인 행의 UPDATE 만 막는다(draft → approved 는 된다).
    with engine.begin() as conn:
        conn.execute(text("UPDATE strategy_specs SET status = 'approved' WHERE spec_id = :s"), {"s": spec_id})


CONFIRMED = [
    {"ticker": "069500", "weight_min": 0.05, "weight_max": 0.30, "was_adjusted": True},
    {"ticker": "273130", "weight_min": 0.10, "weight_max": 0.25, "was_adjusted": False},
]
INTERVAL_CLAMP = {"field": "rebalance.min_interval_days", "requested": 5.0, "applied": 7.0,
                  "limit": "min_interval_days"}
LOSS_CLAMP = {"field": "constraint.max_loss_per_trade", "requested": 0.07, "applied": 0.05,
              "limit": "max_loss_per_trade"}


# ── 기록된 검증 (가격 불필요) ──────────────────────────────────────────


def test_recorded_pass_uses_confirmed_ranges_cash_interval_and_loss(make_spec):
    spec_id = make_spec(TWO, WEEKLY)
    specs.record_validation(spec_id, bounds=CONFIRMED, logs=_logs(cash_target=0.12, clamped=[INTERVAL_CLAMP]))

    choice = m2_chain.resolve_policy(spec_id, as_of=AS_OF)

    c = choice.confirmed
    assert c.source == "recorded"
    assert c.bounds == {"069500": (0.05, 0.30), "273130": (0.10, 0.25)}  # 확정값, _raw(0.20) 아님
    assert c.cash_target == 0.12
    assert c.min_interval_days == 7  # 4단 클램프 적용값
    assert c.max_loss_per_trade == 0.04  # 클램프 안 됐으면 전략서 제약값
    assert choice.validation["status"] == "passed" and choice.validation["source"] == "recorded"
    assert choice.validation["cash_target"] == 0.12 and choice.validation["min_interval_days"] == 7
    assert "checked_at" in choice.validation


def test_clamped_loss_limit_wins_over_the_requested_one(make_spec):
    spec_id = make_spec(TWO)
    specs.record_validation(spec_id, bounds=CONFIRMED, logs=_logs(clamped=[LOSS_CLAMP]))

    assert m2_chain.resolve_policy(spec_id, as_of=AS_OF).confirmed.max_loss_per_trade == 0.05


def test_recorded_block_stops_the_backtest(make_spec):
    # 검증을 통과한 명세만 집행한다(2026-10-21 결정) — 막힌 전략서는 백테스트도 돌지 않는다
    spec_id = make_spec(TWO)
    specs.record_validation(spec_id, bounds=None, logs=_logs(passed=False))

    blocked = "검증 4단에서 막힌 전략서는 백테스트하지 않는다.*: 레버리지"
    with pytest.raises(m2_chain.ChainError, match=blocked):
        m2_chain.resolve_policy(spec_id, as_of=AS_OF)


def test_block_policy_is_one_switch(make_spec, monkeypatch):
    # 되돌리면 예전 정책(c1f2501) — 확정값 없이 러너 기본 경로로 돈다
    monkeypatch.setattr(m2_chain, "FALLBACK_WHEN_BLOCKED", True)
    spec_id = make_spec(TWO)
    specs.record_validation(spec_id, bounds=None, logs=_logs(passed=False))

    choice = m2_chain.resolve_policy(spec_id, as_of=AS_OF)

    assert choice.confirmed is None
    assert choice.validation == {"status": "failed", "source": "recorded", "blocked_at": 4}


def test_latest_validation_wins(make_spec, engine):
    spec_id = make_spec(TWO)
    specs.record_validation(spec_id, bounds=None, logs=_logs(passed=False))
    specs.record_validation(spec_id, bounds=CONFIRMED, logs=_logs(cash_target=0.07))

    assert m2_chain.resolve_policy(spec_id, as_of=AS_OF).confirmed.cash_target == 0.07


def test_approved_reads_stored_values(make_spec, engine):
    spec_id = make_spec(TWO)
    specs.record_validation(spec_id, bounds=CONFIRMED, logs=_logs(cash_target=0.09))
    _approve(engine, spec_id)

    choice = m2_chain.resolve_policy(spec_id, as_of=AS_OF)

    assert choice.confirmed.source == "stored" and choice.validation["source"] == "stored"
    assert choice.confirmed.cash_target == 0.09


def test_approved_without_a_passing_record_stops(make_spec, engine):
    # 승인된 전략서에 통과 기록이 없는 건 정책 문제가 아니라 기록이 깨진 것이다 — 폴백하지 않는다
    spec_id = make_spec(TWO)
    _approve(engine, spec_id)

    with pytest.raises(m2_chain.ChainError, match="통과한 검증 기록이 없다"):
        m2_chain.resolve_policy(spec_id, as_of=AS_OF)


def test_unknown_spec_stops():
    with pytest.raises(m2_chain.ChainError, match="전략서가 없다"):
        m2_chain.resolve_policy("STR-없는전략서", as_of=AS_OF)


# ── 기록이 없으면 메모리에서 다시 검증한다 (2단이 실가격을 본다) ─────────────


@pytest.mark.requires_backfill
def test_unrecorded_draft_is_revalidated_in_memory_and_nothing_is_written(make_spec, engine):
    spec_id = make_spec(TWO)

    choice = m2_chain.resolve_policy(spec_id, as_of=AS_OF)

    assert choice.validation["status"] == "passed" and choice.validation["source"] == "revalidated"
    assert choice.confirmed.source == "revalidated"
    assert choice.confirmed.max_loss_per_trade == 0.04
    assert specs.latest_validation(spec_id) == []  # 기록하지 않는다
    rows = specs.get_spec_universe(spec_id)
    universe = {u["ticker"]: (float(u["weight_min"]), float(u["weight_max"])) for u in rows}
    assert universe == {"069500": (0.05, 0.20), "273130": (0.10, 0.25)}  # 확정값 칸도 그대로


@pytest.mark.requires_backfill
def test_unrecorded_blocked_draft_stops_the_backtest(make_spec):
    # 레버리지는 하드캡 v0.1 이 막는다(4단). 기록이 없어도 메모리 재검증에서 막히면 실패
    spec_id = make_spec([("069500", 0.05, 0.20), ("122630", 0.00, 0.10)])

    with pytest.raises(m2_chain.ChainError, match="검증 4단에서 막힌 전략서는 백테스트하지 않는다"):
        m2_chain.resolve_policy(spec_id, as_of=AS_OF)
