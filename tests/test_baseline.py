"""비교 대상(LLM 코드 직접 생성): 프롬프트 공정성, 응답 읽기, 샌드박스, 미래 참조 검사, 주문 비교·체결, 화면 상태.
가짜 LLM + 합성 가격 (네트워크 없음). 샌드박스는 실제로 자식 프로세스를 띄운다."""
import functools

import pandas as pd
import pytest

from baseline import compare as B
from baseline.codegen import build_prompt, generate, parse
from baseline.orders import execute_orders, match_counts, orders_from_fills, same_orders
from baseline.sandbox import check_code, run_code
from conftest import wavy_ohlcv
from core.dictionary import load_tickers

START, END = "2023-01-01", "2025-06-30"


def _prices(phase, base=100.0):
    df = wavy_ohlcv(1000, phase=phase, base=base)
    return df.set_axis(df.index.shift(-500, freq="B"), axis=0)


PRICES = {"005930": _prices(0.0), "KS11": _prices(1.0, 300.0)}
KNOWN = {"005930"}

CROSS = '''
import pandas as pd
def strategy(prices, start, end):
    c = prices["005930"]["Close"]
    f, s = c.rolling(5).mean(), c.rolling(20).mean()
    up = (f > s) & (f.shift(1) <= s.shift(1))
    dn = (f < s) & (f.shift(1) >= s.shift(1))
    out = []
    for d in c.index[(c.index >= start) & (c.index <= end)]:
        if up[d]:
            out.append({"date": d.strftime("%Y-%m-%d"), "ticker": "005930", "side": "buy", "weight": 1.0})
        elif dn[d]:
            out.append({"date": d.strftime("%Y-%m-%d"), "ticker": "005930", "side": "sell", "weight": 0.0})
    return out
'''
LEAKY = CROSS.replace("c.rolling(20).mean()", "c.shift(-1).rolling(20).mean()")


def fake_chat(*contents):
    calls = []

    def chat(messages, fmt):
        calls.append(messages)
        return {"message": {"content": contents[min(len(calls), len(contents)) - 1]}, "done_reason": "stop"}
    chat.calls = calls
    return chat


def block(code):
    return f"```python\n{code}\n```"


# ---------------------------------------------------------------- 프롬프트 · 응답

def test_prompt_has_data_and_output_format_but_not_our_dictionary():
    p = build_prompt()
    for must in ("Open", "High", "Low", "Close", "Volume", '"date"', '"ticker"', '"side"', '"weight"', "다음 거래일 시가",
                 "pandas", "numpy", "005930", "KS11", "VIX", "NEED_CLARIFICATION", "CANNOT_IMPLEMENT"):
        assert must in p
    # 우리 사전·규칙·판정이 들어가지 않는다: 지표 이름, 연산자, 표현 표, 종목 별칭, 판정·거절 코드
    for banned in ("RSI", "이동평균", "골든크로스", "cross_above", "cross_below", "holding_days", "entry_return",
                   "UNKNOWN_INDICATOR", "되묻기", "거절", "팔기 우선", "max_weight", "1/N"):
        assert banned not in p, banned
    # 종목은 코드와 정식 이름만. 별칭(삼전, 하닉, 포스코 …)은 정식 이름을 지운 나머지에 없어야 한다
    rest = p
    for t in load_tickers().values():
        rest = rest.replace(t.name, "")
    aliases = {a for t in load_tickers().values() for a in t.aliases if a != t.name}
    assert not [a for a in aliases if a in rest]


def test_parse_response_kinds():
    assert parse(block(CROSS))[0] == "code"
    assert parse("설명\n" + block(CROSS) + "\nNEED_CLARIFICATION: x")[0] == "code"     # 코드를 냈으면 코드
    assert parse("NEED_CLARIFICATION: 어떤 종목인가요?") == ("clarify", None, "어떤 종목인가요?")
    assert parse("`CANNOT_IMPLEMENT: 분봉 데이터가 없음`") == ("reject", None, "분봉 데이터가 없음`".strip("`"))
    assert parse("def strategy(prices, start, end):\n    return []")[0] == "code"
    assert parse("잘 모르겠습니다")[0] == "error"
    # 출력 상한에 걸려 코드 블록이 안 닫힌 경우: 여는 ``` 를 코드에 넣지 않는다 (2026-11-02 '1행 문법 오류'로 잘못 보임)
    kind, code, _ = parse("```python\nimport pandas as pd\ndef strategy(p, s, e):\n    x = p['A'].rolling(window=75")
    assert kind == "code" and code.startswith("import pandas") and "```" not in code


def test_generate_uses_same_model_settings_and_cache(tmp_path):
    from core.slot_fill import MODEL, OPTIONS
    chat = fake_chat(block(CROSS))
    a = generate("삼성전자 사줘", use_cache=True, chat_fn=chat, cache_dir=tmp_path)
    b = generate("삼성전자 사줘", use_cache=True, chat_fn=chat, cache_dir=tmp_path)
    assert a.kind == "code" and not a.cached and b.cached and len(chat.calls) == 1
    assert chat.calls[0][0]["role"] == "system" and chat.calls[0][1] == {"role": "user", "content": "삼성전자 사줘"}
    assert MODEL == "qwen3:8b" and OPTIONS["temperature"] == 0 and OPTIONS["seed"] == 42


# ---------------------------------------------------------------- 샌드박스

@pytest.mark.parametrize("code,why", [
    ("import os\ndef strategy(p, s, e): return []", "import os"),
    ("from subprocess import run\ndef strategy(p, s, e): return []", "subprocess"),
    ("def strategy(p, s, e):\n    open('x.txt', 'w')\n    return []", "open"),
    ("def strategy(p, s, e):\n    return __import__('os')", "__import__"),
    ("def strategy(p, s, e):\n    return ().__class__.__bases__", "__class__"),
    ("def strategy(p, s, e):\n    return getattr(p, 'x')", "getattr"),
])
def test_static_check_blocks(code, why):
    probs = check_code(code)
    assert probs and any(why in x for x in probs)


def test_static_check_allows_pandas_numpy():
    assert check_code("from __future__ import annotations\nimport pandas as pd\nimport numpy as np\n"
                      "from numpy import where\n" + CROSS) == []


def test_sandbox_runs_good_code():
    r = run_code(CROSS, PRICES, START, END, known=KNOWN)
    assert r.ok, r.error
    assert r.orders and {o["side"] for o in r.orders} == {"buy", "sell"}
    assert all(START <= o["date"] <= END for o in r.orders)
    assert r.orders == run_code(CROSS, PRICES, START, END, known=KNOWN).orders     # 결정론적


@pytest.mark.parametrize("code,stage,needle", [
    ("import pandas as pd\ndef strategy(p, s, e):\n    pd.read_csv('secret.csv')\n    return []", "run", "PermissionError"),
    ("import pandas as pd\ndef strategy(p, s, e):\n    pd.DataFrame({'a': [1]}).to_csv('out.csv')\n    return []",
     "run", "PermissionError"),
    ("def strategy(p, s, e):\n    return {'a': 1}", "run", "list"),
    ("def strategy(p, s, e):\n    return [{'date': '2024-01-02', 'ticker': '005930', 'side': 'hold', 'weight': 1}]",
     "run", "side"),
    ("def strategy(p, s, e):\n    return [{'date': '2024-01-02', 'ticker': '000000', 'side': 'buy', 'weight': 1}]",
     "output", "000000"),
    ("def other(p, s, e):\n    return []", "run", "strategy"),
    ("def strategy(p, s, e):\n    return 1 / 0", "run", "ZeroDivisionError"),
])
def test_sandbox_failures_are_reported(code, stage, needle):
    r = run_code(code, PRICES, START, END, known=KNOWN)
    assert not r.ok and r.stage == stage and needle in r.error, r.error


def test_sandbox_timeout():
    r = run_code("def strategy(p, s, e):\n    while True:\n        pass", PRICES, START, END, timeout=3)
    assert not r.ok and r.stage == "timeout"


def test_sandbox_cannot_touch_files(tmp_path):
    target = tmp_path / "x.txt"
    code = f"import numpy as np\ndef strategy(p, s, e):\n    np.save({str(target)!r}, np.zeros(1))\n    return []"
    r = run_code(code, PRICES, START, END)
    assert not r.ok and not target.exists() and not (tmp_path / "x.txt.npy").exists()


# ---------------------------------------------------------------- 미래 참조

def test_lookahead_check_same_method_as_engine():
    cal = B.window_calendar(PRICES, START, END)
    good = run_code(CROSS, PRICES, START, END, known=KNOWN)
    res = B.check_lookahead(CROSS, PRICES, START, END, good.orders, cal, KNOWN)
    assert not res["leak"] and [c["status"] for c in res["cuts"]] == ["same"] * 4
    bad = run_code(LEAKY, PRICES, START, END, known=KNOWN)
    res = B.check_lookahead(LEAKY, PRICES, START, END, bad.orders, cal, KNOWN)
    assert res["leak"] and any(c["status"] == "changed" for c in res["cuts"])


def test_trial_repeats_and_flags_inconsistency():
    chat = fake_chat(block(CROSS), block(CROSS), block(LEAKY))
    t = B.trial("골든크로스", PRICES, START, END, repeats=3, chat_fn=chat)
    assert [r["outcome"] for r in t["runs"]] == ["code_ok"] * 3
    assert not t["consistent"] and not t["codes_same"]
    assert len(t["lookahead"]) == 2 and sorted(v["leak"] for v in t["lookahead"].values()) == [False, True]
    same = B.trial("골든크로스", PRICES, START, END, repeats=2, chat_fn=fake_chat(block(CROSS)), lookahead=False)
    assert same["consistent"] and same["codes_same"]


def test_trial_clarify_does_not_run_code():
    t = B.trial("바닥에서 사", PRICES, START, END, repeats=1, chat_fn=fake_chat("NEED_CLARIFICATION: 바닥이 뭔가요?"))
    assert t["runs"][0]["outcome"] == "clarify" and t["runs"][0]["run"] is None and t["lookahead"] == {}


# ---------------------------------------------------------------- 주문

def _o(d, side, w, t="005930"):
    return {"date": d, "ticker": t, "side": side, "weight": w}


def test_same_orders_tolerance_half_pp():
    a = [_o("2024-01-02", "buy", 0.5)]
    assert same_orders(a, [_o("2024-01-02", "buy", 0.504)])
    assert not same_orders(a, [_o("2024-01-02", "buy", 0.506)])
    assert not same_orders(a, [_o("2024-01-03", "buy", 0.5)])
    assert not same_orders(a, [_o("2024-01-02", "sell", 0.5)])
    assert match_counts(a + [_o("2024-02-01", "sell", 0)], a) == {"matched": 1, "pred": 2, "gold": 1}


def test_orders_from_fills_weights_and_initial_day():
    cal = pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-04"])
    fills = [{"date": "2024-01-02", "signal_date": None, "ticker": "A", "side": "buy", "reason": "initial"},
             {"date": "2024-01-04", "signal_date": "2024-01-03", "ticker": "A", "side": "sell", "reason": "exit"}]
    assert orders_from_fills(fills, {"A": 0.5}, cal) == [_o("2024-01-02", "buy", 0.5, "A"),
                                                         _o("2024-01-03", "sell", 0.0, "A")]


def test_execute_orders_next_open_with_costs():
    df = PRICES["005930"]
    cal = df.index[(df.index >= START) & (df.index <= END)]
    d0, d1, d5, d6 = (cal[i].strftime("%Y-%m-%d") for i in (0, 1, 5, 6))
    ex = execute_orders([_o(d0, "buy", 1.0), _o(d5, "sell", 0.0)], PRICES, cal, initial_cash=10_000_000,
                        commission_rate=0.00015, slippage_rate=0.0005, instruments={"005930": "KOSPI_STOCK"})
    buy, sell = ex["fills"]
    assert (buy["date"], buy["side"], sell["date"], sell["side"]) == (d1, "buy", d6, "sell")
    assert buy["price"] == pytest.approx(df.loc[d1, "Open"] * 1.0005)
    assert ex["equity"].iloc[0] == 10_000_000                     # 첫날은 아직 현금
    assert ex["equity"].iloc[-1] == ex["equity"].iloc[7]          # 판 뒤에는 현금 그대로
    gross = buy["shares"] * df.loc[d6, "Open"] * 0.9995
    assert ex["equity"].iloc[-1] < 10_000_000 + gross - buy["shares"] * buy["price"]   # 수수료·세금이 빠졌다


def test_execute_orders_out_of_window():
    df = PRICES["005930"]
    cal = df.index[(df.index >= START) & (df.index <= END)]
    early, last = df.index[df.index < START][-5].strftime("%Y-%m-%d"), cal[-1].strftime("%Y-%m-%d")
    ex = execute_orders([_o(early, "buy", 1.0), _o(last, "buy", 1.0)], PRICES, cal, initial_cash=1e7,
                        commission_rate=0, slippage_rate=0, instruments={"005930": "KOSPI_STOCK"})
    assert len(ex["ignored"]) == 1 and len(ex["unfilled"]) == 1 and ex["fills"] == []


# ---------------------------------------------------------------- 화면 상태

def test_ui_baseline_compare_side_by_side(tmp_path):
    from slots import basic
    from test_pipeline import BASIC
    from test_ui_state import PRICES as UI_PRICES, make_run
    from ui import state as S
    rf = make_run(tmp_path, basic())
    conv = S.new_conversation()
    S.submit(conv, BASIC, rf)
    S.backtest(conv, 1, rf)
    ridx = len(conv["messages"]) - 1
    chat = fake_chat(block(CROSS), block(CROSS), block(LEAKY))
    steps = []
    S.baseline_compare(conv, ridx, rf, steps.append, trial_fn=functools.partial(B.trial, chat_fn=chat),
                       data_fn=lambda end: (UI_PRICES, []))
    m = conv["messages"][-1]
    assert m["kind"] == "baseline" and conv["messages"][ridx]["baseline_done"]
    assert steps[:3] == ["가격 데이터 준비", "LLM 코드 생성·실행 (1/3)", "LLM 코드 생성·실행 (2/3)"]
    assert m["outcome"] == "code_ok" and m["code"].startswith("import pandas") and m["base_orders"]
    assert m["ours_orders"] and all(set(o) == {"date", "ticker", "side", "weight"} for o in m["ours_orders"])
    assert len(m["chart"]["dates"]) == len(m["chart"]["ours"]) == len(m["chart"]["base"])
    assert not m["lookahead"]["leak"] and m["lookahead_other"]          # 3회차 코드는 미래 참조
    texts = dict(S.baseline_badges(m))
    assert texts["3회 실행 결과가 다름"] == "red" and texts["다른 회차 코드에서 미래 참조 잡힘"] == "red"
    n = len(conv["messages"])
    S.baseline_compare(conv, ridx, rf, trial_fn=functools.partial(B.trial, chat_fn=chat))   # 한 번만
    assert len(conv["messages"]) == n


def test_ui_baseline_badges_failure_and_leak():
    base = {"runs": [{}] * 3, "consistent": True, "codes_same": True, "lookahead": None}
    assert ("실행 실패 — KeyError: 1", "red") in S_badges(dict(base, outcome="code_fail", error="KeyError: 1"))
    leak = {"leak": True, "cuts": [{"status": "changed"}]}
    assert ("미래 참조 잡힘", "red") in S_badges(dict(base, outcome="code_ok", lookahead=leak))
    ok = {"leak": False, "cuts": [{"status": "same"}] * 4}
    orders = [{"date": "2024-01-02", "ticker": "005930", "side": "buy", "weight": 1.0}]
    assert S_badges(dict(base, outcome="code_ok", lookahead=ok, base_orders=orders)) == [
        ("미래 참조 없음 (가격 섭동 4곳)", "green"), ("3회 실행 결과 일치", "green")]
    assert ("실행은 됐지만 주문을 하나도 내지 않음", "orange") in S_badges(dict(base, outcome="code_ok", base_orders=[]))


def S_badges(m):
    from ui import state as S
    return S.baseline_badges(m)
