"""전략서 v0.2 계약 (설계 문서 "전략 명세 v0.2 및 전략 검증기" 의 "전략서와 AI 출력의 모양").

모양은 평가 데이터셋 정답 전략서(eval/dataset.jsonl 의 expected_spec)와 같고 meta 칸을 더한다.
평가가 정답 전략서와 시스템 전략서를 똑같이 백테스트해 주문을 비교하므로 두 모양이 같아야 한다.

이 계약은 모양만 본다: 타입, 허용 값 목록, 필수 칸, 0~1 범위.
아래 같은 뜻 검사는 검사 3단계가 사유 코드와 근거를 남기며 한다. 여기서 막으면 그럴 수 없다.
  - 지표별 허용 기간(ma 는 5 · 10 · 20 · 60 · 120), 지표와 of 의 짝(volume_ratio 를 지수에)
  - 단위가 다른 비교(RSI 와 이평), 숫자 범위(RSI 150), 자기 자신 비교
  - entry_return · holding_days 를 살 때 조건에 씀
  - 비중 합 100% 초과(되묻기 weights_over_100)

v0.1(app/contracts/spec.py)은 모듈을 하나씩 옮긴 뒤 지운다(docs/v0.2-plan.md).
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

STRATEGY_VERSION = "0.2"

# 전략 요소 사전의 값. 기간이 맞는지는 검사 3단계가 본다
IndicatorName = Literal[
    "close", "ma", "rsi", "ret", "vol", "volume_ratio", "vix", "entry_return", "holding_days",
]
Op = Literal["<", "<=", ">", ">=", "cross_below", "cross_above"]
RebalanceId = Literal[
    "RB_MONTHLY_FIRST", "RB_MONTHLY_MID", "RB_QUARTERLY", "RB_WEEKLY", "RB_THRESHOLD_5",
]

# 종목코드는 숫자로 시작하는 6자리다. 숫자만이 아닌 코드도 있다(예: 0000D0)
# 숫자로 시작해야 NASDAQ 같은 영문 6자를 종목코드로 받지 않는다. 원장 3,850종목이 모두 이 형식이다
# 누구의 값을 볼지: 그 종목 자신, 지수 둘, VIX, 다른 종목
SUBJECT_PATTERN = r"^(self|KOSPI200|KOSDAQ|VIX|[0-9][0-9A-Z]{5})$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Indicator(_Strict):
    indicator: IndicatorName
    window: int | None = Field(default=None, gt=0)
    of: str = Field(default="self", pattern=SUBJECT_PATTERN)


class Value(_Strict):
    value: float


class Compare(_Strict):
    left: Indicator
    op: Op
    right: Indicator | Value


class AllOf(_Strict):
    # 묶음은 한 겹만. 묶음 안에 묶음은 받지 않는다(설계 문서 2026-11-02)
    all: list[Compare] = Field(min_length=2)


class AnyOf(_Strict):
    any: list[Compare] = Field(min_length=2)


Cond = Compare | AllOf | AnyOf


class Rule(_Strict):
    target: str = Field(default="each", pattern=r"^(each|[0-9][0-9A-Z]{5})$")
    entry: Cond | None = None
    exit: Cond | None = None

    @model_validator(mode="after")
    def _one_side_at_least(self) -> Rule:
        if self.entry is None and self.exit is None:
            raise ValueError("rule 에는 entry 나 exit 중 하나는 있어야 한다")
        return self


class Selector(_Strict):
    # 종류로 고르기. 데모 범위 밖이지만 평가 데이터셋 정답에 있어 모양은 받는다
    kind: Literal["sector", "market"]
    value: str | None = None
    market: Literal["KOSPI", "KOSDAQ"] | None = None
    count: int | Literal["all"]
    method: Literal["market_cap", "trading_value", "momentum", "low_vol", "direct"] | None = None


class Universe(_Strict):
    tickers: list[str] | None = Field(default=None, min_length=1)
    selector: Selector | None = None
    # selector 로 실제로 고른 종목. 다시 돌릴 때 종목을 다시 고르지 않는다(재현성)
    resolved_tickers: list[str] | None = None

    @field_validator("tickers", "resolved_tickers")
    @classmethod
    def _ticker_format(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            bad = [t for t in v if not _is_ticker(t)]
            if bad:
                raise ValueError(f"종목코드 형식이 아니다: {bad}")
            if len(set(v)) != len(v):
                raise ValueError("종목코드가 겹친다")
        return v

    @model_validator(mode="after")
    def _tickers_or_selector(self) -> Universe:
        if (self.tickers is None) == (self.selector is None):
            raise ValueError("universe 에는 tickers 와 selector 중 하나만 있어야 한다")
        if self.resolved_tickers is not None and self.selector is None:
            raise ValueError("resolved_tickers 는 selector 로 고른 경우에만 쓴다")
        return self

    def held_tickers(self) -> list[str] | None:
        # 실제로 거래할 종목. selector 를 아직 풀지 않았으면 None
        return self.tickers if self.tickers is not None else self.resolved_tickers


class Constraint(_Strict):
    # 사용자가 줄 수 있는 제한은 종목당 최대 비중 하나다
    max_weight_per_asset: float | None = Field(default=None, gt=0, le=1)


class Backtest(_Strict):
    start: date = date(2023, 1, 1)
    end: date = date(2025, 12, 31)
    capital: float = Field(default=10_000_000, gt=0)
    include_dividends: bool = False

    @model_validator(mode="after")
    def _start_before_end(self) -> Backtest:
        if self.start >= self.end:
            raise ValueError("backtest.start 는 end 보다 앞이어야 한다")
        return self


DefaultName = Literal[
    "weights", "rebalance", "period", "capital", "costs", "dividends",
    "rsi_window", "rsi_level", "subject",
]


class Meta(_Strict):
    # 화면의 "기본값 사용" 표시와 "AI가 이해한 전략" 설명, 사전 버전 기록에 쓴다. 실행에는 영향이 없다
    defaults_used: list[DefaultName] = Field(default_factory=list)
    evidence: dict[str, list[str]] = Field(default_factory=dict)
    dictionary_version: str | None = None


class StrategyV0_2(_Strict):
    universe: Universe
    weights: Literal["equal"] | dict[str, float] = "equal"
    rebalance: RebalanceId | None = None
    rules: list[Rule] = Field(default_factory=list)
    constraint: Constraint = Field(default_factory=Constraint)
    backtest: Backtest = Field(default_factory=Backtest)
    meta: Meta = Field(default_factory=Meta)

    @field_validator("weights")
    @classmethod
    def _weight_range(cls, v):
        if isinstance(v, dict):
            if not v:
                raise ValueError("weights 를 종목별로 적을 때는 비어 있으면 안 된다")
            for ticker, w in v.items():
                if not _is_ticker(ticker):
                    raise ValueError(f"weights 의 종목코드 형식이 아니다: {ticker}")
                if not 0 < w <= 1:
                    raise ValueError(f"weights 는 0 초과 1 이하다: {ticker}={w}")
        return v

    @model_validator(mode="after")
    def _references(self) -> StrategyV0_2:
        tickers = self.universe.held_tickers()
        if tickers is None:
            return self
        held = set(tickers)
        if isinstance(self.weights, dict) and not set(self.weights) <= held:
            raise ValueError(f"weights 에 universe 밖 종목이 있다: {sorted(set(self.weights) - held)}")
        for rule in self.rules:
            if rule.target != "each" and rule.target not in held:
                raise ValueError(f"rule.target 이 universe 밖 종목이다: {rule.target}")
        return self

    @property
    def is_rules_strategy(self) -> bool:
        # 전략 유형 칸은 두지 않는다. rules 가 있으면 매매 조건형, 없으면 자산 배분형(설계 문서 2026-11-02)
        return bool(self.rules)


def iter_compares(cond: Cond | None) -> Iterator[Compare]:
    """조건 안의 비교식을 차례로 돌려준다. 묶음은 한 겹이다."""
    if cond is None:
        return
    if isinstance(cond, Compare):
        yield cond
    else:
        yield from (cond.all if isinstance(cond, AllOf) else cond.any)


def _is_ticker(value: str) -> bool:
    return (
        len(value) == 6 and value.isascii() and value.isalnum()
        and value == value.upper() and value[0].isdigit()
    )
