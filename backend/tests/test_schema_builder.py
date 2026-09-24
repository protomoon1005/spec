"""전략서 스키마 동적 조립 (M1 4단계).

postgres 와 시드가 적재된 상태여야 한다.

여기서 보는 것은 **"LLM 이 어길 수 있는 자리가 남아 있는가"** 다. 계약이 내놓는
스키마는 비중이 0~1 이면 통과하고 지표 이름은 아무거나 통과한다. 조립 뒤에는
이번 요청에 맞는 값만 통과해야 한다.
"""
from __future__ import annotations

import pytest

from app.contracts.spec import SpecV0_1
from app.m1 import candidates as C
from app.m1 import schema_builder as S
from app.repositories import bbl
from app.views.market.features import CURRENT_FEATURE_SET_VERSION, FEATURE_SETS


@pytest.fixture
def built(engine):
    return S.build_compile_schema(C.select(risk_level=5, sectors=["SECTOR_SEMICONDUCTOR"], target=3))


def test_no_candidates_is_an_error(engine):
    """빈 스키마를 주면 LLM 이 아무거나 지어내거나 빈 배열을 낸다. 둘 다 원인을 알기 어렵다."""
    empty = C.select(risk_level=1, requested_tickers=["999999"], sectors=["SECTOR_CONSUMER"], target=0)
    assert empty.candidates == ()

    with pytest.raises(ValueError, match="후보"):
        S.build_compile_schema(empty)


# --- 종목 ------------------------------------------------------------------


def _allowed_tickers(schema) -> set[str]:
    return {
        ticker
        for option in schema["properties"]["universe"]["items"]["oneOf"]
        for ticker in option["properties"]["ticker"]["enum"]
    }


def test_universe_offers_only_the_candidates(engine, built):
    """후보로 뽑힌 종목만 고를 수 있어야 한다. 어떤 종목이 뽑히는지는 원장에 달려
    있으므로 종목코드를 박지 않는다 — 원장이 바뀌어도 깨지지 않게."""
    expected = set(C.select(risk_level=5, sectors=["SECTOR_SEMICONDUCTOR"], target=3).tickers)

    assert _allowed_tickers(built) == expected
    assert built["properties"]["universe"]["maxItems"] == len(expected)


def test_options_are_grouped_by_bound_not_by_ticker(engine):
    """제약 디코딩이 선택지 수에 따라 급격히 느려진다 — 10종목이면 5분을 넘겨 죽었다.
    범위가 같은 종목을 묶으면 후보를 늘려도 선택지는 등급 수(최대 6)를 넘지 않는다."""
    result = C.select(risk_level=5, target=30)
    schema = S.build_compile_schema(result)

    options = schema["properties"]["universe"]["items"]["oneOf"]
    assert len(result.candidates) > len(options)
    assert len(options) <= 6
    assert _allowed_tickers(schema) == set(result.tickers)


def test_weight_range_matches_the_preset(engine):
    result = C.select(risk_level=3, target=4)
    schema = S.build_compile_schema(result)
    by_ticker = {
        ticker: option["properties"]
        for option in schema["properties"]["universe"]["items"]["oneOf"]
        for ticker in option["properties"]["ticker"]["enum"]
    }

    for candidate in result.candidates:
        weights = by_ticker[candidate.ticker]
        assert weights["weight_max"]["maximum"] == candidate.weight_max
        assert weights["weight_min"]["minimum"] == candidate.weight_min


def test_name_is_not_fixed_in_the_schema(engine, built):
    """종목명은 종목코드만 정해지면 우리가 아는 값이다. 문법을 키우지 않으려고 빼고
    후처리에서 덮어쓴다."""
    for option in built["properties"]["universe"]["items"]["oneOf"]:
        assert option["properties"]["name"] == {"type": "string"}


def test_extra_properties_are_forbidden_per_item(engine, built):
    """칸을 지어내지 못하게 막는다."""
    for option in built["properties"]["universe"]["items"]["oneOf"]:
        assert option["additionalProperties"] is False


# --- 값 집합 ---------------------------------------------------------------


def test_indicator_names_are_limited_to_the_feature_set(engine, built):
    """예시 파일에 있던 momentum_20d 같은 이름은 계산하지 않는다. 지어내면 판단 계층이 못 찾는다."""
    allowed = built["$defs"]["MarketAnalysisRule"]["properties"]["indicators"]["items"]["enum"]

    assert set(allowed) == set(FEATURE_SETS[CURRENT_FEATURE_SET_VERSION])
    assert "momentum_20d" not in allowed


def test_trigger_values_come_from_the_rebalance_blocks(engine, built):
    from_blocks = {
        (block.params_schema["trigger"]["type"], block.params_schema["trigger"]["freq"])
        for block in bbl.list_blocks("rebalance")
    }
    properties = built["$defs"]["TriggerSpec"]["properties"]

    assert set(properties["type"]["enum"]) == {t for t, _ in from_blocks}
    assert set(properties["freq"]["enum"]) == {f for _, f in from_blocks}


def test_macro_indexes_are_limited(engine, built):
    properties = built["$defs"]["MarketTemperatureRule"]["properties"]

    assert properties["trend_index"]["enum"] == ["KOSPI200"]
    assert properties["volatility_index"]["enum"] == ["VIX_CLOSE"]
    # 수집만 하고 전략서에 자리가 없는 코드는 들어가면 안 된다.
    assert "USDKRW" not in properties["trend_index"]["enum"]


def test_sectors_are_limited_to_the_candidates(engine):
    """담지도 않을 업종의 뉴스를 보라고 할 이유가 없다."""
    result = C.select(risk_level=5, sectors=["SECTOR_BATTERY"], target=3)
    schema = S.build_compile_schema(result)

    allowed = schema["$defs"]["SentimentRule"]["properties"]["target_sectors"]["items"]["enum"]
    assert allowed == ["SECTOR_BATTERY"]


# --- 계약을 깨뜨리지 않는다 --------------------------------------------------


def test_base_contract_shape_is_preserved(engine, built):
    from app.contracts.schema_export import build_spec_schema

    base = build_spec_schema()

    assert built["required"] == base["required"]
    assert built["title"] == base["title"]
    assert built["$schema"] == base["$schema"]


def test_building_does_not_mutate_the_base_schema(engine, built):
    """바탕 스키마를 그대로 고치면 다음 요청이 앞 요청의 후보를 물려받는다."""
    from app.contracts.schema_export import build_spec_schema

    again = build_spec_schema()

    assert again["properties"]["universe"]["items"] == {"$ref": "#/$defs/UniverseItem"}


def test_an_instance_built_within_the_schema_passes_the_contract(engine):
    """스키마가 허용하는 값으로 만든 전략서는 계약 검사도 통과해야 한다."""
    result = C.select(risk_level=5, sectors=["SECTOR_SEMICONDUCTOR"], target=2)
    schema = S.build_compile_schema(result)
    rebalance = bbl.get_block("RB_MONTHLY_FIRST").params_schema

    spec = SpecV0_1(
        spec_id="STR-TEST",
        spec_version="0.1",
        user_id=1,
        name="테스트 전략",
        created_at="2026-09-20T09:00:00+09:00",
        universe=[
            {
                "ticker": c.ticker,
                "name": c.name,
                "weight_min": c.weight_min,
                "weight_max": c.weight_max,
            }
            for c in result.candidates
        ],
        rebalance=rebalance,
        signal_rules={
            "market_analysis": {
                "indicators": schema["$defs"]["MarketAnalysisRule"]["properties"]["indicators"][
                    "items"
                ]["enum"][:2]
            }
        },
        constraint={
            "max_weight_per_asset": 0.30,
            "min_weight_per_asset": 0.00,
            "cash_min": 0.05,
            "max_loss_per_trade": 0.05,
            "max_drawdown": 0.25,
        },
    )

    assert len(spec.universe) == len(result.candidates)


def test_same_request_gives_the_same_schema(engine):
    kwargs = {"risk_level": 3, "target": 5}

    first = S.build_compile_schema(C.select(**kwargs))
    second = S.build_compile_schema(C.select(**kwargs))

    assert first == second
