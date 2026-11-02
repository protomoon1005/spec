"""판정, 되묻기 질문, 답 반영(app/m1/decide.py) 테스트. AI 없이 돈다."""

from __future__ import annotations

from app.m1 import assemble, decide, spans
from app.m1.assemble import Ask, Assembly, Reject

DICTIONARY = spans.load_dictionary()
INDEX = spans.InstrumentIndex(
    [("005930", "삼성전자"), ("000660", "SK하이닉스"), ("069500", "KODEX 200")], DICTIONARY
)


def _find(text):
    return spans.find_spans(text, DICTIONARY, INDEX)


def _assembly(asks=(), rejects=()):
    return Assembly({}, None, list(asks), list(rejects), [], {})


def test_precheck_rejects_dictionary_words_unless_negated():
    assert decide.precheck(_find("삼성전자 MACD 골든크로스면 사")).code == "UNKNOWN_INDICATOR"
    assert decide.precheck(_find("삼성전자 공매도 쳐줘")).code == "UNSUPPORTED_ACTION"
    assert decide.precheck(_find("MACD 말고 RSI 30 밑이면 삼성전자 사")) is None


def test_precheck_out_of_domain():
    assert decide.precheck(_find("엑셀에서 VLOOKUP 쓰는 법 알려줘")).code == "OUT_OF_DOMAIN"
    assert decide.precheck(_find("파이썬으로 네이버 뉴스 크롤러 짜줘")).code == "OUT_OF_DOMAIN"
    # 종목과 비중 숫자만 있어도, "백테스트" 같은 말만 있어도 전략 이야기다
    assert decide.precheck(_find("삼성전자 50% SK하이닉스 40%")) is None
    assert decide.precheck(_find("RSI 30 밑에서 사고 70 위에서 파는 전략 백테스트 해줘")) is None


def test_reject_comes_before_asks():
    built = _assembly([Ask("exit", "rules[0].exit", 0)], [Reject("UNKNOWN_INDICATOR", "스토캐스틱", 5)])
    result = decide.decide(built, rounds_asked=0)
    assert result.status == "reject" and result.rejection.code == "UNKNOWN_INDICATOR"


def test_ask_order_and_three_per_round():
    asks = [
        Ask("threshold", "rules[0].entry.items[0]", 10, {"indicator": "rsi", "direction": "down"}),
        Ask("exit", "rules[0].exit", 3),
        Ask("universe", "universe", 50, {"reason": "none", "has_rules": True}),
        Ask("rebalance_not_allowed", "rebalance", 1, {"said": "half_year"}),
        Ask("ma_window", "span4", 20),
    ]
    result = decide.decide(_assembly(asks), rounds_asked=0)
    assert result.status == "ask" and result.round == 1
    assert [q.ask.kind for q in result.questions] == ["universe", "exit", "threshold"]
    assert len(result.all_asks) == 5


def test_deferred_questions_wait_for_the_next_round():
    asks = [
        Ask("universe", "universe", 0, {"reason": "none"}),
        Ask("weight_vs_cap", "weights", 5),
        Ask("threshold", "rules[0].entry.items[0]", 9, {"indicator": "rsi"}),
        Ask("always_both_true", "rules[0]", 2),
    ]
    result = decide.decide(_assembly(asks), rounds_asked=0)
    assert [q.ask.kind for q in result.questions] == ["universe", "threshold"]


def test_round_limits_turn_into_unresolved():
    four = [Ask("exit", f"rules[{i}].exit", i) for i in range(4)]
    assert decide.decide(_assembly(four), rounds_asked=1).rejection.code == "UNRESOLVED"
    assert decide.decide(_assembly(four[:3]), rounds_asked=1).status == "ask"
    assert decide.decide(_assembly(four[:1]), rounds_asked=2).rejection.code == "UNRESOLVED"
    assert decide.decide(_assembly([]), rounds_asked=2).status == "ok"


def test_question_texts_and_choices():
    def q(ask):
        return decide.question_for(ask, "q1")

    exit_q = q(Ask("exit", "rules[0].exit", 0))
    assert exit_q.text == "언제 팔까요?"
    assert [c.value for c in exit_q.choices] == ["reverse", "take_profit_10", "hold"]
    window = q(
        Ask(
            "window_not_allowed",
            "span2",
            0,
            {"indicator": "ma", "window": 7, "allowed": [5, 10, 20, 60, 120]},
        )
    )
    assert window.text == "7일선은 없어요. 5일선과 10일선 중 어느 쪽인가요?"
    rsi = q(Ask("window_not_allowed", "span2", 0, {"indicator": "rsi", "window": 9, "allowed": [14]}))
    assert rsi.text == "RSI는 14일만 써요. 14일로 할까요?"
    threshold = q(Ask("threshold", "p", 0, {"indicator": "ret", "direction": "down"}))
    assert [c.label for c in threshold.choices] == ["-5%", "-10%", "-20%"]
    universe = q(Ask("universe", "universe", 0, {"reason": "none", "has_rules": True}))
    assert universe.choices[0].value == ["069500"]
    public = exit_q.public()
    assert public["choices"][0] == {"id": "c1", "label": "살 조건과 반대가 되면 팔기"}
    assert "value" not in str(public)


def test_answers_become_overrides():
    questions = [
        decide.question_for(Ask("exit", "rules[0].exit", 0), "q1"),
        decide.question_for(Ask("ma_window", "span2", 0), "q2"),
        decide.question_for(Ask("universe", "universe", 0, {"reason": "none"}), "q3"),
    ]
    out = decide.overrides_from_answers(
        questions,
        [
            {"question_id": "q1", "choice_id": "c3"},
            {"question_id": "q2", "text": "20일 정도로"},
            {"question_id": "q3", "text": "SK하이닉스로 해줘"},
            {"question_id": "q9", "choice_id": "c1"},
        ],
        read_text=lambda ask, text: decide.read_answer_text(ask, text, _find),
    )
    assert out == {"exit:rules[0].exit": "hold", "ma_window:span2": 20, "universe:universe": ["000660"]}


def test_read_answer_text_for_threshold_and_direction():
    ask = Ask("threshold", "p", 0, {"indicator": "ret", "direction": "down"})
    assert decide.read_answer_text(ask, "-10%", _find) == -0.1
    assert decide.read_answer_text(Ask("direction", "p", 0), "아래로 떨어지면", _find) == "cross_below"
    assert decide.read_answer_text(Ask("exit", "p", 0), "글쎄요", _find) is None


def test_end_to_end_without_ai():
    # 조립에서 나온 되물을 거리가 질문이 되고, 답을 넣어 다시 조립하면 바로 진행이 된다
    from app.m1 import pairing

    text = "삼성전자 RSI 30 아래로 내려가면 매수"
    result = _find(text)
    raw = {
        "universe": ["1:삼성전자"],
        "weights": [],
        "rebalance": [],
        "max_weight": None,
        "selection": [],
        "backtest": {"period": [], "capital": [], "dividends": []},
        "quotes": [],
        "rules": [
            {
                "target": "each",
                "exit": None,
                "entry": {
                    "actions": ["5:매수"],
                    "join": "one",
                    "items": [{"subject": None, "left": "2:RSI", "op": "4:아래로 내려가면", "right": "3:30"}],
                },
            }
        ],
    }
    p = pairing.normalize(pairing.parse(raw, result))
    first = decide.decide(assemble.assemble(result, p), rounds_asked=0)
    assert [q.text for q in first.questions] == ["언제 팔까요?"]
    overrides = decide.overrides_from_answers(first.questions, [{"question_id": "q1", "choice_id": "c1"}])
    built = assemble.assemble(result, p, overrides=overrides)
    assert decide.decide(built, rounds_asked=1).status == "ok"
    assert built.spec.rules[0].exit.op == "cross_above"
