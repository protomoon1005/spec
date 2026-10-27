"""4단 Validator — DB 왕복과 API (POST /specs/{spec_id}/validate).

postgres 와 시드가 적재된 상태여야 한다. 시험용 종목과 합성 일봉은 픽스처가 넣고 지운다
(실제 백필 데이터에 기대지 않는다). 전략서는 M1 을 거치지 않고 직접 저장한다 —
M1 이 막는 조합(안정투자형 + G2 등)도 넣어 봐야 하기 때문이다.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.backtest.service import DEFAULT_PERIOD_START
from app.m2.stages import FAILED, NOT_RUN, PASSED, Stages
from app.m2.validator import validate
from app.repositories import presets, profiles, specs
from app.repositories import price_daily as price_repo

FALLBACK_END = date(2025, 12, 30)


# ── 시험용 종목과 일봉 ────────────────────────────────────────────────


def _weekdays(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _path(points: list[tuple[float, float]], n: int) -> list[float]:
    """(구간 위치 0~1, 가격) 꼭짓점을 잇는 꺾은선. 꼭짓점 사이가 단조라 최대낙폭이 정확히 정해진다."""
    out = []
    for i in range(n):
        x = i / (n - 1)
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if x0 <= x <= x1:
                out.append(y0 + (y1 - y0) * (x - x0) / (x1 - x0))
                break
    return out


# 최대낙폭 40% · 30% · 5%
CRASH40 = [(0, 100.0), (0.3, 120.0), (0.5, 72.0), (1, 100.0)]
CRASH30 = [(0, 100.0), (0.3, 110.0), (0.5, 77.0), (1, 100.0)]
CALM = [(0, 100.0), (0.4, 102.0), (0.5, 96.9), (1, 105.0)]


@pytest.fixture(scope="module")
def world(engine):
    """시험용 종목 7개. {역할: 종목코드} 와 {종목코드: 위험등급} 을 돌려준다."""
    suffix = uuid.uuid4().hex[:4].upper()
    end = price_repo.get_latest_trade_date() or FALLBACK_END
    days = _weekdays(DEFAULT_PERIOD_START, end)

    # 역할, 위험등급, 레버리지, 활성, 일봉 경로, 일봉 끝
    spec_rows = {
        "eq_a": ("G3", False, True, CRASH40, end),
        "eq_b": ("G3", False, True, CRASH30, end),
        "bond": ("G5", False, True, CALM, end),
        "semi": ("G2", False, True, CRASH30, end),
        "lev": ("G1", True, True, CRASH40, end),
        "delisted": ("G3", False, False, None, None),
        "stale": ("G3", False, True, CRASH30, date(2024, 6, 28)),
    }
    tickers = {role: f"T{suffix}{i}" for i, role in enumerate(spec_rows)}
    tags = {}
    with engine.begin() as conn:
        for role, (tag, lev, active, _, _) in spec_rows.items():
            conn.execute(
                text(
                    "INSERT INTO etf_master"
                    " (ticker, name, sector, group_id, risk_tag, is_leveraged, active, delisted_date)"
                    " VALUES (:t, :n, 'SECTOR_OTHER', 'EQUITY', :tag, :lev, :active, :dl)"
                ),
                {
                    "t": tickers[role],
                    "n": f"검증 시험 {role} {suffix}",
                    "tag": tag,
                    "lev": lev,
                    "active": active,
                    "dl": None if active else date(2025, 6, 30),
                },
            )
            tags[tickers[role]] = tag
    for role, (_, _, _, points, last) in spec_rows.items():
        if points is None:
            continue
        span = [d for d in days if d <= last]
        closes = _path(points, len(span))
        price_repo.upsert_price_bars(
            tickers[role], bars=[{"trade_date": d, "close": c} for d, c in zip(span, closes)]
        )

    yield {"t": tickers, "tags": tags}

    with engine.begin() as conn:
        mine = list(tickers.values())
        conn.execute(text("DELETE FROM price_daily WHERE ticker = ANY(:t)"), {"t": mine})
        conn.execute(text("DELETE FROM etf_master WHERE ticker = ANY(:t)"), {"t": mine})


@pytest.fixture
def make_profile(engine, make_user):
    """성향 등급을 받아 (email, ProfileRecord) 를 만든다. 만든 전략서까지 뒤에서 지운다."""
    users = []

    def _make(level: int):
        email, user_id = make_user("retail")
        version = profiles.active_preset_version()
        record = profiles.insert_profile(
            user_id=user_id,
            risk_level=level,
            preset_version=version,
            defaults=profiles.get_profile_defaults(version, level),
            provenance={},
        )
        users.append(user_id)
        return email, record

    yield _make

    with engine.begin() as conn:
        for user_id in users:
            mine = "(SELECT spec_id FROM strategy_specs WHERE user_id = :u)"
            for table in ("spec_universe", "validation_logs"):
                conn.execute(text(f"DELETE FROM {table} WHERE spec_id IN {mine}"), {"u": user_id})
            conn.execute(text("DELETE FROM strategy_specs WHERE user_id = :u"), {"u": user_id})
            conn.execute(text("DELETE FROM risk_profiles WHERE user_id = :u"), {"u": user_id})


def _save(
    world, profile, items, *, cash_min=0.05, max_drawdown=0.25, min_interval=20, trigger_type="calendar"
) -> str:
    """items: [(역할, weight_min_raw, weight_max_raw)]. M1 을 거치지 않고 바로 저장한다."""
    bounds = presets.get_asset_bounds(profile.risk_level, preset_version=profile.preset_version)
    hardcap = presets.get_active_hardcap()
    universe = []
    for role, lo, hi in items:
        ticker = world["t"][role]
        universe.append(
            {
                "ticker": ticker,
                "preset_id": bounds[world["tags"][ticker]].preset_id,
                "weight_min": lo,
                "weight_max": hi,
                "weight_min_raw": lo,
                "weight_max_raw": hi,
                "was_adjusted": False,
            }
        )
    return specs.insert_spec(
        spec_id=specs.new_spec_id(),
        user_id=profile.user_id,
        profile_id=profile.profile_id,
        hardcap_version=hardcap["hardcap_version"],
        spec_version="0.1",
        name="검증 시험 전략",
        input_prompt="x",
        rebalance={
            "trigger": {"type": trigger_type, "freq": "monthly", "day": 1},
            "min_interval_days": min_interval,
        },
        signal_rules={"market_analysis": {"indicators": ["ret_20"]}},
        constraint={
            "max_weight_per_asset": 0.30,
            "min_weight_per_asset": 0.00,
            "cash_min": cash_min,
            "max_loss_per_trade": 0.05,
            "max_drawdown": max_drawdown,
        },
        universe=universe,
    ).spec_id


def _as_of() -> date:
    return price_repo.get_latest_trade_date()


def _codes(result, stage: int) -> set[str]:
    return {v.code for v in result.stages[stage - 1].violations}


def _universe_rows(spec_id: str) -> list[tuple]:
    return [
        (
            u["ticker"],
            float(u["weight_min"]),
            float(u["weight_max"]),
            float(u["weight_min_raw"]),
            float(u["weight_max_raw"]),
            u["was_adjusted"],
        )
        for u in specs.get_spec_universe(spec_id)
    ]


def _log_count(engine, spec_id: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM validation_logs WHERE spec_id = :s"), {"s": spec_id}
        ).scalar_one()


# ── 2. 상장폐지 종목 → 2단 차단 (실패 장면 3) ─────────────────────────


def test_delisted_blocked_at_reference(world, make_profile):
    _, profile = make_profile(5)
    spec_id = _save(world, profile, [("eq_a", 0.0, 0.30), ("delisted", 0.0, 0.30), ("bond", 0.0, 0.30)])

    result = validate(spec_id, as_of=_as_of())

    assert not result.passed
    assert result.blocked_at == 2
    assert "DELISTED" in _codes(result, 2)
    assert result.regeneration.required
    assert any(r["code"] == "DELISTED" for r in result.regeneration.reasons)
    assert [s.status for s in result.stages] == [PASSED, FAILED, NOT_RUN, NOT_RUN]


def test_stale_prices_blocked_at_reference(world, make_profile):
    _, profile = make_profile(5)
    spec_id = _save(world, profile, [("eq_a", 0.0, 0.30), ("stale", 0.0, 0.30), ("bond", 0.0, 0.30)])

    result = validate(spec_id, as_of=_as_of())

    assert result.blocked_at == 2
    assert _codes(result, 2) == {"NO_PRICE_DATA"}


# ── 3. 안정투자형 + 반도체(G2) → 3단 차단 (실패 장면 1) ───────────────


def test_conservative_with_g2_blocked_at_logic(world, make_profile):
    _, profile = make_profile(1)
    spec_id = _save(
        world, profile, [("bond", 0.0, 0.30), ("semi", 0.0, 0.10)], cash_min=0.20, max_drawdown=0.10
    )

    result = validate(spec_id, as_of=_as_of())

    assert result.blocked_at == 3
    assert "PRESET_FORBIDDEN" in _codes(result, 3)
    assert not result.regeneration.required  # 논리 위반은 재생성 사유가 아니다
    assert [s.status for s in result.stages] == [PASSED, PASSED, FAILED, NOT_RUN]


# ── 4. 하드캡 클램프 ─────────────────────────────────────────────────


def test_hardcap_clamps_weight_and_drawdown(world, make_profile):
    _, profile = make_profile(5)
    spec_id = _save(
        world, profile, [("eq_a", 0.0, 0.40), ("eq_b", 0.0, 0.40), ("bond", 0.0, 0.40)], max_drawdown=0.35
    )

    result = validate(spec_id, as_of=_as_of())

    assert result.passed, [v.as_dict() for s in result.stages for v in s.violations]
    clamped = {c["field"]: c for c in result.stages[3].clamped_fields}
    eq_a = world["t"]["eq_a"]
    assert clamped[f"universe.{eq_a}.weight_max"]["requested"] == pytest.approx(0.40)
    assert clamped[f"universe.{eq_a}.weight_max"]["applied"] == pytest.approx(0.30)
    assert clamped["constraint.max_drawdown"]["requested"] == pytest.approx(0.35)
    assert clamped["constraint.max_drawdown"]["applied"] == pytest.approx(0.25)

    rows = {r[0]: r for r in _universe_rows(spec_id)}
    assert rows[eq_a][2] == pytest.approx(0.30)  # weight_max 확정
    assert rows[eq_a][4] == pytest.approx(0.40)  # weight_max_raw 보존
    assert rows[eq_a][5] is True


def test_leverage_is_violation_at_hardcap(world, make_profile):
    _, profile = make_profile(5)
    spec_id = _save(
        world, profile, [("lev", 0.0, 0.20), ("bond", 0.0, 0.30), ("eq_a", 0.0, 0.30)], max_drawdown=0.35
    )

    result = validate(spec_id, as_of=_as_of())

    assert result.blocked_at == 4
    assert "LEVERAGE_NOT_ALLOWED" in _codes(result, 4)


# ── 5. 낙폭 목표와 변동성 모순 → 3단 ──────────────────────────────────


def test_drawdown_target_contradiction(world, make_profile):
    _, profile = make_profile(5)
    # 가장 덜 빠진 종목도 30% 빠졌다: 0.95 × 0.30 = 0.285 > 목표 0.10
    spec_id = _save(world, profile, [("eq_a", 0.0, 0.30), ("eq_b", 0.0, 0.30)], max_drawdown=0.10)

    result = validate(spec_id, as_of=_as_of())

    assert result.blocked_at == 3
    assert "DRAWDOWN_INFEASIBLE" in _codes(result, 3)


def test_interval_shorter_than_hardcap_is_clamped(world, make_profile):
    _, profile = make_profile(5)
    spec_id = _save(
        world, profile, [("eq_a", 0.0, 0.30), ("bond", 0.0, 0.30)], max_drawdown=0.35, min_interval=1
    )

    result = validate(spec_id, as_of=_as_of())

    assert result.passed
    clamped = {c["field"]: c for c in result.stages[3].clamped_fields}
    assert clamped["rebalance.min_interval_days"] == {
        "field": "rebalance.min_interval_days",
        "requested": 1,
        "applied": 5,
        "limit": "min_interval_days",
    }


def test_no_rebalance_trigger_is_not_clamped(world, make_profile):
    _, profile = make_profile(5)
    spec_id = _save(
        world,
        profile,
        [("eq_a", 0.0, 0.30), ("bond", 0.0, 0.30)],
        max_drawdown=0.35,
        min_interval=0,
        trigger_type="none",
    )

    result = validate(spec_id, as_of=_as_of())

    assert result.passed
    assert "rebalance.min_interval_days" not in {c["field"] for c in result.stages[3].clamped_fields}


# ── 6. 두 번 검증해도 같다 ───────────────────────────────────────────


def test_validate_twice_same_result(world, make_profile):
    _, profile = make_profile(5)
    spec_id = _save(
        world, profile, [("eq_a", 0.05, 0.40), ("eq_b", 0.0, 0.20), ("bond", 0.10, 0.25)], max_drawdown=0.35
    )

    first = validate(spec_id, as_of=_as_of())
    rows_first = _universe_rows(spec_id)
    second = validate(spec_id, as_of=_as_of())

    assert first == second
    assert _universe_rows(spec_id) == rows_first
    assert first.stages[2].adjusted_bounds["case"] == "B"


# ── 7. 계층 끄기 ─────────────────────────────────────────────────────


def test_layer_off_lets_violation_through(engine, world, make_profile):
    _, profile = make_profile(5)
    delisted = _save(
        world, profile, [("eq_a", 0.0, 0.30), ("delisted", 0.0, 0.30), ("bond", 0.0, 0.30)], max_drawdown=0.35
    )
    contradiction = _save(world, profile, [("eq_a", 0.0, 0.30), ("eq_b", 0.0, 0.30)], max_drawdown=0.10)
    over_cap = _save(
        world, profile, [("eq_a", 0.0, 0.40), ("eq_b", 0.0, 0.40), ("bond", 0.0, 0.40)], max_drawdown=0.35
    )
    as_of = _as_of()

    assert validate(delisted, as_of=as_of, stages=Stages(reference=False)).blocked_at != 2
    assert validate(contradiction, as_of=as_of, stages=Stages(logic=False)).passed

    loose = validate(over_cap, as_of=as_of, stages=Stages(hardcap=False))
    assert loose.passed
    assert max(b.weight_max for b in loose.universe) == pytest.approx(0.40)

    # 끈 실행은 기록하지 않는다 — 실험 결과가 검증 이력이나 확정값에 섞이면 안 된다.
    for spec_id in (delisted, contradiction, over_cap):
        assert _log_count(engine, spec_id) == 0
    assert all(not r[5] for r in _universe_rows(over_cap))

    # 같은 전략서를 다 켜고 돌리면 막히거나 깎인다.
    assert validate(delisted, as_of=as_of).blocked_at == 2
    assert validate(contradiction, as_of=as_of).blocked_at == 3
    strict = validate(over_cap, as_of=as_of)
    assert max(b.weight_max for b in strict.universe) == pytest.approx(0.30)


# ── 8. API ───────────────────────────────────────────────────────────


def _auth(client, username: str) -> dict:
    token = client.post("/auth/login", json={"username": username}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_api_validate(engine, client, world, make_profile):
    email, profile = make_profile(5)
    other_email, other = make_profile(5)
    spec_id = _save(
        world, profile, [("eq_a", 0.0, 0.40), ("eq_b", 0.0, 0.40), ("bond", 0.0, 0.40)], max_drawdown=0.35
    )
    others = _save(world, other, [("eq_a", 0.0, 0.30), ("bond", 0.0, 0.30)])
    approved = _save(world, profile, [("eq_a", 0.0, 0.30), ("bond", 0.0, 0.30)])
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE strategy_specs SET status = 'approved' WHERE spec_id = :s"), {"s": approved}
        )

    headers = _auth(client, email)
    assert client.post(f"/specs/{others}/validate", headers=headers).status_code == 404
    assert client.post(f"/specs/{approved}/validate", headers=headers).status_code == 409
    assert client.post("/specs/STR-없는전략서/validate", headers=headers).status_code == 404

    res = client.post(f"/specs/{spec_id}/validate", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["spec_id"] == spec_id
    assert body["passed"] is True
    assert body["blocked_at"] is None
    assert body["regeneration"] == {"required": False, "reasons": []}
    assert [s["stage"] for s in body["stages"]] == [1, 2, 3, 4]
    assert [s["name"] for s in body["stages"]] == ["schema", "reference", "logic", "hardcap"]
    assert body["stages"][2]["adjusted_bounds"]["case"] == "B"
    assert any(c["limit"] == "max_weight_per_asset" for c in body["stages"][3]["clamped_fields"])
    eq_a = next(u for u in body["universe"] if u["ticker"] == world["t"]["eq_a"])
    assert eq_a["weight_max_raw"] == pytest.approx(0.40)
    assert eq_a["weight_max"] == pytest.approx(0.30)
    assert eq_a["was_adjusted"] is True
    assert _log_count(engine, spec_id) == 4
