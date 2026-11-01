"""종목 후보 선정 (M1 4단계).

postgres 와 시드(종목 원장)가 적재된 상태여야 한다. 특정 종목의 분류에 기대지 않고
표에서 읽어 와 확인한다 — 원장이 바뀌어도 안 깨진다.

시스템 상한(성향, 하드캡)이 없으므로 후보의 비중 범위는 전부 0 부터 1 이고, 막히는 경우는
없는 종목과 상장폐지 종목뿐이다(docs/pivot-plan.md 결정 3).
"""
from __future__ import annotations

from app.m1 import candidates as C
from app.m1 import intent as I
from app.repositories import etf_master

UNKNOWN = "999999"


# --- 거절 사유 --------------------------------------------------------------


def test_unknown_ticker_is_reported_not_silently_dropped(engine):
    """조용히 빼면 '그런 종목이 없다' 와 '거래할 수 없다' 를 구분할 수 없다."""
    result = C.select(requested_tickers=[UNKNOWN])

    assert [r.ticker for r in result.rejected] == [UNKNOWN]
    assert result.rejected[0].reason == C.REASON_NOT_FOUND


def test_delisted_ticker_is_rejected_with_its_own_reason(engine, blocked_etfs):
    result = C.select(requested_tickers=[blocked_etfs["delisted"]])

    assert result.rejected[0].reason == C.REASON_DELISTED
    assert result.rejected[0].name is not None


def test_requested_leveraged_ticker_is_accepted(engine, blocked_etfs):
    """시스템이 레버리지를 막지 않는다. 사용자가 지목하면 담는다."""
    leveraged = blocked_etfs["leveraged"]

    result = C.select(requested_tickers=[leveraged])

    assert result.rejected == ()
    assert result.candidates[0].ticker == leveraged


def test_only_one_reason_is_reported_per_ticker(engine, blocked_etfs):
    result = C.select(requested_tickers=[blocked_etfs["delisted"], UNKNOWN])

    assert [r.code for r in result.rejected] == [C.CODE_DELISTED, C.CODE_NOT_FOUND]


def test_unrequested_tickers_are_not_listed_as_rejected(engine):
    """지목하지 않은 종목의 탈락 사유를 다 적을 이유가 없다."""
    result = C.select(sectors=["SECTOR_SEMICONDUCTOR"])

    assert result.rejected == ()


# --- 후보 구성 --------------------------------------------------------------


def test_requested_tickers_come_first(engine):
    ticker = "148070"

    result = C.select(requested_tickers=[ticker], target=5)

    assert result.candidates[0].ticker == ticker
    assert result.candidates[0].requested is True
    assert all(c.requested is False for c in result.candidates[1:])


def test_fills_up_to_target(engine):
    result = C.select(target=7)

    assert len(result.candidates) == 7


def test_every_candidate_gets_the_full_range(engine):
    for candidate in C.select(target=20).candidates:
        assert (candidate.weight_min, candidate.weight_max) == (C.WEIGHT_MIN, C.WEIGHT_MAX)


def test_fill_avoids_the_unclassified_bucket(engine):
    """미분류 버킷으로 보충하면 사실상 '아무거나' 가 된다."""
    semiconductor = etf_master.search(sectors=["SECTOR_SEMICONDUCTOR"])[0]

    result = C.select(requested_tickers=[semiconductor.ticker], target=6)

    assert {c.sector_group_id for c in result.candidates} == {"SECTOR_SEMICONDUCTOR"}


def test_leveraged_products_never_appear_in_the_fill(engine, blocked_etfs):
    """지목하지 않은 레버리지를 보충으로 끼워 넣지 않는다 — 사용자가 말하지 않은 위험이다."""
    result = C.select(target=300)

    assert blocked_etfs["leveraged"] not in result.tickers


# --- 재현성 -----------------------------------------------------------------


def test_same_request_gives_the_same_candidates(engine):
    kwargs = {"requested_tickers": ["148070"], "target": 8}

    first = C.select(**kwargs)
    second = C.select(**kwargs)

    assert first.tickers == second.tickers
    assert [r.reason for r in first.rejected] == [r.reason for r in second.rejected]


def test_duplicate_requested_tickers_are_collapsed(engine):
    ticker = "148070"

    result = C.select(requested_tickers=[ticker, ticker], target=3)

    assert result.tickers.count(ticker) == 1


# --- 요청한 ETF 특성 (2026-10-21) ---------------------------------------------


def test_trait_matches_come_first_and_are_marked(engine):
    # "배당 나오는 ETF" — 배당 블록의 이름 규칙에 맞는 종목이 맨 앞에 온다
    resolved = I.resolve(I.Intent(keywords=("배당",)))
    result = C.select(traits=list(resolved.traits))

    marked = [c for c in result.candidates if c.trait]
    assert list(result.candidates[: len(marked)]) == marked
    # "배당" 말에는 배당 블록과 커버드콜 블록이 같이 걸린다. 특성마다 이름 규칙에 맞는 종목만 붙는다
    assert marked and {c.trait for c in marked} <= {title for title, _pattern in resolved.traits}
    assert all(c.trait in c.name for c in marked)


def test_traits_never_empty_the_candidates(engine):
    # 특성과 업종이 겹치지 않아도("안전하게" → 국채, 반도체) 걸러 내지 않고 예전처럼 채운다
    resolved = I.resolve(I.Intent(keywords=("안전하게",), sectors=("SECTOR_SEMICONDUCTOR",)))
    plain = C.select(sectors=["SECTOR_SEMICONDUCTOR"])
    with_trait = C.select(sectors=["SECTOR_SEMICONDUCTOR"], traits=list(resolved.traits))

    assert resolved.traits  # 국채 블록이 걸리긴 한다
    assert with_trait.tickers == plain.tickers


def test_only_spoken_keywords_pick_traits(engine):
    # asset:bond 태그만으로는 국채 · 초단기 금리 블록을 걸지 않는다 — 사용자가 한 말(kw:)만 본다
    assert I.resolve(I.Intent(asset_groups=("BOND",))).traits == ()


def test_prompt_marks_trait_candidates(engine):
    from app.m1 import prompt

    resolved = I.resolve(I.Intent(keywords=("배당",)))
    text = prompt.build_compile_prompt(
        user_text="배당 나오는 ETF 위주로 담아줘",
        candidate_set=C.select(traits=list(resolved.traits)),
    )

    assert "(요청한 특성: 배당)" in text
    assert "빼지 말고 담아라" in text
