"""BBL 블록 시드와 검색 (M1 3단계).

postgres 와 db/seeds/05_bbl.sql 이 적재된 상태여야 한다.

이 파일의 절반은 **"시드가 다른 모듈과 어긋나지 않았는가"** 를 본다.
지표 블록의 식별자는 곧 전략서에 들어갈 값이라, 판단 계층이 쓰는 이름과 한 글자라도
다르면 그 값을 못 찾는다. 태그도 마찬가지로 자산군·업종 묶음 이름과 맞아야 한다.
"""
from __future__ import annotations

import pytest

from app.contracts.spec import RebalanceRule
from app.macro import codes as macro_codes
from app.repositories import bbl
from app.views.market.features import CURRENT_FEATURE_SET_VERSION, FEATURE_SETS

EXPECTED_COUNTS = {"indicator": 11, "rebalance": 6, "filter": 7, "etf_trait": 8}
MAX_TAGS_PER_BLOCK = 4


# --- 시드 -------------------------------------------------------------------


@pytest.mark.parametrize(("block_type", "expected"), sorted(EXPECTED_COUNTS.items()))
def test_seed_counts(engine, block_type, expected):
    assert len(bbl.list_blocks(block_type)) == expected


def test_lens_blocks_are_intentionally_empty(engine):
    """감성 렌즈는 목록 정의 자체가 아직 없다. M3 조율 전까지 비워 둔다."""
    assert bbl.list_blocks("lens") == []


def test_every_block_has_params_schema(engine):
    """params_schema 가 '이 블록을 고르면 무엇이 되는가' 를 담는다. 비면 쓸 수 없다."""
    for block in bbl.list_blocks():
        assert block.params_schema, block.block_id


# --- 다른 모듈과의 대조 -----------------------------------------------------


def test_price_indicator_blocks_match_feature_set(engine):
    """가격 지표 블록의 식별자 = 판단 계층이 쓰는 피처 이름.

    한 글자라도 다르면 전략서에 적힌 지표를 판단 계층이 찾지 못한다.
    예전 예시 파일에 있던 momentum_20d · volume_ratio_20d 가 그런 경우였다.
    """
    features = set(FEATURE_SETS[CURRENT_FEATURE_SET_VERSION])
    block_ids = {block.block_id for block in bbl.list_blocks("indicator")}

    assert features <= block_ids
    # 피처가 아닌 지표 블록은 거시지표뿐이어야 한다.
    assert block_ids - features == set(macro_codes.TREND_INDEX_CODES) | set(
        macro_codes.VOLATILITY_INDEX_CODES
    )


def test_macro_indicator_blocks_point_at_the_right_slot(engine):
    """거시지표는 전략서에 자리가 둘뿐이다 — 추세 지수와 변동성 지수.

    수집 중인 네 개 중 나머지 둘(환율·신용 스프레드)은 국면 판정이 내부에서 쓰는
    입력이고 전략서에 자리가 없다. 넣으면 잘못된 자리에 박힐 수 있다.
    """
    for code in macro_codes.TREND_INDEX_CODES:
        block = bbl.get_block(code)
        assert block is not None, code
        assert block.params_schema["slot"].endswith("trend_index")

    for code in macro_codes.VOLATILITY_INDEX_CODES:
        block = bbl.get_block(code)
        assert block is not None, code
        assert block.params_schema["slot"].endswith("volatility_index")

    for code in (macro_codes.FX_CODE, macro_codes.CREDIT_SPREAD_CODE):
        assert bbl.get_block(code) is None, code


def test_rebalance_blocks_fit_the_spec_contract(engine):
    """리밸런싱 블록의 params_schema 를 그대로 전략서 자리에 넣으면 계약을 통과해야 한다."""
    for block in bbl.list_blocks("rebalance"):
        rule = RebalanceRule(**block.params_schema)
        assert rule.trigger.type
        assert rule.trigger.freq


def test_rebalance_blocks_respect_the_hardcap_interval(engine):
    """하드캡의 최소 간격(5일)보다 짧은 주기는 넣지 않는다 — 넣어도 나중에 접힌다."""
    from app.repositories import presets

    floor = presets.get_active_hardcap()["min_interval_days"]

    for block in bbl.list_blocks("rebalance"):
        assert block.params_schema["min_interval_days"] >= floor, block.block_id


def test_filter_blocks_reference_real_columns(engine):
    """종목 표에 실제로 값이 들어 있는 칸만 거른다.

    보수율·최대낙폭은 비어 있고 국가는 칸 자체가 없다(README 알려진 문제).
    """
    usable = {"ticker", "name", "sector", "group_id", "risk_tag", "is_leveraged", "active"}

    for block in bbl.list_blocks("filter"):
        assert block.params_schema["column"] in usable, block.block_id


# --- 태그 규약 --------------------------------------------------------------


def test_every_tag_uses_an_allowed_prefix(engine):
    for block in bbl.list_blocks():
        for tag in block.tags:
            assert tag.startswith(bbl.TAG_PREFIXES), f"{block.block_id}: {tag}"


def test_group_tags_match_real_asset_groups(engine):
    """asset: · sector: · country: 값은 자산군 표의 묶음 이름을 소문자로 바꾼 것이다.

    지어내면 종목을 거를 때 맞지 않는다.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        groups = {row[0].lower() for row in conn.execute(text("SELECT group_id FROM asset_groups"))}

    for block in bbl.list_blocks():
        for tag in block.tags:
            prefix, _, value = tag.partition(":")
            if prefix == "asset":
                assert value in groups, tag
            elif prefix in ("sector", "country"):
                assert f"{prefix}_{value}" in groups, tag


def test_risk_tags_use_three_levels(engine):
    values = {
        tag.split(":", 1)[1]
        for block in bbl.list_blocks()
        for tag in block.tags
        if tag.startswith("risk:")
    }

    assert values <= {"low", "mid", "high"}


def test_no_block_is_overtagged(engine):
    """태그를 다 달면 검색이 전부 걸려 쓸모가 없어진다."""
    for block in bbl.list_blocks():
        assert len(block.tags) <= MAX_TAGS_PER_BLOCK, f"{block.block_id}: {block.tags}"


# --- 검색 -------------------------------------------------------------------


def test_search_matches_by_tag(engine):
    found = {block.block_id for block in bbl.search_blocks(["kw:배당"])}

    assert "ET_DIVIDEND" in found
    assert "ET_COVERED_CALL" in found


def test_search_ranks_more_matches_first(engine):
    results = bbl.search_blocks(["kw:안전하게", "kw:보수적"])

    assert results[0].matched_tags == 2
    assert [block.matched_tags for block in results] == sorted(
        (block.matched_tags for block in results), reverse=True
    )


def test_search_is_deterministic(engine):
    """같은 질의에 순서가 흔들리면 같은 입력에 다른 전략서가 나온다."""
    tags = ["kw:안전하게", "kw:채권", "risk:low"]

    first = [block.block_id for block in bbl.search_blocks(tags)]
    second = [block.block_id for block in bbl.search_blocks(tags)]

    assert first == second
    # 동점일 때 식별자 순으로 묶여 있어야 한다.
    for score in {block.matched_tags for block in bbl.search_blocks(tags)}:
        tied = [b.block_id for b in bbl.search_blocks(tags) if b.matched_tags == score]
        assert tied == sorted(tied)


def test_search_can_narrow_by_type(engine):
    results = bbl.search_blocks(["risk:low"], block_type="rebalance")

    assert results
    assert {block.block_type for block in results} == {"rebalance"}


def test_search_without_tags_returns_nothing(engine):
    """아무 태그도 없는데 전부 돌려주면 '찾았다'와 '못 찾았다'가 구분되지 않는다."""
    assert bbl.search_blocks([]) == []


def test_unknown_tag_finds_nothing(engine):
    assert bbl.search_blocks(["kw:없는말"]) == []
