"""regime_snapshots 적재·조회 테스트. postgres 가 필요하다."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.repositories.regime import get_regime_snapshot, upsert_regime_snapshot
from app.views.regime.judge import judge

AS_OF = date(2026, 9, 10)
PANIC = {"VIX_CLOSE": 30.0, "CREDIT_SPREAD_BAA10Y": 2.30, "USDKRW_GAP60": 0.06, "TREND_GAP": -0.08}


@pytest.fixture
def trend_index(engine):
    generated = f"TEST_{uuid.uuid4().hex[:8].upper()}"
    yield generated
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM regime_snapshots WHERE trend_index = :t"), {"t": generated}
        )


def _store(trend_index: str, as_of: date, indicators: dict) -> None:
    result = judge(indicators)
    upsert_regime_snapshot(
        as_of=as_of,
        trend_index=trend_index,
        regime_label=result.label,
        intensity=result.intensity,
        threshold_state=result.threshold_state,
    )


def test_roundtrip_keeps_the_threshold_state(engine, trend_index) -> None:
    _store(trend_index, AS_OF, PANIC)
    snapshot = get_regime_snapshot(trend_index, as_of=AS_OF)

    assert snapshot is not None
    assert snapshot.regime_label == "risk_off"
    assert snapshot.intensity < 0
    assert snapshot.threshold_state["indicators"]["VIX_CLOSE"]["breached"] is True
    assert snapshot.threshold_state["threshold_set_version"].startswith("regime-v")


def test_future_snapshot_is_not_returned(engine, trend_index) -> None:
    _store(trend_index, AS_OF + timedelta(days=1), PANIC)
    assert get_regime_snapshot(trend_index, as_of=AS_OF) is None


def test_latest_snapshot_at_or_before_as_of_wins(engine, trend_index) -> None:
    _store(trend_index, AS_OF - timedelta(days=3), PANIC)
    _store(trend_index, AS_OF - timedelta(days=1), dict(PANIC, VIX_CLOSE=12.0))

    snapshot = get_regime_snapshot(trend_index, as_of=AS_OF)
    assert snapshot.as_of == AS_OF - timedelta(days=1)
    assert snapshot.threshold_state["indicators"]["VIX_CLOSE"]["value"] == pytest.approx(12.0)


def test_reingest_updates_in_place(engine, trend_index) -> None:
    # 거시지표가 개정되면 그날의 국면 판정도 바뀐다.
    _store(trend_index, AS_OF, PANIC)
    _store(trend_index, AS_OF, {"VIX_CLOSE": 13.0, "CREDIT_SPREAD_BAA10Y": 1.5,
                                "USDKRW_GAP60": -0.01, "TREND_GAP": 0.04})

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM regime_snapshots WHERE trend_index = :t"),
            {"t": trend_index},
        ).scalar_one()

    assert count == 1
    assert get_regime_snapshot(trend_index, as_of=AS_OF).regime_label == "risk_on"


def test_unknown_label_is_storable(engine, trend_index) -> None:
    _store(trend_index, AS_OF, {"VIX_CLOSE": 40.0})
    snapshot = get_regime_snapshot(trend_index, as_of=AS_OF)

    assert snapshot.regime_label == "unknown"
    assert snapshot.intensity == 0.0
