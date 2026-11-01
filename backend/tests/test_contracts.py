"""app/contracts/ 전략서 계약(Spec v0.1) 테스트."""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.contracts.schema_export import SPEC_SCHEMA_PATH, build_spec_schema
from app.contracts.spec import (
    ConstraintSpec,
    MarketAnalysisRule,
    MarketTemperatureRule,
    RebalanceRule,
    SentimentRule,
    SignalRules,
    SpecV0_1,
    TriggerSpec,
    UniverseItem,
)

AS_OF = date(2026, 9, 9)


# ---------------------------------------------------------------------------
# 계약 ① Spec JSON 스키마
# ---------------------------------------------------------------------------


def _sample_spec() -> SpecV0_1:
    return SpecV0_1(
        spec_id="spec-1",
        spec_version="v0.1",
        user_id=1,
        name="테스트 전략",
        created_at=datetime(2026, 9, 9, 12, 0, 0),
        universe=[UniverseItem(ticker="069500", name="KODEX 200", weight_min=0.0, weight_max=0.4)],
        rebalance=RebalanceRule(
            trigger=TriggerSpec(type="calendar", freq="monthly", day=1),
            min_interval_days=7,
        ),
        signal_rules=SignalRules(
            market_analysis=MarketAnalysisRule(indicators=["rsi_14"]),
            sentiment=SentimentRule(target_sectors=["반도체"], lookback_hours=24),
            market_temperature=MarketTemperatureRule(
                trend_index="KOSPI_TREND", trend_ma_window=20, volatility_index="VKOSPI"
            ),
        ),
        constraint=ConstraintSpec(
            max_weight_per_asset=0.40,
            min_weight_per_asset=0.00,
            cash_min=0.02,
            max_loss_per_trade=0.05,
            max_drawdown=0.20,
        ),
    )


def test_spec_v0_1_round_trips_through_json():
    spec = _sample_spec()
    restored = SpecV0_1.model_validate_json(spec.model_dump_json())
    assert restored == spec


def test_spec_v0_1_rejects_unknown_top_level_field():
    """objective/reproducibility 같은 정본에 없는 블록은 거부되어야 한다 (extra=forbid)."""
    payload = json.loads(_sample_spec().model_dump_json())
    payload["objective"] = {"target_return": 0.1}

    with pytest.raises(ValidationError):
        SpecV0_1.model_validate(payload)


def test_spec_v0_1_rejects_weight_outside_unit_interval():
    payload = json.loads(_sample_spec().model_dump_json())
    payload["universe"][0]["weight_max"] = 1.5

    with pytest.raises(ValidationError):
        SpecV0_1.model_validate(payload)


def test_spec_v0_1_rejects_free_form_rebalance_trigger():
    """rebalance.trigger는 더 이상 자유 문자열이 아니다 — type/freq/day 구조가 강제된다."""
    payload = json.loads(_sample_spec().model_dump_json())
    payload["rebalance"]["trigger"] = "monthly"  # 예전 자유 문자열 형태

    with pytest.raises(ValidationError):
        SpecV0_1.model_validate(payload)


def test_spec_v0_1_requires_sentiment_lookback_hours():
    payload = json.loads(_sample_spec().model_dump_json())
    del payload["signal_rules"]["sentiment"]["lookback_hours"]

    with pytest.raises(ValidationError):
        SpecV0_1.model_validate(payload)


def test_spec_v0_1_allows_omitting_optional_signal_rule_blocks():
    """감성·시장온도 관점은 선택 범위라(그래프 상위 CLAUDE.md) 블록 자체는 생략할 수 있다."""
    payload = json.loads(_sample_spec().model_dump_json())
    payload["signal_rules"]["sentiment"] = None
    payload["signal_rules"]["market_temperature"] = None

    restored = SpecV0_1.model_validate(payload)

    assert restored.signal_rules.sentiment is None
    assert restored.signal_rules.market_temperature is None
    assert restored.signal_rules.market_analysis is not None


def test_spec_v0_1_rejects_free_form_constraint():
    """constraint도 더 이상 자유 dict가 아니다 — 다섯 필드 구조가 강제된다."""
    payload = json.loads(_sample_spec().model_dump_json())
    payload["constraint"] = {"max_position_size": 0.3}  # 예전 자유 dict 형태

    with pytest.raises(ValidationError):
        SpecV0_1.model_validate(payload)


def test_spec_v0_1_requires_all_five_constraint_fields():
    payload = json.loads(_sample_spec().model_dump_json())
    del payload["constraint"]["max_drawdown"]

    with pytest.raises(ValidationError):
        SpecV0_1.model_validate(payload)


def test_constraint_spec_rejects_unknown_fields():
    """ConstraintSpec 에 없는 칸(예: leverage_allowed)은 들어가면 안 된다."""
    with pytest.raises(ValidationError):
        ConstraintSpec.model_validate(
            {
                "max_weight_per_asset": 0.4,
                "min_weight_per_asset": 0.0,
                "cash_min": 0.02,
                "max_loss_per_trade": 0.05,
                "max_drawdown": 0.2,
                "leverage_allowed": False,
            }
        )


def test_constraint_spec_rejects_out_of_range_value():
    with pytest.raises(ValidationError):
        ConstraintSpec.model_validate(
            {
                "max_weight_per_asset": 1.5,  # > 1
                "min_weight_per_asset": 0.0,
                "cash_min": 0.02,
                "max_loss_per_trade": 0.05,
                "max_drawdown": 0.2,
            }
        )


def test_committed_schema_file_matches_current_model():
    """scripts/export_contract_schemas.py 를 안 돌리고 모델만 고치면 이 테스트가 실패한다."""
    committed = json.loads(SPEC_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert committed == build_spec_schema()
