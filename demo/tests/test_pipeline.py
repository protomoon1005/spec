"""조립 (core.assemble) · 파이프라인 (core.pipeline). LLM 대신 정해진 빈칸 JSON 을 돌려주는 가짜 chat 을 쓴다."""
import json
from pathlib import Path

from core.assemble import assemble
from core.decide import decide
from core.pipeline import run
from core.schema import Strategy
from slots import MA5_BREAK, RSI30_CROSS, asset, assets, basic, cmp, cond, op, val

ROOT = Path(__file__).resolve().parents[1]
BASIC = "삼성전자 RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘"     # basic() 빈칸의 원문


def fake_chat(*outputs):
    calls = []

    def chat(messages, schema):
        calls.append(messages)
        out = outputs[min(len(calls) - 1, len(outputs) - 1)]
        return {"message": {"content": out if isinstance(out, str) else out.model_dump_json()},
                "prompt_eval_count": 100, "eval_count": 10}
    chat.calls = calls
    return chat


# ---------------------------------------------------------------- 조립

def test_assemble_matches_v02_example():
    spec = assemble(decide(basic()).resolved)
    example = json.loads((ROOT / "examples" / "rsi30_ma5_samsung.json").read_text(encoding="utf-8"))
    for k in ("schema_version", "assets", "max_weight", "rebalance", "rules", "backtest"):
        assert spec[k] == example[k], k
    assert spec["defaults_applied"] == ["backtest.period", "backtest.initial_cash", "backtest.costs",
                                        "rules.entry.left.window"]   # RSI 14 는 원문에 없음
    Strategy.model_validate(spec)


def test_assemble_default_paths_for_multiple_conditions():
    s = basic(entry=cond(cmp("RSI 30 아래면", op("rsi"), "<", val(30)), cmp("과매도", op("rsi"), None, val(None))),
              exit=cond(MA5_BREAK, cmp("10% 손실이면", op("entry_return"), "<=", val(-10)), logic="any"))
    spec = assemble(decide(s).resolved)
    assert spec["rules"]["entry"]["all"][0]["left"] == {"indicator": "rsi", "of": "self", "window": 14}
    assert "rules.entry.all[0].left.window" in spec["defaults_applied"]
    assert "rules.entry.all[1]" in spec["defaults_applied"]
    assert spec["rules"]["exit"]["any"][1] == {"left": {"indicator": "entry_return", "of": "self"}, "op": "<=",
                                               "right": {"value": -10}}
    Strategy.model_validate(spec)


def test_assemble_allocation_only():
    from slots import fill
    spec = assemble(decide(fill(assets=assets(asset("KODEX 200", weight=70), asset("KODEX 단기채권", weight=30)))).resolved)
    assert spec["rules"] is None and spec["rebalance"] == "monthly_first"
    assert [a["weight"] for a in spec["assets"]] == [0.7, 0.3]
    assert "rebalance" in spec["defaults_applied"]


# ---------------------------------------------------------------- 파이프라인

def test_run_proceed(tmp_path):
    chat = fake_chat(basic())
    r = run("삼성전자 RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘", chat_fn=chat, cache_dir=tmp_path)
    assert r.verdict == "proceed" and r.rejection is None and r.questions == []
    assert r.report.verdict == "ok" and r.strategy.assets[0].ticker == "005930"
    text = r.narration.text
    assert "(기본값 사용)" in text and "배당 수익은 반영하지 않았습니다" in text and "생존 편향" in text


def test_run_uses_cache(tmp_path):
    chat = fake_chat(basic())
    run(BASIC, chat_fn=chat, cache_dir=tmp_path)
    r = run(BASIC, chat_fn=chat, cache_dir=tmp_path)
    assert len(chat.calls) == 1 and r.fill.cached and r.verdict == "proceed"


def test_run_retries_once_on_bad_format(tmp_path):
    chat = fake_chat("{not json", basic())
    r = run(BASIC, chat_fn=chat, cache_dir=tmp_path)
    assert len(chat.calls) == 2 and r.fill.attempts == 2 and r.verdict == "proceed"


def test_run_error_when_format_fails_twice(tmp_path):
    r = run("문장", chat_fn=fake_chat("{", "{"), cache_dir=tmp_path)
    assert r.verdict == "error" and r.error


def test_run_clarify_then_answer(tmp_path):
    s = basic(assets=assets())
    chat = fake_chat(s)
    r = run("RSI 30 아래로 떨어지면 사고 5일선 깨지면 팔아", chat_fn=chat, cache_dir=tmp_path)
    assert r.verdict == "clarify" and r.round == 1
    q = r.questions[0]
    r2 = run("RSI 30 아래로 떨어지면 사고 5일선 깨지면 팔아", [{q.id: q.options[0]}], chat_fn=chat, cache_dir=tmp_path)
    assert r2.verdict == "proceed" and r2.strategy.assets[0].name == "KODEX 200"
    assert len(chat.calls) == 1        # 답은 규칙이 적용한다 (LLM 을 다시 부르지 않음)


def test_run_validation_block(tmp_path):
    """산 가격 대비 수익률을 살 때 조건에 씀 → 3단계 막힘 → 거절 VALIDATION."""
    s = basic(entry=cond(cmp("산 가격보다 10% 오르면 사", op("entry_return"), ">=", val(10))))
    r = run("x", chat_fn=fake_chat(s), cache_dir=tmp_path)
    assert r.verdict == "reject" and r.rejection.code == "VALIDATION"
    assert "3단계" in r.rejection.detail


def test_run_validation_clarify_counts_rounds(tmp_path):
    """사는 조건이면 파는 조건도 늘 참 → 3단계 되묻기. 2번 물어도 남으면 UNRESOLVED."""
    s = basic(entry=cond(cmp("RSI 30 밑이면", op("rsi", 14), "<", val(30))),
              exit=cond(cmp("RSI 40 밑이면", op("rsi", 14), "<", val(40))))
    chat = fake_chat(s)
    text = "삼성전자 RSI 30 밑이면 사고 RSI 40 밑이면 팔아"
    r = run(text, chat_fn=chat, cache_dir=tmp_path)
    assert r.verdict == "clarify" and [q.id for q in r.questions] == ["validate:ALWAYS_BOTH:rules.exit"]
    assert r.questions[0].options == ["문장을 고쳐서 다시 입력"]
    r = run(text, [{}, {}], chat_fn=chat, cache_dir=tmp_path)
    assert r.verdict == "reject" and r.rejection.code == "UNRESOLVED"


def test_run_hold_answer_drops_no_exit(tmp_path):
    chat = fake_chat(basic(exit=cond()))
    r = run("삼성전자 RSI 30 아래로 떨어지면 사줘", [{"exit:none": "계속 갖고 있기"}], chat_fn=chat, cache_dir=tmp_path)
    assert r.verdict == "proceed" and r.strategy.rules.exit is None
    assert "기간 끝까지 갖고 있습니다" in r.narration.text


def test_run_reject(tmp_path):
    s = basic(entry=cond(cmp("MACD 골든크로스", op("MACD"), "cross_above", val(None), status="unsupported",
                             kind="unknown_indicator")))
    r = run("x", chat_fn=fake_chat(s), cache_dir=tmp_path)
    assert r.verdict == "reject" and r.rejection.code == "UNKNOWN_INDICATOR" and r.spec is None
    assert r.summary()["rejection"]["label"] == "없는 지표"


def test_run_dividend_note(tmp_path):
    r = run("고배당 삼성전자 RSI 30 아래로 떨어지면 사고 5일선 깨지면 팔아", chat_fn=fake_chat(basic()), cache_dir=tmp_path)
    assert "데모에서는 배당 미반영" in r.narration.text


def test_stop_loss_close_basis_note(tmp_path):
    s = basic(exit=cond(MA5_BREAK, cmp("10% 손실이면", op("entry_return"), "<=", val(-10)), logic="any"))
    r = run("삼성전자 RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔고 10% 손실이면 팔아", chat_fn=fake_chat(s),
            cache_dir=tmp_path)
    assert "손절·익절은 그날 종가로 판단합니다" in r.narration.text
    r = run(BASIC, chat_fn=fake_chat(basic(entry=cond(RSI30_CROSS))), cache_dir=tmp_path)
    assert "손절·익절" not in r.narration.text
