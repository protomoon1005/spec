"""종목 후보 선정 (M1 4단계).

postgres 와 시드(기준표·종목·거절 장면용 종목)가 적재된 상태여야 한다.

**위험등급을 못 믿는 상태에서 쓴 테스트다.** 173종목의 등급이 아직 자동 추정값이라
재배정 예정이므로, 특정 종목이 어느 등급인지에 기대지 않는다. 대신 등급을 표에서
읽어 와 "그 등급의 프리셋 상한과 맞는가" 를 본다. 재배정돼도 안 깨진다.
"""
from __future__ import annotations

import pytest

from app.m1 import candidates as C
from app.repositories import etf_master, presets

UNKNOWN = "999999"


def _grade(ticker: str) -> str:
    return etf_master.get_by_tickers([ticker])[ticker].risk_tag


def _level_allowing(ticker: str) -> int:
    """이 종목을 담을 수 있는 가장 낮은 성향. 없으면 건너뛴다."""
    grade = _grade(ticker)
    for level in (1, 2, 3, 4, 5):
        if presets.get_asset_bounds(level)[grade].allowed_max > 0:
            return level
    pytest.skip(f"{ticker} 는 어느 성향도 담을 수 없다 (등급 {grade})")


# --- 거절 사유 --------------------------------------------------------------


def test_unknown_ticker_is_reported_not_silently_dropped(engine):
    """조용히 빼면 '그런 종목이 없다' 와 '규칙이 막았다' 를 구분할 수 없다."""
    result = C.select(risk_level=5, requested_tickers=[UNKNOWN])

    assert [r.ticker for r in result.rejected] == [UNKNOWN]
    assert result.rejected[0].reason == C.REASON_NOT_FOUND


def test_delisted_ticker_is_rejected_with_its_own_reason(engine, blocked_etfs):
    result = C.select(risk_level=5, requested_tickers=[blocked_etfs["delisted"]])

    assert result.rejected[0].reason == C.REASON_DELISTED
    assert result.rejected[0].name is not None


def test_leveraged_ticker_is_rejected_even_for_the_boldest_profile(engine, blocked_etfs):
    """레버리지는 하드캡이 금지한다 — 성향과 무관하다."""
    leveraged = blocked_etfs["leveraged"]

    result = C.select(risk_level=5, requested_tickers=[leveraged])

    assert result.rejected[0].reason == C.REASON_LEVERAGE_FORBIDDEN
    assert leveraged not in result.tickers


def test_grade_out_of_reach_is_rejected(engine):
    """그 성향이 담을 수 없는 등급이면 사유가 달라야 한다."""
    blocked = next(
        record
        for record in etf_master.search(limit=300)
        if presets.get_asset_bounds(1)[record.risk_tag].allowed_max == 0
    )

    result = C.select(risk_level=1, requested_tickers=[blocked.ticker])

    assert result.rejected[0].reason == C.REASON_PRESET_ZERO


def test_only_one_reason_is_reported(engine, blocked_etfs):
    """사유가 하나여야 사용자에게 설명이 된다. 앞에서 걸리면 뒤는 보지 않는다."""
    result = C.select(
        risk_level=1,
        requested_tickers=[blocked_etfs["leveraged"], blocked_etfs["delisted"], UNKNOWN],
    )

    assert len(result.rejected) == 3
    assert len({r.reason for r in result.rejected}) == 3


def test_unrequested_tickers_are_not_listed_as_rejected(engine):
    """지목하지 않은 173종목의 탈락 사유를 다 적을 이유가 없다."""
    result = C.select(risk_level=1, sectors=["SECTOR_SEMICONDUCTOR"])

    assert result.rejected == ()


# --- 후보 구성 --------------------------------------------------------------


def test_requested_tickers_come_first(engine):
    ticker = "148070"
    level = _level_allowing(ticker)

    result = C.select(risk_level=level, requested_tickers=[ticker], target=5)

    assert result.candidates[0].ticker == ticker
    assert result.candidates[0].requested is True
    assert all(c.requested is False for c in result.candidates[1:])


def test_fills_up_to_target(engine):
    result = C.select(risk_level=5, target=7)

    assert len(result.candidates) == 7


def test_bounds_come_from_the_preset_table(engine):
    result = C.select(risk_level=3, target=5)
    bounds = presets.get_asset_bounds(3)

    for candidate in result.candidates:
        expected = bounds[candidate.risk_tag]
        assert candidate.weight_min == expected.allowed_min
        assert candidate.weight_max == expected.allowed_max
        assert candidate.preset_id == expected.preset_id


def test_no_candidate_is_unholdable(engine):
    """상한 0 인 종목이 후보에 남으면 LLM 에게 '고를 수 있는데 못 쓴다' 를 준다."""
    for level in (1, 3, 5):
        for candidate in C.select(risk_level=level, target=20).candidates:
            assert candidate.weight_max > 0, (level, candidate.ticker)


def test_conservative_profile_gets_fewer_choices(engine):
    """성향이 보수적일수록 담을 수 있는 종목이 줄어든다."""
    conservative = len(C.select(risk_level=1, target=300).candidates)
    bold = len(C.select(risk_level=5, target=300).candidates)

    assert conservative < bold


def test_fill_avoids_the_unclassified_bucket(engine):
    """미분류 버킷은 173종목 중 138개라, 그걸로 보충하면 사실상 '아무거나' 가 된다."""
    semiconductor = etf_master.search(sectors=["SECTOR_SEMICONDUCTOR"])[0]

    result = C.select(risk_level=5, requested_tickers=[semiconductor.ticker], target=6)

    assert {c.sector for c in result.candidates} == {"SECTOR_SEMICONDUCTOR"}


def test_leveraged_products_never_appear_in_the_fill(engine, blocked_etfs):
    result = C.select(risk_level=5, target=300)

    assert blocked_etfs["leveraged"] not in result.tickers


# --- 재현성 -----------------------------------------------------------------


def test_same_request_gives_the_same_candidates(engine):
    kwargs = {"risk_level": 3, "requested_tickers": ["148070"], "target": 8}

    first = C.select(**kwargs)
    second = C.select(**kwargs)

    assert first.tickers == second.tickers
    assert [r.reason for r in first.rejected] == [r.reason for r in second.rejected]


def test_duplicate_requested_tickers_are_collapsed(engine):
    ticker = "148070"
    level = _level_allowing(ticker)

    result = C.select(risk_level=level, requested_tickers=[ticker, ticker], target=3)

    assert result.tickers.count(ticker) == 1


def test_preset_version_is_recorded(engine):
    result = C.select(risk_level=3, target=3)

    assert result.preset_version == presets.active_preset_version()
