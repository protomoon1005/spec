"""Validator — DB 왕복과 API (POST /specs/{spec_id}/validate).

postgres 와 시드가 적재된 상태여야 한다. 시험용 종목과 합성 일봉은 픽스처가 넣고 지운다
(실제 백필 데이터에 기대지 않는다). 전략서는 M1 을 거치지 않고 직접 저장한다 —
M1 이 막는 조합(상장폐지 종목 등)도 넣어 봐야 하기 때문이다.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.backtest.service import DEFAULT_PERIOD_START
from app.m2.stages import FAILED, NOT_RUN, PASSED, Stages
from app.m2.validator import validate
from app.repositories import price_daily as price_repo
from app.repositories import specs

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
    """시험용 종목 7개. {역할: 종목코드} 를 돌려준다."""
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
    for role, (_, _, _, points, last) in spec_rows.items():
        if points is None:
            continue
        span = [d for d in days if d <= last]
        closes = _path(points, len(span))
        price_repo.upsert_price_bars(
            tickers[role], bars=[{"trade_date": d, "close": c} for d, c in zip(span, closes)]
        )

    yield {"t": tickers}

    with engine.begin() as conn:
        mine = list(tickers.values())
        conn.execute(text("DELETE FROM price_daily WHERE ticker = ANY(:t)"), {"t": mine})
        conn.execute(text("DELETE FROM etf_master WHERE ticker = ANY(:t)"), {"t": mine})


@pytest.fixture
def make_owner(engine, make_user):
    """() -> (email, user_id). 만든 전략서까지 뒤에서 지운다."""
    users = []

    def _make():
        email, user_id = make_user()
        users.append(user_id)
        return email, user_id

    yield _make

    with engine.begin() as conn:
        for user_id in users:
            mine = "(SELECT spec_id FROM strategy_specs WHERE user_id = :u)"
            for table in ("spec_universe", "validation_logs"):
                conn.execute(text(f"DELETE FROM {table} WHERE spec_id IN {mine}"), {"u": user_id})
            conn.execute(text("DELETE FROM strategy_specs WHERE user_id = :u"), {"u": user_id})


def _save(
    world, user_id, items, *, cash_min=0.05, max_drawdown=0.25, min_interval=20, trigger_type="calendar"
) -> str:
    """items: [(역할, weight_min_raw, weight_max_raw)]. M1 을 거치지 않고 바로 저장한다."""
    universe = []
    for role, lo, hi in items:
        universe.append(
            {
                "ticker": world["t"][role],
                "weight_min": lo,
                "weight_max": hi,
                "weight_min_raw": lo,
                "weight_max_raw": hi,
                "was_adjusted": False,
            }
        )
    return specs.insert_spec(
        spec_id=specs.new_spec_id(),
        user_id=user_id,
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


def test_delisted_blocked_at_reference(world, make_owner):
    _, owner = make_owner()
    spec_id = _save(world, owner, [("eq_a", 0.0, 0.30), ("delisted", 0.0, 0.30), ("bond", 0.0, 0.30)])

    result = validate(spec_id, as_of=_as_of())

    assert not result.passed
    assert result.blocked_at == 2
    assert "DELISTED" in _codes(result, 2)
    assert result.regeneration.required
    assert any(r["code"] == "DELISTED" for r in result.regeneration.reasons)
    assert [s.status for s in result.stages] == [PASSED, FAILED, NOT_RUN]


def test_stale_prices_blocked_at_reference(world, make_owner):
    _, owner = make_owner()
    spec_id = _save(world, owner, [("eq_a", 0.0, 0.30), ("stale", 0.0, 0.30), ("bond", 0.0, 0.30)])

    result = validate(spec_id, as_of=_as_of())

    assert result.blocked_at == 2
    assert _codes(result, 2) == {"NO_PRICE_DATA"}


# ── 3. 레버리지는 막지 않는다 ───────────────────────────────────────


def test_leveraged_ticker_passes(world, make_owner):
    """시스템 상한이 없다(주제 변경 결정 3). 레버리지 종목도 통과한다."""
    _, owner = make_owner()
    spec_id = _save(
        world, owner, [("lev", 0.0, 0.20), ("bond", 0.0, 0.30), ("eq_a", 0.0, 0.30)], max_drawdown=0.50
    )

    result = validate(spec_id, as_of=_as_of())

    assert result.passed, [v.as_dict() for s in result.stages for v in s.violations]
    assert [s.status for s in result.stages] == [PASSED, PASSED, PASSED]


# ── 4. 낙폭 목표와 변동성 모순 → 3단 ──────────────────────────────────


def test_drawdown_target_contradiction(world, make_owner):
    _, owner = make_owner()
    # 가장 덜 빠진 종목도 30% 빠졌다: 0.95 × 0.30 = 0.285 > 목표 0.10
    spec_id = _save(world, owner, [("eq_a", 0.0, 0.30), ("eq_b", 0.0, 0.30)], max_drawdown=0.10)

    result = validate(spec_id, as_of=_as_of())

    assert result.blocked_at == 3
    assert "DRAWDOWN_INFEASIBLE" in _codes(result, 3)


# ── 5. 두 번 검증해도 같다 ───────────────────────────────────────────


def test_validate_twice_same_result(world, make_owner):
    _, owner = make_owner()
    spec_id = _save(
        world, owner, [("eq_a", 0.05, 0.40), ("eq_b", 0.0, 0.20), ("bond", 0.10, 0.25)], max_drawdown=0.35
    )

    first = validate(spec_id, as_of=_as_of())
    rows_first = _universe_rows(spec_id)
    second = validate(spec_id, as_of=_as_of())

    assert first == second
    assert _universe_rows(spec_id) == rows_first
    assert first.stages[2].adjusted_bounds["case"] == "B"


# ── 6. 계층 끄기 ─────────────────────────────────────────────────────


def test_layer_off_lets_violation_through(engine, world, make_owner):
    _, owner = make_owner()
    delisted = _save(
        world, owner, [("eq_a", 0.0, 0.30), ("delisted", 0.0, 0.30), ("bond", 0.0, 0.30)], max_drawdown=0.35
    )
    contradiction = _save(world, owner, [("eq_a", 0.0, 0.30), ("eq_b", 0.0, 0.30)], max_drawdown=0.10)
    as_of = _as_of()

    assert validate(delisted, as_of=as_of, stages=Stages(reference=False)).blocked_at != 2
    assert validate(contradiction, as_of=as_of, stages=Stages(logic=False)).passed

    # 끈 실행은 기록하지 않는다 — 실험 결과가 검증 이력이나 확정값에 섞이면 안 된다.
    for spec_id in (delisted, contradiction):
        assert _log_count(engine, spec_id) == 0

    # 같은 전략서를 다 켜고 돌리면 막힌다.
    assert validate(delisted, as_of=as_of).blocked_at == 2
    assert validate(contradiction, as_of=as_of).blocked_at == 3


# ── 7. API ───────────────────────────────────────────────────────────


def _auth(client, username: str) -> dict:
    token = client.post("/auth/login", json={"username": username}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_api_validate(engine, client, world, make_owner):
    email, owner = make_owner()
    _, other = make_owner()
    # 상한 합 0.60 < 투자 가능 0.95 — 범위 보정 case B 로 상한이 늘어난다
    spec_id = _save(
        world, owner, [("eq_a", 0.0, 0.20), ("eq_b", 0.0, 0.20), ("bond", 0.0, 0.20)], max_drawdown=0.35
    )
    others = _save(world, other, [("eq_a", 0.0, 0.30), ("bond", 0.0, 0.30)])
    approved = _save(world, owner, [("eq_a", 0.0, 0.30), ("bond", 0.0, 0.30)])
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
    assert [s["stage"] for s in body["stages"]] == [1, 2, 3]
    assert [s["name"] for s in body["stages"]] == ["schema", "reference", "logic"]
    assert body["stages"][2]["adjusted_bounds"]["case"] == "B"
    eq_a = next(u for u in body["universe"] if u["ticker"] == world["t"]["eq_a"])
    assert eq_a["weight_max_raw"] == pytest.approx(0.20)
    assert eq_a["weight_max"] > 0.20
    assert eq_a["was_adjusted"] is True
    assert _log_count(engine, spec_id) == 3
