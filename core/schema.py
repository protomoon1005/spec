"""DemoSpec v0.1 Pydantic 모델 + JSON Schema export (docs/demo-spec.md §B2·B3).

- 지표·연산자는 Literal(enum)로 모델에 고정한다.
- 종목코드·종목명은 모델에서는 str 로 두고(엔진은 임의 종목을 돌릴 수 있어야 함),
  LLM 구조화 출력용으로 내보내는 JSON Schema 에만 data/tickers.json 의 enum 을 박는다.
  사전 대조는 검증기 2단계(참조)가 한다.
- 수치 범위(손절 0~0.5 등)는 모델에 넣지 않는다. 검증기 4단계에서 한국어 메시지로 잡는다.
- 탈출구: 사전에 정확히 맞는 값이 없을 때 LLM 이 비슷한 값으로 조용히 바꿔치기하지 않도록
  종목 자리에 UNKNOWN, 지표 자리에 UNSUPPORTED 를 쓸 수 있고, 매핑 못 한 원문 구절은
  unmapped_phrases 에 적는다. 이 값이 하나라도 있으면 검증기가 ok 를 무효로 하고
  clarify/reject 로 돌린다 (백테스트 엔진까지 가지 않는다).

내보내기: python -m core.schema   → schema/demospec.json
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from core.indicators import INDICATOR_IDS

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema" / "demospec.json"

UNKNOWN_TICKER = "UNKNOWN"            # 종목 사전에 없는 종목
UNSUPPORTED_INDICATOR = "UNSUPPORTED"  # 지표 사전에 없는 지표

IndicatorId = Literal[tuple(INDICATOR_IDS)]  # type: ignore[valid-type]
# 명세에 쓸 수 있는 지표 자리 값 = 사전 지표 + 탈출구. 실행 가능한 지표는 IndicatorId 뿐이다.
IndicatorRef = Literal[tuple(INDICATOR_IDS) + (UNSUPPORTED_INDICATOR,)]  # type: ignore[valid-type]
Operator = Literal["lt", "gt", "cross_above", "cross_below"]
RejectCode = Literal["FUTURE_REF", "UNSUPPORTED_CONCEPT", "UNSUPPORTED_ACTION", "NO_EXIT_RULE"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RefMul(_Strict):
    """지표 × 배수. 예: {"ref": "VOL_SMA_20", "mul": 2} = 거래량 20일 평균의 2배."""
    ref: IndicatorRef
    mul: float


class Condition(_Strict):
    left: IndicatorRef
    op: Operator
    right: Union[IndicatorRef, float, RefMul]


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
    start: str = Field("2021-01-01", pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field("2025-12-31", pattern=r"^\d{4}-\d{2}-\d{2}$")
    initial_cash: float = 10_000_000

    @field_validator("start", "end")
    @classmethod
    def _real_date(cls, v: str) -> str:
        date.fromisoformat(v)  # 2025-02-30 같은 값은 ValueError
        return v


class CostConfig(_Strict):
    commission_bps: float = 1.5
    tax_profile: Literal["kr_default"] = "kr_default"


class RejectReason(_Strict):
    code: RejectCode
    detail: str


class Mapping(_Strict):
    phrase: str
    field: str


class Provenance(_Strict):
    input_text: str
    mappings: list[Mapping] = Field(default_factory=list)
    defaults_applied: list[str] = Field(default_factory=list)


class DemoSpec(_Strict):
    """verdict 가 clarify/reject 이면 universe/entry/exit 를 비워 둘 수 있다.
    실행 가능 여부(verdict=ok, 조건 존재 등)는 검증기 3단계가 판정한다."""
    schema_version: Literal["demo-0.1"] = "demo-0.1"
    verdict: Literal["ok", "clarify", "reject"] = "ok"
    questions: list[str] = Field(default_factory=list, max_length=3)
    reject_reason: Optional[RejectReason] = None
    universe: list[UniverseItem] = Field(default_factory=list)
    entry: Optional[EntryRule] = None
    exit: Optional[ExitRule] = None
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    costs: CostConfig = Field(default_factory=CostConfig)
    provenance: Optional[Provenance] = None
    unmapped_phrases: list[str] = Field(default_factory=list)   # 사전에 매핑할 수 없는 원문 구절

    @field_validator("questions")
    @classmethod
    def _clarify_questions(cls, v: list[str], info: ValidationInfo) -> list[str]:
        """clarify 면 질문이 1개 이상이고 모두 물음표로 끝나야 한다 (형식 검증)."""
        if info.data.get("verdict") == "clarify":
            if not v:
                raise PydanticCustomError("clarify_questions", "추가 질문 판정(clarify)인데 질문이 없습니다.")
            bad = [q for q in v if not q.strip().endswith(("?", "？"))]
            if bad:
                raise PydanticCustomError("clarify_questions", "질문은 물음표(?)로 끝나야 합니다: {bad}",
                                          {"bad": bad[0]})
        return v

    @field_validator("reject_reason")
    @classmethod
    def _reject_has_reason(cls, v, info: ValidationInfo):
        if info.data.get("verdict") == "reject" and v is None:
            raise PydanticCustomError("reject_reason", "실행 불가 판정(reject)인데 사유가 없습니다.")
        return v


# ---------------------------------------------------------------- JSON Schema export

# provenance 가 가리킬 수 있는 명세 경로 (LLM 스키마 enum). 실제 존재 여부는 검증기 2단계가 본다.
PROVENANCE_PATHS: list[str] = (
    ["universe"] + [f"universe[{i}]" for i in range(3)]
    + ["entry"] + [f"entry.conditions[{i}]" for i in range(4)]
    + ["exit"] + [f"exit.conditions[{i}]" for i in range(4)]
    + ["exit.stop_loss", "exit.take_profit", "exit.max_hold_days",
       "backtest", "backtest.start", "backtest.end", "backtest.initial_cash", "costs"]
)

DEFAULTABLE_PATHS: list[str] = [p for p in PROVENANCE_PATHS if not p.startswith("universe")]


def _require_all(node) -> None:
    """모든 객체의 속성을 required 로. Ollama 문법 제약에서 선택 필드는 모델이 생략해 버린다
    (2026-11-02 확인: verdict·universe 누락). 값을 비우려면 null/[] 을 명시적으로 쓰게 한다."""
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node["required"] = list(node["properties"])
        for v in node.values():
            _require_all(v)
    elif isinstance(node, list):
        for v in node:
            _require_all(v)


def build_json_schema() -> dict:
    """LLM 구조화 출력용 JSON Schema.
    - 종목코드·종목명에 tickers.json enum(+UNKNOWN), provenance 경로에 PROVENANCE_PATHS enum 주입
    - 모든 필드 required (Pydantic 모델은 기본값이 있어 선택 필드로 남는다)
    Ollama 0.40 이 $defs/$ref·anyOf 를 문법으로 강제하는 것을 확인했으므로 참조 구조를 그대로 둔다."""
    from core.dictionary import load_tickers  # 순환 import 방지

    schema = DemoSpec.model_json_schema()
    tickers = load_tickers()
    props = schema["$defs"]["UniverseItem"]["properties"]
    props["ticker"]["enum"] = list(tickers) + [UNKNOWN_TICKER]
    props["name"]["enum"] = [t.name for t in tickers.values()] + [UNKNOWN_TICKER]
    schema["$defs"]["Mapping"]["properties"]["field"]["enum"] = PROVENANCE_PATHS
    # 종목은 기본값이 없다 (언급 없으면 clarify) → defaults_applied 에서 universe 계열 제외
    schema["$defs"]["Provenance"]["properties"]["defaults_applied"]["items"]["enum"] = DEFAULTABLE_PATHS
    _require_all(schema)
    schema["$comment"] = ("generated by `python -m core.schema` — 직접 수정 금지. "
                          "지표·연산자·종목은 사전(docs/demo-spec.md §B3, data/tickers.json) 값만 허용.")
    return schema


def dump_json_schema(schema: dict) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2) + "\n"


def export_json_schema(path: Path | str = SCHEMA_PATH) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_json_schema(build_json_schema()), encoding="utf-8", newline="\n")
    return path


if __name__ == "__main__":
    print(f"wrote {export_json_schema()}")
