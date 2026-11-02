"""판정 규칙 (core.decide). LLM 없이 빈칸 상태를 직접 넣어 판정을 단언한다."""
import pytest

from core.decide import DEFAULTS, MAX_QUESTIONS, decide
from slots import (MA5_BREAK, RSI30_CROSS, SAMSUNG, asset, assets, basic, cmp, cond, fill, op, other, rt, slot,
                   val)


def ids(d):
    return [q.id for q in d.questions]


def only_q(d):
    assert d.verdict == "clarify", (d.verdict, d.rejection)
    assert len(d.questions) == 1, [q.text for q in d.questions]
    return d.questions[0]


# ---------------------------------------------------------------- 진행

def test_proceed_basic():
    d = decide(basic())
    assert d.verdict == "proceed" and d.questions == [] and d.rejection is None
    r = d.resolved
    assert r.assets == [{"ticker": "005930", "name": "삼성전자", "weight": None, "evidence": "삼성전자",
                         "source": "text", "weight_evidence": "삼성전자 삼성전자"}]
    assert r.entry.items[0].left == {"indicator": "rsi", "window": 14, "of": "self"}
    assert r.entry.items[0].op == "cross_below" and r.entry.items[0].right == {"value": 30}
    assert r.exit.items[0].right == {"indicator": "ma", "window": 5, "of": "self"}


def test_deterministic():
    a, b = decide(basic()), decide(basic())
    assert a.resolved == b.resolved and a.verdict == b.verdict


# ---------------------------------------------------------------- 거절이 되묻기보다 먼저

def test_reject_beats_clarify():
    s = fill(assets=assets(),   # 종목 말 안 함 → 원래는 되묻기
             entry=cond(cmp("MACD 골든크로스 나오면", op("MACD"), "cross_above", rt(), status="unsupported",
                            kind="unknown_indicator")),
             exit=cond(cmp("RSI 높을 때", op("rsi"), ">", rt(), status="ambiguous")))
    d = decide(s)
    assert d.verdict == "reject" and d.questions == []
    assert d.rejection.code == "UNKNOWN_INDICATOR"
    assert d.rejection.evidence == "MACD 골든크로스 나오면"
    assert "MACD" in d.rejection.detail


@pytest.mark.parametrize("kind,code", [
    ("future_info", "FUTURE_REF"), ("trade_method", "UNSUPPORTED_ACTION"), ("intraday", "INTRADAY"),
    ("not_investment", "OUT_OF_DOMAIN"), ("foreign_asset", "UNSUPPORTED_ASSET"), ("other_product", "UNSUPPORTED_ASSET"),
])
def test_reject_kind_to_code(kind, code):
    d = decide(basic(others=[other("어떤 표현", kind=kind)]))
    assert d.verdict == "reject" and d.rejection.code == code and d.rejection.evidence == "어떤 표현"


@pytest.mark.parametrize("evidence,code", [
    ("공매도해줘", "UNSUPPORTED_ACTION"), ("내일 오를", "FUTURE_REF"), ("5분봉", "INTRADAY"), ("비트코인", "UNSUPPORTED_ASSET"),
])
def test_reject_phrase_table_beats_llm_kind(evidence, code):
    """표현 표의 거절 구절이 LLM 분류보다 우선."""
    d = decide(basic(others=[other(evidence, kind="other")]))
    assert d.rejection.code == code


def test_reject_in_condition_future():
    s = basic(entry=cond(cmp("내일 오를 것 같으면", op("close"), ">", rt(), status="unsupported", kind="future_info")))
    assert decide(s).rejection.code == "FUTURE_REF"


def test_unknown_indicator_even_if_llm_says_filled():
    s = basic(entry=cond(cmp("PER 10 아래면", op("PER"), "<", val(10))))
    d = decide(s)
    assert d.verdict == "reject" and d.rejection.code == "UNKNOWN_INDICATOR"


def test_unknown_indicator_when_llm_swapped_to_known_id():
    """LLM 이 '볼린저 하단'을 사전 id(close)로 적었어도 '없는 지표'로 분류했으면 거절."""
    s = basic(entry=cond(cmp("볼린저 하단 아래로 내려가면", op("close"), "cross_below", rt("ma", 20),
                             status="unsupported", kind="unknown_indicator")))
    assert decide(s).rejection.code == "UNKNOWN_INDICATOR"


def test_foreign_asset_rejected():
    s = basic(assets=assets(asset("애플", status="unsupported", kind="foreign_asset")))
    d = decide(s)
    assert d.rejection.code == "UNSUPPORTED_ASSET" and d.rejection.evidence == "애플"


def test_out_of_domain_when_nothing():
    d = decide(fill(), text="오늘 저녁 뭐 먹지")
    assert d.rejection.code == "OUT_OF_DOMAIN"


def test_unplaceable_other_asks_to_drop():
    q = only_q(decide(basic(others=[other("조용히", kind="other")])))
    assert q.options == ["빼고 진행", "문장을 고쳐서 다시 입력"]
    d = decide(basic(others=[other("조용히", kind="other")]), [{q.id: "빼고 진행"}])
    assert d.verdict == "proceed"


# ---------------------------------------------------------------- 되묻기: 한 번에 3개, 최대 2번

def _four_questions():
    return fill(assets=assets(),
                entry=cond(cmp("이평선 돌파하면", op("close"), "cross_above", rt("ma"), status="ambiguous")),
                exit=cond(cmp("RSI 낮을 때", op("rsi"), "<", rt(), status="ambiguous")),
                max_weight=slot(None, status="ambiguous", evidence="적당히"))


def test_at_most_three_questions_per_round():
    d = decide(_four_questions())
    assert d.verdict == "clarify" and d.round == 1
    assert len(d.questions) == MAX_QUESTIONS and len(d.pending) == 4
    assert ids(d) == ["assets:none", "max_weight:value", "entry[0]:ma_vague"]
    d2 = decide(_four_questions(), [{"assets:none": "KODEX 200", "max_weight:value": "40", "entry[0]:ma_vague": "20일"}])
    assert d2.verdict == "clarify" and d2.round == 2 and ids(d2) == ["exit[0]:rsi_low_vague"]
    d3 = decide(_four_questions(), [{"assets:none": "KODEX 200", "max_weight:value": "40", "entry[0]:ma_vague": "20일"},
                                    {"exit[0]:rsi_low_vague": "30"}])
    assert d3.verdict == "proceed"
    r = d3.resolved
    assert r.assets[0]["ticker"] == "069500" and r.max_weight == 0.4
    assert r.entry.items[0].right == {"indicator": "ma", "window": 20, "of": "self"}
    assert r.exit.items[0].right == {"value": 30}


def test_unresolved_after_two_rounds():
    s = fill(assets=assets(), entry=cond(RSI30_CROSS), exit=cond(MA5_BREAK))
    d1 = decide(s)
    assert d1.verdict == "clarify" and d1.round == 1 and ids(d1) == ["assets:none"]
    d2 = decide(s, [{"assets:none": "아무거나"}])            # 규칙으로 읽을 수 없는 답 → 빈칸이 남음
    assert d2.verdict == "clarify" and d2.round == 2
    d3 = decide(s, [{"assets:none": "아무거나"}, {"assets:none": "글쎄"}])
    assert d3.verdict == "reject" and d3.rejection.code == "UNRESOLVED"
    assert "어떤 종목으로 할까요?" in d3.rejection.detail


def test_reject_found_after_answer_still_rejects():
    s = fill(assets=assets(), entry=cond(RSI30_CROSS), exit=cond(MA5_BREAK))
    d = decide(s, [{"assets:none": "애플"}])     # 목록에서 못 찾으면 적용 안 됨 → 다시 질문
    assert d.verdict == "clarify" and d.round == 2


def test_unknown_answer_ids_ignored():
    d = decide(basic(), [{"nope": "x"}])
    assert d.verdict == "proceed"


# ---------------------------------------------------------------- 기본값 표 각 행

def test_row_asset_with_rules_first_option_kodex200():
    s = fill(assets=assets(), entry=cond(RSI30_CROSS), exit=cond(MA5_BREAK))
    q = only_q(decide(s))
    assert q.text == "어떤 종목으로 할까요?" and q.options == ["KODEX 200", "직접 입력"]
    d = decide(s, [{q.id: "KODEX 200"}])
    assert d.verdict == "proceed" and d.resolved.assets[0]["ticker"] == "069500"


def test_row_asset_without_rules_no_default_option():
    s = fill(assets=assets(), rebalance=slot("quarterly", evidence="분기마다"))
    q = only_q(decide(s))
    assert q.options == ["직접 입력"]
    d = decide(s, [{q.id: "KODEX 200, KODEX 단기채권"}])
    assert [a["ticker"] for a in d.resolved.assets] == ["069500", "153130"]


def test_row_asset_category_demo_asks_name():
    s = basic(assets=assets(asset("반도체주", status="unsupported", kind="category")))
    q = only_q(decide(s))
    assert "데모에서는 종목 이름으로만 받아요. 어떤 종목으로 할까요?" in q.text
    assert q.options == ["직접 입력"] and "삼성전자" in q.hint
    d = decide(s, [{q.id: "삼성전자, SK하이닉스"}])
    assert d.verdict == "proceed" and [a["ticker"] for a in d.resolved.assets] == ["005930", "000660"]


@pytest.mark.parametrize("status,kind", [("unsupported", "not_in_list"), ("filled", None), ("unsupported", None)])
def test_korean_stock_not_in_list_asks_other(status, kind):
    """목록에 없는 한국 종목: 거절하지 않고 되묻기 (demo-scope). LLM 이 filled 라고 해도 규칙이 잡는다."""
    s = basic(assets=assets(asset("LG에너지솔루션", status=status, kind=kind)))
    q = only_q(decide(s))
    assert q.text == "'LG에너지솔루션'은 데모 종목 목록에 없어요. 다른 종목으로 할까요?"
    assert decide(s, [{q.id: "카카오"}]).resolved.assets[0]["ticker"] == "035720"


def test_alias_lookup_exact_only():
    s = basic(assets=assets(asset("하이닉스")))
    assert decide(s).resolved.assets[0]["ticker"] == "000660"
    s = basic(assets=assets(asset("삼성")))       # '삼성'은 별칭이 아님 → 비슷한 종목으로 바꾸지 않음
    assert decide(s).verdict == "clarify"


def test_row_weight_default_equal():
    d = decide(basic(assets=assets(asset("현대차"), asset("기아"))))
    assert [a["weight"] for a in d.resolved.assets] == [None, None]
    assert "assets.weight" in d.resolved.defaults


def test_weight_given():
    d = decide(basic(assets=assets(asset("현대차", weight=50), asset("기아", weight=50))))
    assert [a["weight"] for a in d.resolved.assets] == [0.5, 0.5] and "assets.weight" not in d.resolved.defaults


def test_weight_sum_over_100_asks():
    s = basic(assets=assets(asset("현대차", weight=70), asset("기아", weight=50)))
    q = only_q(decide(s))
    assert q.text == "비중 합이 120%예요. 어떻게 나눌까요?" and q.options == ["똑같이 나누기", "직접 입력"]
    assert [a["weight"] for a in decide(s, [{q.id: "똑같이 나누기"}]).resolved.assets] == [None, None]
    assert [a["weight"] for a in decide(s, [{q.id: "60, 40"}]).resolved.assets] == [0.6, 0.4]


def test_weight_partial_asks():
    s = basic(assets=assets(asset("현대차", weight=60), asset("기아"), asset("카카오")))
    q = only_q(decide(s))
    d = decide(s, [{q.id: "남은 비중을 똑같이 나누기"}])
    assert [a["weight"] for a in d.resolved.assets] == [0.6, 0.2, 0.2]


def test_max_weight_conflict_asks():
    s = basic(assets=assets(asset("현대차", weight=50), asset("기아", weight=50)),
              max_weight=slot(40, evidence="40% 넘게는 싫어"))
    d = decide(s)
    assert ids(d) == ["assets[0]:over_max", "assets[1]:over_max"]
    assert d.questions[0].options == ["50% 담기", "40%에 맞추기"]


def test_row_rebalance_allocation_default():
    d = decide(fill(assets=assets(asset("KODEX 200", weight=70), asset("KODEX 단기채권", weight=30))))
    assert d.verdict == "proceed" and d.resolved.rebalance == "monthly_first" and "rebalance" in d.resolved.defaults
    assert d.resolved.entry is None and d.resolved.exit is None


def test_row_rebalance_with_rules_none():
    d = decide(basic())
    assert d.resolved.rebalance is None and "rebalance" not in d.resolved.defaults


def test_rebalance_unknown_period_asks_nearest():
    s = fill(assets=assets(SAMSUNG), rebalance=slot("1년에 한 번", status="unsupported", evidence="1년에 한 번"))
    q = only_q(decide(s))
    assert q.options == ["분기마다(1·4·7·10월 첫 거래일)", "매달 첫 거래일"]
    assert decide(s, [{q.id: q.options[0]}]).resolved.rebalance == "quarterly"


def test_row_entry_only_asks_exit():
    s = basic(exit=cond())
    q = only_q(decide(s))
    assert q.id == "exit:none" and q.text == "언제 팔까요?"
    assert q.options == ["조건이 반대가 되면 팔기", "몇 % 오르면 팔기", "계속 갖고 있기"]


def test_exit_answer_negate():
    s = basic(exit=cond())
    d = decide(s, [{"exit:none": "조건이 반대가 되면 팔기"}])
    assert d.verdict == "proceed"
    e = d.resolved.exit
    assert e.source == "negate" and e.items[0].op == "cross_above" and e.items[0].right == {"value": 30}


def test_exit_answer_take_profit_then_number():
    s = basic(exit=cond())
    d = decide(s, [{"exit:none": "몇 % 오르면 팔기"}])
    q = only_q(d)
    assert q.text == "산 가격보다 몇 % 오르면 팔까요?" and d.round == 2
    d = decide(s, [{"exit:none": "몇 % 오르면 팔기"}, {q.id: "10"}])
    assert d.verdict == "proceed"
    c = d.resolved.exit.items[0]
    assert c.left["indicator"] == "entry_return" and c.op == ">=" and c.right == {"value": 10}


def test_exit_answer_hold():
    d = decide(basic(exit=cond()), [{"exit:none": "계속 갖고 있기"}])
    assert d.verdict == "proceed" and d.resolved.exit is None and d.resolved.hold


def test_vague_exit_asks_when_to_sell():
    s = basic(exit={"status": "ambiguous", "value": {"logic": "all", "items": []}, "evidence": "적당한 때 팔아",
                    "reason": ""})
    assert only_q(decide(s)).id == "exit:none"


def test_row_exit_only_asks_entry():
    s = basic(entry=cond())
    q = only_q(decide(s))
    # '처음부터 갖고 시작'은 데모에서 뺐다 (docs/demo-scope.md 팀 안건)
    assert q.id == "entry:none" and q.options == ["조건이 반대가 되면 다시 사기"]
    d = decide(s, [{q.id: "조건이 반대가 되면 다시 사기"}])
    assert d.verdict == "proceed" and d.resolved.entry.items[0].op == "cross_above"
    assert d.resolved.entry.source == "negate" and d.resolved.entry.items[0].source == "negate"
    d = decide(s, [{q.id: "처음부터 갖고 시작"}])        # 선택지에 없는 답 → 적용하지 않고 다시 묻는다
    assert d.verdict == "clarify" and ids(d) == ["entry:none"]


def test_exit_only_stop_loss_cannot_negate():
    s = basic(entry=cond(), exit=cond(cmp("10% 손실이면", op("entry_return"), "<=", val(-10))))
    assert only_q(decide(s)).options == ["문장을 고쳐서 다시 입력"]


def test_row_rsi_window_default_14():
    s = basic(entry=cond(cmp("RSI 30 아래면", op("rsi"), "<", val(30))))
    c = decide(s).resolved.entry.items[0]
    assert c.left == {"indicator": "rsi", "window": DEFAULTS["rsi_window"], "of": "self"}
    assert c.defaults == ["left.window"]


def test_row_ma_window_asks():
    s = basic(entry=cond(cmp("이평선 돌파하면", op("close"), "cross_above", rt("ma"), status="ambiguous")))
    q = only_q(decide(s))
    assert q.text == "몇 일 이동평균선으로 할까요?" and q.options == ["5일", "20일", "60일"]
    c = decide(s, [{q.id: "60일"}]).resolved.entry.items[0]
    assert (c.left["indicator"], c.op, c.right) == ("close", "cross_above", {"indicator": "ma", "window": 60, "of": "self"})


def test_ma_vague_when_llm_left_items_empty():
    """실제 qwen3:8b 출력: '이평선 돌파하면'을 칸 상태 ambiguous 로만 적고 비교는 비움."""
    vague = lambda ev: {"status": "ambiguous", "value": {"logic": "all", "items": []}, "evidence": ev,  # noqa: E731
                        "reason": ""}
    s = basic(entry=vague("이평선 돌파하면"), exit=vague("이평선 깨지면"))
    d = decide(s)
    assert ids(d) == ["entry[0]:ma_vague", "exit[0]:ma_vague"] and d.questions[0].options == ["5일", "20일", "60일"]
    d = decide(s, [{"entry[0]:ma_vague": "20일", "exit[0]:ma_vague": "5일"}])
    e, x = d.resolved.entry.items[0], d.resolved.exit.items[0]
    assert (e.left["indicator"], e.op, e.right["window"]) == ("close", "cross_above", 20)
    assert (x.left["indicator"], x.op, x.right["window"]) == ("close", "cross_below", 5)


def test_ma_vague_even_if_llm_filled_window():
    """'이평선 돌파'에 LLM 이 20일을 지어 넣고 filled 라고 해도 숫자가 원문에 없으면 되묻는다."""
    s = basic(entry=cond(cmp("이평선 돌파하면", op("close"), "cross_above", rt("ma", 20))))
    assert only_q(decide(s)).id == "entry[0]:ma_vague"


def test_row_threshold_asks():
    s = basic(entry=cond(cmp("RSI 낮을 때", op("rsi"), "<", rt(), status="ambiguous")))
    q = only_q(decide(s))
    assert q.text == "RSI가 얼마 아래일 때를 낮다고 볼까요?" and q.options == ["30", "20"]
    c = decide(s, [{q.id: "20"}]).resolved.entry.items[0]
    assert c.left["indicator"] == "rsi" and c.op == "<" and c.right == {"value": 20}


def test_rsi_14_filled_by_llm_marked_default():
    c = decide(basic()).resolved.entry.items[0]          # 근거 'RSI 30 아래로 떨어지면' 에 14 가 없음
    assert c.left["window"] == 14 and c.defaults == ["left.window"]
    s = basic(entry=cond(cmp("RSI 14 30 아래면", op("rsi", 14), "<", val(30))))
    assert decide(s).resolved.entry.items[0].defaults == []


@pytest.mark.parametrize("word,o,v", [("과매도면", "<", 30), ("과매수면", ">", 70)])
def test_row_oversold_overbought_default(word, o, v):
    s = basic(entry=cond(cmp(word, op("rsi"), None, rt())))
    c = decide(s).resolved.entry.items[0]
    assert c.left == {"indicator": "rsi", "window": 14, "of": "self"} and c.op == o and c.right == {"value": v}
    assert c.defaults == [""]


@pytest.mark.parametrize("word,o", [("골든크로스 나오면", "cross_above"), ("데드크로스 나오면", "cross_below")])
def test_row_cross_asks(word, o):
    s = basic(entry=cond(cmp(word, op("ma"), o, rt("ma"), status="ambiguous")))
    q = only_q(decide(s))
    assert q.options == ["5일·20일선", "20일·60일선"]
    c = decide(s, [{q.id: "20일·60일선"}]).resolved.entry.items[0]
    assert (c.left["window"], c.op, c.right["window"]) == (20, o, 60)


def test_row_of_default_self_and_kospi():
    s = basic(entry=cond(cmp("코스피가 20일선 위면", op("close", None, "코스피"), ">", rt("ma", 20, "코스피")),
                         cmp("RSI 30 아래면", op("rsi", 14, None), "<", val(30))))
    items = decide(s).resolved.entry.items
    assert items[0].left["of"] == "KOSPI200" and items[0].right["of"] == "KOSPI200"
    assert items[1].left["of"] == "self"


def test_of_other_ticker_and_unknown():
    s = basic(entry=cond(cmp("하이닉스가 20일선 위면", op("close", None, "하이닉스"), ">", rt("ma", 20, "하이닉스"))))
    assert decide(s).resolved.entry.items[0].left["of"] == "000660"
    s = basic(entry=cond(cmp("에코프로가 20일선 위면", op("close", None, "에코프로"), ">", rt("ma", 20, "에코프로"))))
    assert "목록에 없어요" in only_q(decide(s)).text


@pytest.mark.parametrize("evidence,o", [("5일선 깨지면", "cross_below"), ("RSI 30 이하면", "<="),
                                        ("20일선 올라서면", "cross_above"), ("RSI 70 위면", ">")])
def test_row_direction_table_when_op_missing(evidence, o):
    left = op("close") if "선" in evidence else op("rsi", 14)
    right = rt("ma", int(evidence.split("일")[0])) if "선" in evidence else val(30)
    s = basic(exit=cond(cmp(evidence, left, None, right)))
    assert decide(s).resolved.exit.items[0].op == o


def test_row_backtest_defaults():
    r = decide(basic()).resolved
    assert (r.start, r.end, r.initial_cash) == ("2023-01-01", "2025-12-31", 10_000_000)
    assert (r.commission_rate, r.slippage_rate) == (0.00015, 0.0005)
    assert {"backtest.period", "backtest.initial_cash", "backtest.costs"} <= set(r.defaults)


def test_backtest_given():
    r = decide(basic(period=slot({"start": "2022-01-01", "end": None}, evidence="2022년부터"),
                     initial_cash=slot(50_000_000, evidence="5천만 원"))).resolved
    assert r.start == "2022-01-01" and r.end == "2025-12-31" and r.initial_cash == 50_000_000
    assert "backtest.end" in r.defaults and "backtest.start" not in r.defaults
    assert "backtest.period" not in r.defaults and "backtest.initial_cash" not in r.defaults


def test_row_dividend():
    assert decide(basic(), text="삼성전자 RSI 30 아래로 떨어지면 사고 5일선 깨지면 팔아").resolved.dividend_mentioned is False
    assert decide(basic(), text="고배당 삼성전자 ...").resolved.dividend_mentioned is True
    s = basic(dividend=slot(False, evidence="배당은 빼줘"))
    assert decide(s, text="배당은 빼줘").resolved.dividend_mentioned is False


# ---------------------------------------------------------------- 없는 기간 → 가까운 기간을 선택지로

@pytest.mark.parametrize("status,kind", [("unsupported", "bad_window"), ("filled", None)])
def test_7day_ma_asks_nearest(status, kind):
    s = basic(exit=cond(cmp("7일선 깨지면", op("close"), "cross_below", rt("ma", 7), status=status, kind=kind)))
    d = decide(s)
    q = only_q(d)
    assert q.text == "7일선은 없어요. 5일선과 10일선 중 어느 쪽인가요?"   # v0.2 예시 그대로
    assert q.options == ["5일선", "10일선"]
    c = decide(s, [{q.id: "10일선"}]).resolved.exit.items[0]
    assert c.right == {"indicator": "ma", "window": 10, "of": "self"}


def test_9day_rsi_asks_14():
    s = basic(entry=cond(cmp("9일 RSI가 20 아래면", op("rsi", 9), "<", val(20), status="unsupported", kind="bad_window")))
    q = only_q(decide(s))
    assert q.text == "9일 RSI는 없어요. 14일 RSI로 할까요?" and q.options == ["14일 RSI"]
    c = decide(s, [{q.id: "14일 RSI"}]).resolved.entry.items[0]
    assert c.left["window"] == 14 and c.right == {"value": 20}


def test_200day_ma_only_upper_neighbor_missing():
    s = basic(exit=cond(cmp("200일선 깨지면", op("close"), "cross_below", rt("ma", 200))))
    assert only_q(decide(s)).options == ["120일선"]


# ---------------------------------------------------------------- LLM 이 애매하다고 했는데 규칙으로는 다 맞음

def test_ambiguous_but_complete_confirms():
    s = basic(entry=cond(cmp("RSI 30 근처로 떨어지면", op("rsi", 14), "cross_below", val(30), status="ambiguous")))
    q = only_q(decide(s))
    assert q.options == ["네", "문장을 고쳐서 다시 입력"] and "RSI(14일) 30 아래로 내려가는 순간" in q.text
    assert decide(s, [{q.id: "네"}]).verdict == "proceed"


def test_filled_condition_with_no_items_is_not_silently_dropped():
    """실제 qwen3:8b 출력: entry 를 status filled 로 적고 비교 목록을 빠뜨림 → 빈 문장(OUT_OF_DOMAIN)이 아니라 되묻기."""
    empty = lambda ev: {"status": "filled", "value": {"logic": "all", "items": []}, "evidence": ev,  # noqa: E731
                        "reason": ""}
    d = decide(fill(entry=empty("RSI 30 아래로 떨어지면"), exit=empty("5일선 깨지면")), text="x")
    assert d.verdict == "clarify" and "assets:none" in ids(d)
