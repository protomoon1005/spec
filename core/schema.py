"""DemoSpec v0.1 Pydantic 모델 (docs/demo-spec.md §B2·B3).

1단계 범위: 모델만. JSON Schema export·enum 강화·검증기는 2단계에서.
지표/연산자 허용값은 B3 블록 사전 그대로 Literal 로 고정한다.
"""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

IndicatorId = Literal[
    "CLOSE", "OPEN", "HIGH", "LOW", "VOLUME",
    "SMA_5", "SMA_10", "SMA_20", "SMA_60", "SMA_120",
    "EMA_12", "EMA_26",
    "RSI_14",
    "BB_UPPER_20", "BB_LOWER_20",
    "VOL_SMA_20",
    "HIGH_N_20", "LOW_N_20",
]
Operator = Literal["lt", "gt", "cross_above", "cross_below"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RefMul(_Strict):
    """지표 × 배수. 예: {"ref": "VOL_SMA_20", "mul": 2} = 거래량 20일 평균의 2배."""
    ref: IndicatorId
    mul: float


class Condition(_Strict):
    left: IndicatorId
    op: Operator
    right: Union[IndicatorId, float, RefMul]


class EntryRule(_Strict):
    logic: Literal["all", "any"] = "all"
    conditions: list[Condition]


class ExitRule(_Strict):
    logic: Literal["all", "any"] = "any"
    conditions: list[Condition] = Field(default_factory=list)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    max_hold_days: Optional[int] = None


class UniverseItem(_Strict):
    ticker: str
    name: str
    weight: float


class BacktestConfig(_Strict):
    start: str = "2021-01-01"
    end: str = "2025-12-31"
    initial_cash: float = 10_000_000


class CostConfig(_Strict):
    commission_bps: float = 1.5
    tax_profile: str = "kr_default"


class Mapping(_Strict):
    phrase: str
    field: str


class Provenance(_Strict):
    input_text: str
    mappings: list[Mapping] = Field(default_factory=list)
    defaults_applied: list[str] = Field(default_factory=list)


class DemoSpec(_Strict):
    schema_version: Literal["demo-0.1"] = "demo-0.1"
    verdict: Literal["ok", "clarify", "reject"] = "ok"
    questions: list[str] = Field(default_factory=list)
    reject_reason: Optional[str] = None
    universe: list[UniverseItem]
    entry: EntryRule
    exit: ExitRule
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    costs: CostConfig = Field(default_factory=CostConfig)
    provenance: Optional[Provenance] = None
