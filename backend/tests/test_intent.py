"""자연어 의도 추출 (M1 4단계).

앞쪽은 LLM 없이 돈다 — 스키마 조립과 결과 해석은 결정적이어야 하는 구간이다.
맨 뒤 한 개만 실제 LLM 을 부르고 `requires_ollama` 가 붙어 있다.
"""
from __future__ import annotations

import pytest

from app.m1 import intent as I
from app.repositories import bbl, presets


@pytest.fixture
def schema(engine):
    return I.build_intent_schema()


# --- 고를 수 있는 값을 스키마가 제한한다 -------------------------------------


def test_keyword_choices_come_from_the_tag_vocabulary(engine, schema):
    """자유 문자열을 받으면 사전에 없는 말이 들어와 아무 블록도 안 잡힌다."""
    assert set(schema["properties"]["keywords"]["items"]["enum"]) == set(bbl.list_tags("kw:"))


def test_sector_and_group_choices_come_from_the_group_table(engine, schema):
    sectors = {g for g in presets.list_group_ids(2) if g.startswith("SECTOR_")}

    assert set(schema["properties"]["sectors"]["items"]["enum"]) == sectors
    assert set(schema["properties"]["asset_groups"]["items"]["enum"]) == set(presets.list_group_ids(1))


def test_names_stay_free_text(engine, schema):
    """종목은 176개라 목록으로 주기엔 많고, 코드를 지어내게 두면 없는 코드가 나온다."""
    assert schema["properties"]["mentioned_names"]["items"] == {"type": "string"}


# --- 목록 밖의 값을 한 번 더 거른다 -------------------------------------------


def test_values_outside_the_vocabulary_are_dropped(engine):
    """Ollama 는 형식 강제가 약하다. 스키마를 믿고 그냥 쓰면 없는 업종으로 종목을 찾게 된다."""
    intent = I.from_payload(
        {
            "keywords": ["배당", "그런거없음"],
            "sectors": ["SECTOR_SEMICONDUCTOR", "SECTOR_우주"],
            "asset_groups": ["BOND", "CRYPTO"],
            "mentioned_names": ["KODEX 200"],
        }
    )

    assert intent.keywords == ("배당",)
    assert intent.sectors == ("SECTOR_SEMICONDUCTOR",)
    assert intent.asset_groups == ("BOND",)


def test_garbage_payload_does_not_crash(engine):
    intent = I.from_payload({"keywords": "배당", "sectors": None, "mentioned_names": [1, None]})

    assert intent == I.Intent()


def test_duplicates_are_collapsed(engine):
    intent = I.from_payload({"keywords": ["배당", "배당"], "mentioned_names": []})

    assert intent.keywords == ("배당",)


def test_too_many_names_are_cut(engine):
    intent = I.from_payload({"mentioned_names": [f"이름{i}" for i in range(20)]})

    assert len(intent.mentioned_names) == I.MAX_NAME_MENTIONS


# --- 의도를 태그와 종목으로 옮긴다 -------------------------------------------


def test_resolve_builds_tag_queries_with_prefixes(engine):
    resolved = I.resolve(
        I.Intent(keywords=("배당",), sectors=("SECTOR_SEMICONDUCTOR",), asset_groups=("BOND",))
    )

    assert resolved.tag_queries == ("kw:배당", "sector:semiconductor", "asset:bond")


def test_tag_queries_actually_match_blocks(engine):
    """접두어 규약이 어긋나면 태그가 만들어져도 아무 블록도 안 잡힌다."""
    resolved = I.resolve(I.Intent(keywords=("배당",), asset_groups=("BOND",)))

    assert bbl.search_blocks(list(resolved.tag_queries))


def test_exact_name_wins_over_partial_matches(engine):
    """KODEX 200 은 부분일치로 넷이 걸린다(200TR · 200IT TR · 200미국채혼합50).
    정확 일치를 안 보면 멀쩡한 요청이 '못 찾았다' 로 떨어진다."""
    resolved = I.resolve(I.Intent(mentioned_names=("KODEX 200",)))

    assert resolved.requested_tickers == ("069500",)
    assert resolved.unresolved_names == ()


def test_spacing_and_case_do_not_matter(engine):
    """사람이 종목명을 정확히 띄어 쓰지 않는다."""
    assert I.resolve(I.Intent(mentioned_names=("kodex200",))).requested_tickers == ("069500",)


def test_ticker_code_is_accepted_directly(engine):
    assert I.resolve(I.Intent(mentioned_names=("069500",))).requested_tickers == ("069500",)


def test_ambiguous_name_is_left_unresolved(engine):
    """엉뚱한 종목을 담는 것보다 되묻는 편이 낫다."""
    resolved = I.resolve(I.Intent(mentioned_names=("국고채",)))

    assert resolved.requested_tickers == ()
    assert resolved.unresolved_names == ("국고채",)


def test_unknown_name_is_reported(engine):
    resolved = I.resolve(I.Intent(mentioned_names=("없는이름",)))

    assert resolved.unresolved_names == ("없는이름",)


def test_delisted_name_still_resolves(engine, blocked_etfs):
    """지목한 종목이 폐지됐다는 것을 후보 선정이 판정해야 한다.
    여기서 못 찾은 것으로 처리하면 '없다' 가 되어 거절 사유가 달라진다."""
    delisted = blocked_etfs["delisted"]

    resolved = I.resolve(I.Intent(mentioned_names=(delisted,)))

    assert resolved.requested_tickers == (delisted,)


def test_resolve_is_deterministic(engine):
    intent = I.Intent(keywords=("배당", "안전하게"), mentioned_names=("KODEX 200", "069500"))

    assert I.resolve(intent) == I.resolve(intent)


# --- 실제 LLM ---------------------------------------------------------------


@pytest.mark.requires_ollama
def test_extract_returns_vocabulary_values(engine):
    """호스트 Ollama 로 실제 한 번 돌린다. 값이 사전 안에 있기만 하면 통과다 —
    어떤 낱말을 고르는지는 모델이 정하는 것이라 고정하지 않는다."""
    from app.llm.client import get_llm_client

    intent = I.extract("반도체 위주로 공격적으로 굴리고 매달 정리해줘", get_llm_client())

    allowed = set(bbl.list_tags("kw:"))
    sectors = {g for g in presets.list_group_ids(2) if g.startswith("SECTOR_")}
    assert set(intent.keywords) <= allowed
    assert set(intent.sectors) <= sectors
