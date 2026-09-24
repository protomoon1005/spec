# 전략서 스키마 동적 조립 (M1 4단계).
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 candidates.py 와 같다 —
# 이 파일은 as_of 가드 사정권이다.
#
# ── 무엇을 하나 ──────────────────────────────────────────────────────
# 계약이 내놓는 전략서 스키마는 "어떤 모양인가" 만 정해 둔다. 비중은 0~1 이면 통과하고
# 지표 이름은 아무 문자열이나 통과한다. 여기서 그 빈칸을 **이번 요청에 맞게** 좁힌다.
#
#   universe        후보 종목만, 종목마다 그 성향의 허용 비중 범위로
#   indicators      BBL 의 지표 블록 이름만
#   trigger         BBL 의 리밸런싱 블록이 쓰는 방식·주기만
#   trend/vol index BBL 의 거시지표 블록만
#   target_sectors  후보 종목들이 속한 업종만
#
# 좁힌 스키마를 LLM 에 넘기면 **형식 자체가 규칙이라 어길 수가 없다.** 만들고 나서
# 검사해 걸러내는 것보다 강하다.
#
# ── 계약을 다시 쓰지 않는다 ──────────────────────────────────────────
# 바탕은 contracts/schema_export.build_spec_schema() 가 만든 것을 그대로 쓰고 위에
# 덮어쓴다. 손으로 다시 쓰면 계약이 두 벌이 되고 언젠가 어긋난다.
#
# ── 못 막는 것 ───────────────────────────────────────────────────────
# JSON Schema 로는 **칸끼리의 관계**를 표현할 수 없다. weight_min <= weight_max 나
# 비중 합계가 1 이하인지는 여기서 못 막는다. 파싱 뒤에 확인해야 한다(5단계).
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.contracts.schema_export import build_spec_schema
from app.m1.candidates import CandidateSet
from app.repositories import bbl

INDICATOR_SLOT = "signal_rules.market_analysis.indicators"
TREND_SLOT = "signal_rules.market_temperature.trend_index"
VOLATILITY_SLOT = "signal_rules.market_temperature.volatility_index"


def build_compile_schema(candidate_set: CandidateSet) -> dict[str, Any]:
    # 후보가 없으면 만들 수 있는 전략서가 없다. 빈 스키마를 주면 LLM 이
    # 아무거나 지어내거나 빈 배열을 내고, 어느 쪽이든 원인을 알기 어렵다.
    if not candidate_set.candidates:
        raise ValueError("후보 종목이 없어 스키마를 만들 수 없다")

    schema = deepcopy(build_spec_schema())
    defs = schema["$defs"]

    schema["properties"]["universe"] = _universe_schema(candidate_set)
    _narrow_indicators(defs)
    _narrow_trigger(defs)
    _narrow_market_temperature(defs)
    _narrow_sectors(defs, candidate_set)
    return schema


def _universe_schema(candidate_set: CandidateSet) -> dict[str, Any]:
    # 허용 범위가 같은 종목끼리 묶어 선택지를 만든다. 범위는 위험등급마다 정해지므로
    # **후보가 몇 개든 선택지는 많아야 여섯 개**(G1~G6)다.
    #
    # 처음엔 종목마다 선택지를 하나씩 뒀는데, 제약 디코딩이 선택지 수에 따라 급격히
    # 느려졌다 — 5종목 2분, 10종목은 5분을 넘겨 시간초과로 죽었다(2026-09-20 실측,
    # qwen3:8b). 묶으면 후보를 늘려도 문법 크기가 그대로다.
    #
    # 종목명은 스키마로 고정하지 않는다. 종목코드만 정해지면 이름은 우리가 아는
    # 값이라, 받아 놓고 후처리에서 덮어쓴다(postprocess). 모델에게 정확한 문자열을
    # 받아 내려고 문법을 키울 이유가 없다.
    groups: dict[tuple[float, float], list[str]] = {}
    for candidate in candidate_set.candidates:
        groups.setdefault((candidate.weight_min, candidate.weight_max), []).append(candidate.ticker)

    options = [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["ticker", "name", "weight_min", "weight_max"],
            "properties": {
                "ticker": {"enum": tickers},
                "name": {"type": "string"},
                "weight_min": {"type": "number", "minimum": low, "maximum": high},
                "weight_max": {"type": "number", "minimum": low, "maximum": high},
            },
        }
        for (low, high), tickers in sorted(groups.items())
    ]
    return {
        "type": "array",
        "minItems": 1,
        "maxItems": len(candidate_set.candidates),
        "items": {"oneOf": options},
    }


def _narrow_indicators(defs: dict[str, Any]) -> None:
    names = _block_values(INDICATOR_SLOT)
    if names:
        defs["MarketAnalysisRule"]["properties"]["indicators"]["items"] = {"enum": names}


def _narrow_trigger(defs: dict[str, Any]) -> None:
    types: set[str] = set()
    freqs: set[str] = set()
    for block in bbl.list_blocks("rebalance"):
        trigger = (block.params_schema or {}).get("trigger", {})
        if trigger.get("type"):
            types.add(trigger["type"])
        if trigger.get("freq"):
            freqs.add(trigger["freq"])
    properties = defs["TriggerSpec"]["properties"]
    if types:
        properties["type"] = {"enum": sorted(types)}
    if freqs:
        properties["freq"] = {"enum": sorted(freqs)}


def _narrow_market_temperature(defs: dict[str, Any]) -> None:
    properties = defs["MarketTemperatureRule"]["properties"]
    trend = _block_values(TREND_SLOT)
    volatility = _block_values(VOLATILITY_SLOT)
    if trend:
        properties["trend_index"] = {"enum": trend}
    if volatility:
        properties["volatility_index"] = {"enum": volatility}


def _narrow_sectors(defs: dict[str, Any], candidate_set: CandidateSet) -> None:
    # 감성 관점은 후보 종목이 속한 업종을 본다. 담지도 않을 업종의 뉴스를 보라고
    # 할 이유가 없다.
    sectors = sorted(
        {c.sector_group_id for c in candidate_set.candidates if c.sector_group_id}
    )
    if sectors:
        defs["SentimentRule"]["properties"]["target_sectors"]["items"] = {"enum": sectors}


def _block_values(slot: str) -> list[str]:
    # 블록의 params_schema 가 "전략서의 어느 자리에 어떤 값으로 들어가는지" 를 담는다.
    return sorted(
        block.params_schema["value"]
        for block in bbl.list_blocks("indicator")
        if (block.params_schema or {}).get("slot") == slot
    )
