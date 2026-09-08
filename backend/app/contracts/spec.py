"""계약 ① Spec JSON 스키마 v0.1 (docs/infra-spec.md 6단계 표 ①).

M1(자연어 -> Spec 컴파일러)의 structured outputs 스키마이자, M2 이후 모든 계층이
읽는 Spec의 형태다. 스키마 변경은 전원 합의 사항이므로(그래프 상위 CLAUDE.md)
여기 없는 필드를 추가하지 않는다.

`objective`와 `reproducibility` 블록은 정본이 명시적으로 제외했다: 재현 조건
(random_seed · data_snapshot_asof · feature_set_version)은 BACKTEST_RUNS와
DECISION_RECORDS에만 존재한다 (docs/db-erd.md 4.3 "재현 조건 위치").

`rebalance`/`signal_rules`/`constraint`의 하위 구조는 기획서 3.2 확정본을 그대로
따른다. 다만 `trigger.type`처럼 "허용값 집합"까지는 아직 팀이 정하지 않은 필드가
있다 — 그런 자리는 BBL(Building Block Library) 확정 후 M1이 채우도록 스칼라
타입만 고정하고 enum을 지어내지 않는다(각 필드의 `# TODO` 주석 참조).

`constraint`(ConstraintSpec)는 하드캡과 이름이 겹치는 필드가 있어 혼동하기
쉽다: `max_weight_per_asset`/`max_loss_per_trade`/`max_drawdown`은
HARDCAP_VERSIONS에도 동명 컬럼이 있지만, 여기 있는 값은 **사용자가 요청한
값**이고 하드캡은 Validator 4계층이 적용하는 **시스템 상한**이다(그래프
상위 CLAUDE.md "하드캡" 절 — 하드캡 자체는 Spec 스키마에 자리가 없다).
`min_interval_days`(rebalance 블록 소유)와 `leverage_allowed`(하드캡 전용,
Spec 어디에도 없다)는 ConstraintSpec에 넣지 않는다.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UniverseItem(BaseModel):
    """SPEC_UNIVERSE 한 행에 대응하는 LLM 원출력 단위.

    여기 담기는 weight_min/weight_max는 LLM이 낸 원출력이다. 검증 단계(Validator,
    이번 범위 밖)를 거치면 DB의 spec_universe.weight_min_raw/weight_max_raw로
    보존되고, 허용범위 프리셋으로 보정된 값이 weight_min/weight_max에 확정된다.
    """

    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(min_length=1, description="etf_master.ticker FK")
    name: str = Field(min_length=1)
    weight_min: float = Field(ge=0, le=1)
    weight_max: float = Field(ge=0, le=1)


class TriggerSpec(BaseModel):
    """리밸런싱 발동 조건 (기획서 3.2 확정본).

    type/freq/day 각각의 허용값 집합(예: type이 "calendar"/"threshold"/
    "signal" 중 무엇을 가질 수 있는지)은 BBL(Building Block Library) 확정 후
    M1이 채운다. 지금은 이름과 스칼라 타입만 고정한다 — day는 freq에 따라
    의미가 갈릴 수 있어(예: 월간이면 일자, 주간이면 요일) 강제로 값을 요구하지
    않는다.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)  # TODO: 값 집합은 BBL 확정 후 M1
    freq: str = Field(min_length=1)  # TODO: 값 집합은 BBL 확정 후 M1
    day: int | None = None  # TODO: 값 집합은 BBL 확정 후 M1


class RebalanceRule(BaseModel):
    """STRATEGY_SPECS.rebalance JSONB (docs/db-erd.md 4.1, 기획서 3.2).

    min_interval_days는 사용자가 요청한 리밸런싱 주기다. HARDCAP_VERSIONS.
    min_interval_days(시스템 하드캡, 이 스키마엔 자리가 없다)는 이 값의 하한으로
    Validator 4계층이 클램프한다 — SPEC_UNIVERSE의 weight_min/max가 허용범위
    프리셋으로 클램프되는 것과 같은 패턴이다.
    """

    model_config = ConfigDict(extra="forbid")

    trigger: TriggerSpec
    min_interval_days: int = Field(ge=0)


class MarketAnalysisRule(BaseModel):
    """signal_rules.market_analysis (기획서 3.2). 허용 지표명 집합은 BBL 확정 후
    M1이 채운다."""

    model_config = ConfigDict(extra="forbid")

    indicators: list[str] = Field(default_factory=list)  # TODO: 값 집합은 BBL 확정 후 M1


class SentimentRule(BaseModel):
    """signal_rules.sentiment (기획서 3.2). 허용 섹터명 집합은 BBL 확정 후
    M1이 채운다."""

    model_config = ConfigDict(extra="forbid")

    target_sectors: list[str] = Field(default_factory=list)  # TODO: 값 집합은 BBL 확정 후 M1
    lookback_hours: int = Field(ge=0)


class MarketTemperatureRule(BaseModel):
    """signal_rules.market_temperature (기획서 3.2).

    trend_index/volatility_index는 REGIME_SNAPSHOTS.trend_index(docs/db-erd.md
    4.2, VARCHAR)와 같은 성격의 지표 식별 코드로 보고 문자열로 잡았다 — 수치가
    아니라 "어떤 지표를 쓸지"를 가리키는 값이다. 어떤 코드가 허용되는지는 BBL
    확정 후 M1이 채운다. trend_ma_window(이동평균 윈도우 크기)만 순수 정수라
    TODO를 달지 않았다.
    """

    model_config = ConfigDict(extra="forbid")

    trend_index: str = Field(min_length=1)  # TODO: 값 집합은 BBL 확정 후 M1
    trend_ma_window: int = Field(ge=1)
    volatility_index: str = Field(min_length=1)  # TODO: 값 집합은 BBL 확정 후 M1


class SignalRules(BaseModel):
    """STRATEGY_SPECS.signal_rules JSONB — 3관점 판단 기준 (기획서 3.2).

    세 관점 모두 내부 구조는 고정하되, 블록 자체는 여전히 선택적(None 허용)
    이다 — 그래프 상위 CLAUDE.md가 감성·시장온도 관점을 "선택 범위"로 명시하고
    있어(P3 필수 범위는 시장분석뿐), 이 Spec 하나가 세 관점을 전부 채워야
    한다고 강제하지 않는다. 하위 값 집합(지표명/섹터명/지표 코드)은 BBL 확정
    후 M1이 채운다 — 위 세 서브모델의 TODO 주석 참조.
    """

    model_config = ConfigDict(extra="forbid")

    market_analysis: MarketAnalysisRule | None = None
    sentiment: SentimentRule | None = None
    market_temperature: MarketTemperatureRule | None = None


class ConstraintSpec(BaseModel):
    """STRATEGY_SPECS.constraint_user JSONB — 사용자 제약 (기획서 3.2 확정본).

    다섯 필드 모두 사용자가 요청한 값이다. 이름이 같은 HARDCAP_VERSIONS 컬럼
    (max_weight_per_asset · max_loss_per_trade · max_drawdown)은 별개의
    시스템 상한이며, 이 값을 그대로 쓰는 게 아니라 Validator 4계층이 이 값을
    하드캡으로 클램프한다 — 이 스키마는 그 클램프 이전의 요청값만 담는다.
    """

    model_config = ConfigDict(extra="forbid")

    max_weight_per_asset: float = Field(ge=0, le=1)
    min_weight_per_asset: float = Field(ge=0, le=1)
    cash_min: float = Field(ge=0, le=1)
    max_loss_per_trade: float = Field(ge=0, le=1)
    max_drawdown: float = Field(ge=0, le=1)


class SpecV0_1(BaseModel):
    """Strategy Spec JSON 스키마 v0.1 (LLM structured outputs 대상)."""

    model_config = ConfigDict(extra="forbid")

    spec_id: str = Field(min_length=1)
    spec_version: str = Field(min_length=1)
    user_id: int
    name: str = Field(min_length=1)
    created_at: datetime
    universe: list[UniverseItem] = Field(min_length=1)
    rebalance: RebalanceRule
    signal_rules: SignalRules
    constraint: ConstraintSpec
