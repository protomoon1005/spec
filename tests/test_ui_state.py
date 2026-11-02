"""화면 상태 처리 (ui.state). 가짜 LLM + 합성 가격으로 core.pipeline.run 을 그대로 쓴다 (네트워크 없음)."""
import json

import pytest

from conftest import wavy_ohlcv
from core.decide import DIRECT, RESTATE
from core.pipeline import STEPS, run
from slots import MA5_BREAK, basic, cmp, cond, op, val
from test_pipeline import BASIC, fake_chat
from ui import state as S

def _prices(phase):
    df = wavy_ohlcv(1000, phase=phase)
    return df.set_axis(df.index.shift(-500, freq="B"), axis=0)      # 2022-02 ~ 2025-11 (시작 전 이력 포함)


PRICES = {"005930": _prices(0.0), "KS11": _prices(1.0)}


def make_run(tmp_path, *slots):
    chat = fake_chat(*slots)

    def run_fn(text, answers, on_step=None):
        return run(text, answers, chat_fn=chat, cache_dir=tmp_path / "cache", prices=PRICES, on_step=on_step)
    run_fn.chat = chat
    return run_fn


def test_examples_cover_kinds():
    kinds = [e["kind"] for e in S.EXAMPLES]
    assert kinds.count("진행") == 2 and kinds.count("되묻기") == 1 and kinds.count("거절") == 1
    assert BASIC in [e["text"] for e in S.EXAMPLES]


def test_submit_proceed_then_backtest_is_reproducible(tmp_path):
    rf = make_run(tmp_path, basic())
    conv = S.new_conversation()
    steps = []
    S.submit(conv, BASIC, rf, steps.append)
    assert steps == list(STEPS)
    assert conv["title"] == S.title_of(BASIC)
    user, card = conv["messages"]
    assert user == {"role": "user", "text": BASIC}
    assert card["kind"] == "understood" and card["used"] is None and card["trial_hash"]
    assert any(p["default"] for p in card["parts"]) and "backtest.period" in card["defaults"]
    assert [s["stage"] for s in card["checklist"]] == [1, 2, 3, 4, 5]
    assert card["spec"]["assets"][0]["ticker"] == "005930"

    S.backtest(conv, 1, rf)
    res = conv["messages"][-1]
    assert card["used"] == "backtest" and res["kind"] == "result"
    assert res["match"] and res["hash"] == card["trial_hash"]
    c = res["chart"]
    assert len(c["dates"]) == len(c["strategy"]) == len(c["kospi"]) == len(c["buy_hold"])
    assert "buys" not in c                        # 사기·팔기 표시는 매매 지점 차트로 옮겼다
    assert res["card_idx"] == 1 and res["collapsed"] is False
    assert set(res["metrics"]) >= {"total_return", "cagr", "mdd", "n_trades"}
    assert any("배당" in n for n in res["notes"]) and any("생존 편향" in n for n in res["notes"])
    assert "수수료" in res["costs"]
    S.backtest(conv, 1, rf)                       # 이미 쓴 카드는 다시 돌리지 않는다
    assert len(conv["messages"]) == 3
    json.dumps(conv)                              # 그대로 저장할 수 있어야 한다


def test_backtest_mismatch_is_reported(tmp_path):
    rf = make_run(tmp_path, basic())
    conv = S.new_conversation()
    S.submit(conv, BASIC, rf)
    conv["messages"][1]["trial_hash"] = "deadbeef"
    S.backtest(conv, 1, rf)
    assert conv["messages"][-1]["match"] is False


def test_clarify_answer_flow(tmp_path):
    rf = make_run(tmp_path, basic(exit=cond()))
    conv = S.new_conversation()
    S.submit(conv, "삼성전자 RSI 30 아래로 떨어지면 사줘", rf)
    q = conv["messages"][-1]
    assert q["kind"] == "clarify" and q["round"] == 1 and q["max_rounds"] == 2 and not q["done"]
    assert conv["pending"] == {"text": "삼성전자 RSI 30 아래로 떨어지면 사줘", "answers": []}
    qd = next(x for x in q["questions"] if x["id"] == "exit:none")
    assert "계속 갖고 있기" in qd["options"]
    reply, err = S.collect_answers(q["questions"], {"exit:none": "계속 갖고 있기"}, {})
    assert err is None
    S.answer(conv, reply, rf)
    assert q["done"] and conv["pending"] is None
    assert conv["messages"][-2] == {"role": "user", "text": f"{qd['text']} → 계속 갖고 있기"}
    card = conv["messages"][-1]
    assert card["kind"] == "understood" and card["answers"] == [reply]
    assert len(rf.chat.calls) == 1               # 답은 규칙이 적용 (LLM 다시 안 부름)


def test_collect_answers_direct_input():
    qs = [{"id": "a", "text": "어떤 종목?", "options": ["KODEX 200", DIRECT]},
          {"id": "b", "text": "언제?", "options": ["x", DIRECT]}]
    assert S.collect_answers(qs, {"a": DIRECT, "b": "x"}, {"a": "  삼성전자 "}) == ({"a": "삼성전자", "b": "x"}, None)
    assert S.collect_answers(qs, {"a": DIRECT, "b": "x"}, {"a": " "})[1]
    assert S.collect_answers(qs, {"a": "KODEX 200"}, {})[1]


def test_clarify_view_adds_direct_only_when_answerable(tmp_path):
    s = basic(entry=cond(cmp("RSI 30 밑이면", op("rsi", 14), "<", val(30))),
              exit=cond(cmp("RSI 40 밑이면", op("rsi", 14), "<", val(40))))
    rf = make_run(tmp_path, s)
    conv = S.new_conversation()
    S.submit(conv, "삼성전자 RSI 30 밑이면 사고 RSI 40 밑이면 팔아", rf)
    q = conv["messages"][-1]
    assert q["kind"] == "clarify" and q["questions"][0]["options"] == [RESTATE]
    S.answer(conv, {q["questions"][0]["id"]: RESTATE}, rf)
    assert conv["pending"] is None and conv["messages"][-1]["kind"] == "note"
    assert len(rf.chat.calls) == 1


def test_new_text_closes_open_clarify(tmp_path):
    rf = make_run(tmp_path, basic(exit=cond()), basic())
    conv = S.new_conversation()
    S.submit(conv, "삼성전자 RSI 30 아래로 떨어지면 사줘", rf)
    q = conv["messages"][-1]
    S.submit(conv, BASIC, rf)
    assert q["done"] and conv["messages"][-1]["kind"] == "understood"
    assert conv["title"] == S.title_of("삼성전자 RSI 30 아래로 떨어지면 사줘")   # 제목은 첫 문장


def test_reject_view_highlights_evidence(tmp_path):
    s = basic(entry=cond(cmp("MACD 골든크로스", op("MACD"), "cross_above", val(None), status="unsupported",
                             kind="unknown_indicator")), exit=cond(MA5_BREAK))
    rf = make_run(tmp_path, s)
    conv = S.new_conversation()
    S.submit(conv, "삼성전자 MACD 골든크로스면 사고 5일선 깨지면 팔아", rf)
    r = conv["messages"][-1]
    assert r["kind"] == "reject" and r["code"] == "UNKNOWN_INDICATOR" and r["label"] == "없는 지표" and r["plain"]
    assert ["MACD 골든크로스", True] in r["segments"]
    assert "".join(t for t, _ in r["segments"]) == "삼성전자 MACD 골든크로스면 사고 5일선 깨지면 팔아"


def test_error_verdict(tmp_path):
    rf = make_run(tmp_path, "{", "{")
    conv = S.new_conversation()
    S.submit(conv, "아무 말", rf)
    assert conv["messages"][-1]["kind"] == "error"


def test_revise_and_blank_input(tmp_path):
    rf = make_run(tmp_path, basic())
    conv = S.new_conversation()
    S.submit(conv, "   ", rf)
    assert conv["messages"] == [] and conv["title"] == "새 전략"
    S.submit(conv, BASIC, rf)
    S.revise(conv, 1)
    assert conv["messages"][1]["used"] == "revise" and conv["messages"][-1]["kind"] == "note"
    S.backtest(conv, 1, rf)
    assert conv["messages"][-1]["kind"] == "note"


@pytest.mark.parametrize("text,ev,expected", [
    ("내일 오를 종목 사줘", "내일 오를", [["내일 오를", True], [" 종목 사줘", False]]),
    ("내일 오를 종목 사줘", "", [["내일 오를 종목 사줘", False]]),
    ("내일 오를 종목 사줘", "모레", [["내일 오를 종목 사줘", False]]),
    ("5분봉 RSI 보고", "5분봉RSI", [["5분봉 RSI", True], [" 보고", False]]),
])
def test_highlight(text, ev, expected):
    assert S.highlight(text, ev) == expected


def test_history_save_load_list(tmp_path):
    from datetime import datetime
    a = S.new_conversation(datetime(2026, 11, 1, 9))
    b = S.new_conversation(datetime(2026, 11, 2, 9))
    assert S.save(a, tmp_path) is None             # 빈 대화는 저장하지 않음
    for c, t in ((a, "첫 전략"), (b, "둘째 전략")):
        c["title"] = t
        c["messages"].append({"role": "user", "text": t})
        S.save(c, tmp_path)
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    assert [x["title"] for x in S.list_history(tmp_path)] == ["둘째 전략", "첫 전략"]
    assert S.load(a["id"], tmp_path) == a and S.load("none", tmp_path) is None


def test_fmt_pct():
    assert S.fmt_pct(0.1234) == "+12.3%" and S.fmt_pct(-0.05, signed=False) == "-5.0%" and S.fmt_pct(None) == "—"


def test_fmt_won_and_ret():
    assert S.fmt_won(74937.42) == "74,937원" and S.fmt_won(None) == "—"
    assert S.fmt_ret(0.04751) == "+4.75%" and S.fmt_ret(-0.0306) == "-3.06%" and S.fmt_ret(None) == "—"


# ---------------------------------------------------------------- 전략 카드

def _spec(**kw) -> "Strategy":
    from core.schema import Strategy
    base = {"assets": [{"ticker": "005930", "name": "삼성전자", "weight": None}],
            "rules": {"target": "each",
                      "entry": {"left": {"indicator": "rsi", "window": 14, "of": "self"}, "op": "cross_below",
                                "right": {"value": 30}},
                      "exit": {"left": {"indicator": "close", "of": "self"}, "op": "cross_below",
                               "right": {"indicator": "ma", "window": 5, "of": "self"}}}}
    base.update(kw)
    return Strategy.model_validate(base)


def test_card_core_lines_and_defaults(tmp_path):
    rf = make_run(tmp_path, basic())
    conv = S.new_conversation()
    S.submit(conv, BASIC, rf)
    card = conv["messages"][1]["card"]
    lines = {ln["key"]: ln for ln in card["lines"]}
    assert list(lines) == ["대상", "살 때", "팔 때"]
    assert lines["대상"]["text"] == "삼성전자 (전액)"
    assert lines["살 때"]["text"] == "RSI(14일)가 30 아래로 내려가는 날 → 다음 날 시가에 매수"
    assert lines["팔 때"]["text"] == "종가가 5일 이동평균 아래로 내려가는 날 → 다음 날 시가에 전량 매도"
    assert lines["살 때"]["defaults"] == ["RSI 기간 14일"] and lines["팔 때"]["defaults"] == []
    assert card["other_defaults"] == ["기간 2023-01-01 ~ 2025-12-31", "시작 금액 1,000만 원",
                                      "수수료 0.015% · 체결 오차 0.05%"]
    labels = [c["label"] for c in card["common"]]
    assert {"체결·평가", "기간·비용", "팔기 우선", "세금", "배당", "생존 편향"} <= set(labels)
    tax = next(c for c in card["common"] if c["label"] == "세금")["tax"]
    assert tax["headline"] == "거래한 날짜의 세율 적용 (코스피 주식 0.15~0.20%)"
    assert ["2023", "0.20%"] in tax["table"][0]["rows"]
    # 재서술 문장은 바꾸지 않고 자리만 옮긴다 (세금 문장은 요약 + 표로 대체)
    narr = [p["text"] for p in conv["messages"][1]["parts"] if not p["text"].startswith("세금은")]
    assert narr == [c["text"] for c in card["common"] if c["label"] != "세금"]


def test_card_of_rebuilds_old_saved_card(tmp_path):
    rf = make_run(tmp_path, basic())
    conv = S.new_conversation()
    S.submit(conv, BASIC, rf)
    m = dict(conv["messages"][1])
    card = m.pop("card")
    assert S.card_of(m) == card


def test_target_text_variants():
    two = [{"ticker": "005930", "name": "삼성전자", "weight": None}, {"ticker": "068270", "name": "셀트리온", "weight": None}]
    assert S.core_lines(_spec(assets=two))[0]["text"] == "삼성전자, 셀트리온 (종목마다 1/2씩)"
    w = [dict(two[0], weight=0.6), dict(two[1], weight=0.4)]
    assert S.core_lines(_spec(assets=w))[0]["text"] == "삼성전자 60% · 셀트리온 40%"
    alloc = S.core_lines(_spec(assets=w, rules=None, rebalance="monthly_first"))
    assert [ln["key"] for ln in alloc] == ["대상", "처음", "맞추기"]
    assert alloc[2]["text"].startswith("매달 첫 거래일 종가 기준 → 다음 날 시가에")
    hold = S.core_lines(_spec(rules={"target": "each", "entry": _spec().rules.entry.model_dump(), "exit": None}))
    assert hold[2]["text"] == "팔 조건 없음 → 기간 끝까지 보유"


def test_checklist_summary():
    items = [{"status": s} for s in ("passed", "passed", "passed", "passed", "warned")]
    assert S.checklist_summary(items) == ("검사 5단계 · 통과 4 · 경고 1", False)
    items[1]["status"] = "questions"
    assert S.checklist_summary(items) == ("검사 5단계 · 통과 3 · 경고 1 · 되묻기 1", True)


# ---------------------------------------------------------------- 결과 카드

def test_trade_chart_uses_backtest_indicator_values(tmp_path):
    from core.backtest import run_backtest
    rf = make_run(tmp_path, basic())
    conv = S.new_conversation()
    S.submit(conv, BASIC, rf)
    S.backtest(conv, 1, rf)
    res = conv["messages"][-1]
    (tc,) = res["trade_chart"]
    n = len(res["chart"]["dates"])
    assert tc["ticker"] == "005930" and tc["name"] == "삼성전자" and len(tc["close"]) == n
    assert [ln["label"] for ln in tc["price_lines"]] == ["5일 이동평균"]       # 종가는 따로 그린다
    assert len(tc["panels"]) == 1 and tc["panels"][0]["refs"] == [30.0]
    assert [ln["label"] for ln in tc["panels"][0]["lines"]] == ["RSI(14일)"]
    # 백테스트가 조건 계산에 쓴 값과 같다 (화면이 다시 계산하지 않음)
    trial = run_backtest(_spec(), data=PRICES)
    ind = {i["indicator"]: i["values"] for i in trial.series["005930"]["indicators"]}
    assert tc["panels"][0]["lines"][0]["values"] == [S._num(v) for v in ind["rsi"]]
    assert tc["price_lines"][0]["values"] == [S._num(v) for v in ind["ma"]]
    assert tc["buys"] and all(b["date"] in res["chart"]["dates"] for b in tc["buys"] + tc["sells"])
    json.dumps(res, allow_nan=False)                # NaN(지표 워밍업)은 null 로


def test_series_field_does_not_change_result_hash():
    from core.backtest import run_backtest
    a, b = run_backtest(_spec(), data=PRICES), run_backtest(_spec(), data=PRICES)
    assert a.result_hash == b.result_hash and set(a.series) == {"005930"}
    assert len(a.series["005930"]["close"]) == len(a.equity)


def test_trade_rows_new_and_old_format():
    new = {"ticker": "005930", "entry_date": "2023-01-25", "entry_price": 74937.4, "exit_date": None,
           "exit_price": None, "holding_days": 7, "return_pct": 0.0475}
    old = {"종목": "005930", "산 날": "2023-01-25", "산 값": 74937.4, "판 날": "보유 중", "판 값": None,
           "보유일": 7, "수익률(%)": 4.75}
    a, b = S.trade_rows([new, old])
    assert a == b and a["name"] == "삼성전자" and a["open"] is True and a["return_pct"] == 0.0475


# ---------------------------------------------------------------- 뒤로가기 · 다시하기

def test_result_navigation(tmp_path):
    rf = make_run(tmp_path, basic())
    conv = S.new_conversation()
    S.submit(conv, BASIC, rf)
    S.backtest(conv, 1, rf)
    assert S.retry_text(conv, 2) == BASIC and S.retry_text(conv, 1) == BASIC
    assert S.collapse_result(conv, 2) == 1 and conv["messages"][2]["collapsed"] is True
    S.expand_result(conv, 2)
    assert conv["messages"][2]["collapsed"] is False
    del conv["messages"][2]["card_idx"]             # 이전 판 결과: 앞쪽의 백테스트한 카드
    assert S.source_card(conv, 2) == 1


def _two_rounds(tmp_path):
    rf = make_run(tmp_path, basic(exit=cond()))
    conv = S.new_conversation()
    text = "삼성전자 RSI 30 아래로 떨어지면 사줘"
    S.submit(conv, text, rf)
    assert S.previous_question(conv) is None        # 1번째 되묻기에는 이전 질문이 없다
    S.answer(conv, {"exit:none": "몇 % 오르면 팔기"}, rf)
    q2 = conv["messages"][-1]
    assert q2["kind"] == "clarify" and q2["round"] == 2
    return rf, conv, text


def test_back_to_previous_question(tmp_path):
    rf, conv, text = _two_rounds(tmp_path)
    assert S.previous_question(conv) == 1
    S.back_to_previous_question(conv)
    q1 = conv["messages"][-1]
    assert len(conv["messages"]) == 2 and q1["round"] == 1 and not q1["done"] and q1["rev"] == 1
    assert q1["reply"] == {"exit:none": "몇 % 오르면 팔기"}     # 전에 고른 답은 남긴다
    assert conv["pending"] == {"text": text, "answers": []}
    assert S.previous_question(conv) is None
    S.answer(conv, {"exit:none": "계속 갖고 있기"}, rf)        # 다른 답으로 다시
    card = conv["messages"][-1]
    assert card["kind"] == "understood" and card["answers"] == [{"exit:none": "계속 갖고 있기"}]


def test_history_restores_whole_conversation(tmp_path):
    rf, conv, _ = _two_rounds(tmp_path)
    S.save(conv, tmp_path)
    back = S.load(conv["id"], tmp_path)
    assert back == conv
    assert back["messages"][1]["reply"] == {"exit:none": "몇 % 오르면 팔기"} and back["pending"]["answers"]
    S.back_to_previous_question(back)                   # 복원한 대화에서도 이어서 할 수 있다
    assert back["messages"][-1]["round"] == 1
