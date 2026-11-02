"""조각 찾기(app/m1/spans.py)와 표현 표(db/seeds/expressions.yaml) 테스트. DB 없이 돈다."""
from __future__ import annotations

import pytest

from app.m1 import spans

DICTIONARY = spans.load_dictionary()
RECORDS = [
    ("005930", "삼성전자"), ("005935", "삼성전자우"), ("000660", "SK하이닉스"), ("069500", "KODEX 200"),
    ("229200", "KODEX 코스닥150"), ("132030", "KODEX 골드선물(H)"), ("001680", "대상"),
    ("396500", "TIGER 반도체TOP10"),
]
INDEX = spans.InstrumentIndex(RECORDS, DICTIONARY)


def _find(text: str) -> spans.SpanResult:
    return spans.find_spans(text, DICTIONARY, INDEX)


def _examples():
    for entry in DICTIONARY.entries:
        for example in entry.examples:
            yield pytest.param(entry.id, example, id=f"{entry.id}:{example}")


@pytest.mark.parametrize(("entry_id", "example"), list(_examples()))
def test_every_example_is_one_span_of_its_entry(entry_id, example):
    result = spans.find_spans(example, DICTIONARY)
    assert [(s.entry_id, s.text) for s in result.spans] == [(entry_id, spans.normalize(example))]


def test_representative_sentence():
    result = _find("삼성전자 RSI 30 아래로 떨어지면 사고 5일선 깨지면 팔아줘")
    assert [s.label() for s in result.spans] == [
        "1:삼성전자", "2:RSI", "3:30", "4:아래로 떨어지면", "5:사고", "6:5일선", "7:깨지면", "8:팔아줘",
    ]
    by_id = {s.id: s for s in result.spans}
    assert by_id[1].meaning == {"ticker": "005930"}
    assert by_id[4].meaning == {"op": "cross_below"}
    assert by_id[6].meaning == {"indicator": "ma", "window": 5}
    assert by_id[3].meaning == {"value": 30, "unit": "none"}
    assert result.dictionary_version == DICTIONARY.version


def test_longest_span_wins():
    result = _find("KODEX 골드선물(H) 사고, 비트코인은 안 돼")
    pairs = [(s.kind, s.text) for s in result.spans]
    assert pairs[:2] == [("instrument", "KODEX 골드선물(H)"), ("action", "사고")]
    assert ("reject", "비트코인") in pairs
    moved = [s.text for s in _find("20일 이평 밑으로 빠지면 팔아").spans]
    assert moved == ["20일 이평", "밑으로 빠지면", "팔아"]


@pytest.mark.parametrize(
    ("text", "ticker"),
    [
        ("kodex 코스닥150 사", "229200"),
        ("코덱스 200 사", "069500"),
        ("KODEX200 사", "069500"),
        ("하이닉스랑 삼성전자", "000660"),
        ("TIGER 반도체 TOP10 사", "396500"),
        ("삼성전자우 사", "005935"),
    ],
)
def test_instrument_names(text, ticker):
    first = _find(text).spans[0]
    assert first.kind == "instrument"
    assert first.meaning["ticker"] == ticker


def test_instrument_boundaries():
    # 이름 뒤에 다른 낱말이 붙거나, 흔한 낱말(대상)이면 종목으로 읽지 않는다
    assert [s.meaning["ticker"] for s in _find("삼성전자랑 SK하이닉스는").by_kind("instrument")] == [
        "005930", "000660",
    ]
    assert _find("대상 종목은 삼성전자").by_kind("instrument")[0].meaning["ticker"] == "005930"
    assert len(_find("대상 종목은 삼성전자").by_kind("instrument")) == 1


def test_short_words_need_boundaries():
    # "회사" 의 "사" 를 사기로, "위험" 의 "위" 를 위치로 읽지 않는다
    assert _find("회사가 위험하면").by_kind("action", "verb") == []


def test_numbers_keep_their_units():
    result = _find("산 가격 대비 -8%면 손절, 시작금액 1억, 2024년 한 해만")
    meanings = {s.text: dict(s.meaning) for s in result.spans}
    assert meanings["-8%"] == {"value": -8, "unit": "percent"}
    assert meanings["1억"] == {"value": 1, "scale": "억", "unit": "won"}
    assert meanings["2024년 한 해만"] == {"year": 2024}
    assert meanings["손절"]["exit_kind"] == "stop_loss"


def test_negation_cancels_the_span_right_before():
    result = _find("MACD 말고 RSI로, 배당은 빼고")
    negated = spans.negated_span_ids(result)
    assert {s.text for s in result.spans if s.id in negated} == {"MACD", "배당"}
    assert spans.negated_span_ids(_find("MACD 골든크로스면 사")) == set()


def test_normalize():
    assert spans.normalize("  삼성전자　 RSI\n30 ") == "삼성전자 RSI 30"
    assert spans.normalize("ＲＳＩ") == "RSI"


def test_dictionary_version_is_stable():
    assert spans.load_dictionary().version == DICTIONARY.version
    assert len(DICTIONARY.version) == 12
