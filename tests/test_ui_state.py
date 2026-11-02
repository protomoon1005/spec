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
    assert c["buys"] and all(b["date"] in c["dates"] for b in c["buys"] + c["sells"])
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
