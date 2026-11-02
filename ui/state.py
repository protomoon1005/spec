"""채팅 화면의 상태 처리. Streamlit 을 쓰지 않는다 (pytest 로 테스트).

화면(app.py)은 여기서 만든 대화(dict)를 그리기만 한다. 판정·검사·백테스트는 전부 core.pipeline.run 이 한다.
이 모듈은 run 결과를 화면에 그릴 수 있는 JSON 값(메시지)으로 옮겨 적고, 대화를 data/history 에 저장한다.

대화 (dict, JSON 으로 저장)
  id, title, created, messages: [메시지], pending: 되묻기 중이면 {"text", "answers"} 아니면 None

메시지
  {"role": "user", "text"}
  {"role": "assistant", "kind": "clarify",    "round", "max_rounds", "questions", "done",
                                              "reply", "rev"}            # reply: 고른 답, rev: 다시 열린 횟수
  {"role": "assistant", "kind": "reject",     "code", "label", "plain", "detail", "segments"}
  {"role": "assistant", "kind": "understood", "text", "answers", "parts", "card", "defaults", "checklist", "spec",
                                              "trial_hash", "used"}      # used: None | backtest | revise
  {"role": "assistant", "kind": "result",     "card_idx", "metrics", "chart", "trade_chart", "warnings", "trades",
                                              "hash", "first_hash", "match", "costs", "notes", "collapsed"}
  {"role": "assistant", "kind": "note" | "error", "text"}

이전 판에서 저장한 대화(card·trade_chart·reply 가 없음)도 그대로 열린다: card 는 전략서에서 다시 만들고,
매매 지점 차트는 없다고 표시하고, 옛 거래 내역 형식은 trade_rows 가 새 형식으로 바꾼다.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from core.backtest import slot_weights
from core.decide import DIRECT, MAX_ROUNDS, RESTATE
from core.dictionary import INDICATORS, OF_LABELS, REBALANCE, TAX_CATEGORY_LABELS, indicator_label, of_kind, \
    tax_category, ticker_info
from core.narrate import DIVIDEND_NOTE_DEMO, _cond, _day, _pct, _won, narrate
from core.pipeline import PipelineResult, StepFn
from core.schema import Compare, Operand, Strategy, Value, iter_compares
from core.tax import rate_history

ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "data" / "history"

# run_fn(text, answers, on_step) → PipelineResult. 화면은 default_run, 테스트는 가짜 LLM·합성 가격을 쓴 run.
RunFn = Callable[[str, list[dict], Optional[StepFn]], PipelineResult]

EXAMPLES = [
    {"kind": "진행", "text": "삼성전자 RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘"},
    {"kind": "진행", "text": "셀트리온 RSI 25 이하일 때 사고 RSI 60 이상이면 팔아"},
    {"kind": "되묻기", "text": "삼성전자 RSI 30 아래면 사줘"},
    {"kind": "거절", "text": "내일 오를 종목 사줘"},
]

STATUS_LABELS = {"passed": "통과", "failed": "막힘", "questions": "되묻기", "warned": "경고", "skipped": "건너뜀"}

# 거절 사유 코드별 쉬운 설명 (core.dictionary.REJECT_CODES 의 짧은 이름 옆에 보인다)
PLAIN = {
    "FUTURE_REF": "그날까지 알 수 있는 정보로만 판단할 수 있어요. 앞으로 오를지·내릴지는 미리 알 수 없어서 규칙으로 만들 수 없습니다.",
    "UNKNOWN_INDICATOR": "데모 사전에 없는 지표나 숫자로 정할 수 없는 말이 들어 있어요. RSI·이동평균·볼린저 같은 사전 속 지표로 말해 주세요.",
    "UNSUPPORTED_ASSET": "데모에서 다루는 국내 종목·ETF 목록 밖의 상품이에요.",
    "UNSUPPORTED_ACTION": "사기·팔기만 다룹니다. 공매도·신용·레버리지 같은 매매 방법은 다루지 않아요.",
    "INTRADAY": "하루에 한 번, 그날 종가로 판단하고 다음 날 시가에 사고팝니다. 장중 매매는 다루지 않아요.",
    "NO_DATA": "이 기간의 가격 데이터가 없어 시험할 수 없어요.",
    "OUT_OF_DOMAIN": "투자 전략으로 읽을 수 있는 문장이 아니에요.",
    "UNRESOLVED": "두 번 여쭤봤지만 정하지 못한 부분이 남았어요. 문장을 고쳐서 다시 말해 주세요.",
    "VALIDATION": "전략서를 만들었지만 검사에서 말이 안 되는 부분이 나왔어요.",
}


def default_run(text: str, answers: list[dict], on_step: Optional[StepFn] = None) -> PipelineResult:
    from core.pipeline import load_prices, run
    return run(text, answers, prices=load_prices, on_step=on_step)


# ---------------------------------------------------------------- 대화

def new_conversation(now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    return {"id": now.strftime("%Y%m%d-%H%M%S-%f"), "title": "새 전략", "created": now.isoformat(timespec="seconds"),
            "messages": [], "pending": None}


def title_of(text: str, limit: int = 28) -> str:
    t = " ".join(text.split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _say(conv: dict, kind: str, **kw) -> None:
    conv["messages"].append({"role": "assistant", "kind": kind, **kw})


def submit(conv: dict, text: str, run_fn: RunFn, on_step: Optional[StepFn] = None) -> dict:
    """새 문장. 되묻기 중이었어도 새 문장으로 처음부터 한다."""
    text = text.strip()
    if not text:
        return conv
    if not any(m["role"] == "user" for m in conv["messages"]):
        conv["title"] = title_of(text)
    _close_clarify(conv)
    conv["messages"].append({"role": "user", "text": text})
    return _handle(conv, text, [], run_fn(text, [], on_step))


def collect_answers(questions: list[dict], picks: dict[str, Optional[str]],
                    typed: dict[str, str]) -> tuple[Optional[dict], Optional[str]]:
    """화면에서 고른 선택지(picks)와 직접 입력(typed) → ({질문 id: 답}, 오류 문구)."""
    out = {}
    for q in questions:
        pick = picks.get(q["id"])
        if pick is None:
            return None, f"'{q['text']}'에 답을 골라 주세요."
        if pick == DIRECT:
            val = (typed.get(q["id"]) or "").strip()
            if not val:
                return None, f"'{q['text']}'에 답을 입력해 주세요."
            pick = val
        out[q["id"]] = pick
    return out, None


def answer(conv: dict, reply: dict[str, str], run_fn: RunFn, on_step: Optional[StepFn] = None) -> dict:
    """마지막 되묻기에 답한다. '문장을 고쳐서 다시 입력'을 골랐으면 되묻기를 끝내고 새 문장을 기다린다."""
    pending = conv.get("pending")
    q = _open_clarify(conv)
    if pending is None or q is None:
        return conv
    q["done"] = True
    q["reply"] = dict(reply)                     # 지난 대화를 열었을 때 고른 답을 그대로 보이기 위해
    texts = {x["id"]: x["text"] for x in q["questions"]}
    conv["messages"].append({"role": "user", "text": " · ".join(f"{texts.get(k, k)} → {v}" for k, v in reply.items())})
    if RESTATE in reply.values():
        conv["pending"] = None
        _say(conv, "note", text="문장을 고쳐서 아래 입력창에 다시 말해 주세요.")
        return conv
    answers = pending["answers"] + [reply]
    return _handle(conv, pending["text"], answers, run_fn(pending["text"], answers, on_step))


def backtest(conv: dict, idx: int, run_fn: RunFn, on_step: Optional[StepFn] = None) -> dict:
    """'이대로 백테스트'. 같은 문장·같은 답으로 run 을 다시 돌려 검사 5단계(시험 실행) 결과를 보여 주고,
    처음 실행의 result_hash 와 비교해 재현성을 표시한다."""
    card = conv["messages"][idx]
    if card.get("kind") != "understood" or card.get("used"):
        return conv
    card["used"] = "backtest"
    res = run_fn(card["text"], card["answers"], on_step)
    trial = res.report.trial if res.report else None
    if res.verdict != "proceed" or trial is None:
        why = res.error or (res.rejection.detail if res.rejection else "") or _trial_error(res)
        _say(conv, "error", text=f"백테스트를 하지 못했습니다. {why}".strip())
        return conv
    conv["messages"].append({"role": "assistant", "kind": "result", "card_idx": idx,
                             **result_view(res, card.get("trial_hash"))})
    return conv


def revise(conv: dict, idx: int) -> dict:
    card = conv["messages"][idx]
    if card.get("kind") != "understood" or card.get("used"):
        return conv
    card["used"] = "revise"
    _say(conv, "note", text="고칠 부분을 넣어 문장을 아래 입력창에 다시 말해 주세요.")
    return conv


def _open_clarify(conv: dict) -> Optional[dict]:
    for m in reversed(conv["messages"]):
        if m.get("kind") == "clarify":
            return None if m.get("done") else m
    return None


def _close_clarify(conv: dict) -> None:
    if (q := _open_clarify(conv)) is not None:
        q["done"] = True
    conv["pending"] = None


def _trial_error(res: PipelineResult) -> str:
    if not res.report:
        return ""
    return " / ".join(i.message for i in res.report.issues if i.code == "TRIAL_ERROR")


def _handle(conv: dict, text: str, answers: list[dict], res: PipelineResult) -> dict:
    conv["pending"] = None
    if res.verdict == "error":
        _say(conv, "error", text=f"문장을 빈칸으로 나누지 못했습니다. {res.error or ''}".strip())
    elif res.verdict == "reject":
        _say(conv, "reject", **reject_view(text, res))
    elif res.verdict == "clarify":
        conv["pending"] = {"text": text, "answers": answers}
        _say(conv, "clarify", **clarify_view(res))
    else:
        _say(conv, "understood", text=text, answers=answers, **understood_view(res))
    return conv


# ---------------------------------------------------------------- 화면용 값

def highlight(text: str, evidence: str) -> list[list]:
    """원문을 [[조각, 걸린 구절인가], ...] 로 나눈다. 근거를 원문에서 못 찾으면 강조 없이 한 조각."""
    ev = (evidence or "").strip()
    if not ev:
        return [[text, False]]
    m = re.search(re.escape(ev), text) or re.search(r"\s*".join(map(re.escape, ev.replace(" ", ""))), text)
    if not m:
        return [[text, False]]
    parts = [[text[: m.start()], False], [m.group(0), True], [text[m.end():], False]]
    return [p for p in parts if p[0]]


def reject_view(text: str, res: PipelineResult) -> dict:
    r = res.rejection
    return {"code": r.code, "label": r.label, "plain": PLAIN.get(r.code, ""), "detail": r.detail,
            "evidence": r.evidence, "segments": highlight(text, r.evidence)}


def clarify_view(res: PipelineResult) -> dict:
    qs = []
    for q in res.questions:
        opts = list(q.options)
        if RESTATE not in opts and DIRECT not in opts:
            opts.append(DIRECT)   # 문장을 고쳐야만 풀리는 질문에는 직접 입력을 주지 않는다 (답해도 풀리지 않음)
        qs.append({"id": q.id, "text": q.text, "options": opts, "evidence": q.evidence, "hint": q.hint})
    return {"round": res.round, "max_rounds": MAX_ROUNDS, "questions": qs, "done": False}


def checklist(report) -> list[dict]:
    return [{"stage": s.stage, "name": s.name, "status": s.status, "label": STATUS_LABELS[s.status],
             "issues": [i.message for i in s.issues], "note": s.note} for s in report.stages]


def understood_view(res: PipelineResult) -> dict:
    trial = res.report.trial
    return {"parts": [{"text": p.text, "default": p.is_default} for p in res.narration.parts],
            "card": build_card(res.report.spec, res.narration),
            "notes": list(res.narration.notes),
            "defaults": list(res.spec.get("defaults_applied", [])),
            "checklist": checklist(res.report),
            "spec": res.report.spec.model_dump(mode="json"),
            "trial_hash": trial.result_hash if trial else None,
            "used": None}


# ---------------------------------------------------------------- 전략 카드 배치
# 재서술(core.narrate)의 문장은 그대로 쓰고 자리만 나눈다:
#   핵심 줄(대상 · 살 때 · 팔 때 [· 맞추기]) — 사용자가 말한 내용. 짧은 틀에 narrate 의 조건 구절을 끼운다.
#   공통 규칙 — narrate 문장 전체 (기본 닫힘). 세금만 한 줄 요약 + 연도별 표로 바꿔 보인다.
#   카드 아래 한 줄 — 핵심 줄에 속하지 않는 기본값 (한국어 이름).

def _plain(s: str) -> str:
    return s.replace("**", "")


def _is_under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[")


_WINDOW_NAMES = {"rsi": "RSI", "ma": "이동평균", "ret": "수익률", "vol": "변동성", "volume_ratio": "거래량 비율"}


def _compare_at(spec: Strategy, path: str) -> Optional[Compare]:
    if spec.rules is None:
        return None
    for base, cond in (("rules.entry", spec.rules.entry), ("rules.exit", spec.rules.exit)):
        for p, c in iter_compares(cond, base):
            if p == path:
                return c
    return None


def default_label(path: str, spec: Strategy) -> str:
    """defaults_applied 의 경로 → 화면에 보일 한국어 이름과 값."""
    b = spec.backtest
    if path in ("backtest.period", "backtest.start", "backtest.end"):
        return f"기간 {b.start} ~ {b.end}"
    if path == "backtest.initial_cash":
        return f"시작 금액 {_won(b.initial_cash)}"
    if path in ("backtest.costs", "backtest.commission_rate", "backtest.slippage_rate"):
        return f"수수료 {_pct(b.commission_rate)} · 체결 오차 {_pct(b.slippage_rate)}"
    if _is_under(path, "assets"):
        return "종목마다 같은 비중"
    if path == "rebalance":
        return f"비중 맞추기 {REBALANCE[spec.rebalance]}" if spec.rebalance else "비중 맞추기 없음"
    m = re.fullmatch(r"(rules\.\w+(?:\.(?:all|any)\[\d+\])?)\.(left|right)\.window", path)
    if m and (c := _compare_at(spec, m.group(1))) is not None:
        op = getattr(c, m.group(2))
        if isinstance(op, Operand):
            return f"{_WINDOW_NAMES.get(op.indicator, indicator_label(op.indicator, op.window))} 기간 {op.window}일"
    if (c := _compare_at(spec, path)) is not None:
        return f"기준 '{_plain(_cond(c))}'"
    return path


def _target_text(spec: Strategy) -> str:
    ws = slot_weights(spec)
    n = len(spec.assets)
    names = [a.name for a in spec.assets]
    if all(a.weight is None for a in spec.assets) and spec.rules is not None:
        if n == 1 and ws[0] >= 1 - 1e-9:
            text = f"{names[0]} (전액)"
        else:
            size = f"1/{n}" if abs(ws[0] - 1 / n) < 1e-9 else _pct(ws[0])
            text = f"{', '.join(names)} (종목마다 {size}씩)"
    else:
        text = " · ".join(f"{name} {_pct(w)}" for name, w in zip(names, ws))
    if spec.max_weight is not None:
        text += f" · 종목당 최대 {_pct(spec.max_weight)}"
    return text


def core_lines(spec: Strategy) -> list[dict]:
    """[{"key": 대상|살 때|팔 때|처음|맞추기, "text", "field"}]. field 는 기본값 태그를 붙일 전략서 경로."""
    lines = [{"key": "대상", "text": _target_text(spec), "field": "assets"}]
    r = spec.rules
    if r is None:
        lines.append({"key": "처음", "text": "기간 첫 거래일 시가에 비중대로 매수", "field": ""})
    else:
        buy = f"{_plain(_day(r.entry))} → 다음 날 시가에 매수" if r.entry is not None else "살 조건 없음"
        sell = (f"{_plain(_day(r.exit))} → 다음 날 시가에 전량 매도" if r.exit is not None
                else "팔 조건 없음 → 기간 끝까지 보유")
        lines += [{"key": "살 때", "text": buy, "field": "rules.entry"},
                  {"key": "팔 때", "text": sell, "field": "rules.exit"}]
    if spec.rebalance is not None:
        whom = "갖고 있는 종목을 자기 자리 비중으로" if r is not None else "비중을"
        lines.append({"key": "맞추기", "text": f"{REBALANCE[spec.rebalance]} 종가 기준 → 다음 날 시가에 {whom} 다시 맞춤",
                      "field": "rebalance"})
    return lines


_COMMON_LABELS = {"assets": ["자리"], "rules.entry": ["사기"], "rules.exit": ["팔기"], "rules": ["팔기 우선"],
                  "rebalance": ["비중 맞추기"], "backtest": ["체결·평가", "기간·비용"],
                  "limits.close_basis": ["손절·익절"], "limits.dividend": ["배당"], "limits.survivorship": ["생존 편향"]}


def tax_summary(spec: Strategy) -> dict:
    """{"headline": "거래한 날짜의 세율 적용 (코스피 주식 0.15~0.20%)", "table": [{"label", "rows": [[연도, 세율]]}]}"""
    cats = list(dict.fromkeys(c for a in spec.assets if (c := tax_category(a.ticker))))
    bits, table = [], []
    for c in cats:
        label = TAX_CATEGORY_LABELS[c]
        if c.endswith("_STOCK"):
            hist = rate_history(c)
            lo, hi = min(r for *_, r in hist), max(r for *_, r in hist)
            bits.append(f"{label} {lo * 100:.2f}%" if lo == hi else f"{label} {lo * 100:.2f}~{hi * 100:.2f}%")
            rows = []
            for s, e, rate in hist:
                year = f"{s.year}~" if e is None else (f"{s.year}" if e.year == s.year else f"{s.year}~{e.year}")
                rows.append([year, f"{rate * 100:.2f}%"])
            table.append({"label": f"{label} (판 금액에)", "rows": rows})
        elif c == "ETF_DOMESTIC_EQUITY":
            bits.append(f"{label} 세금 없음")
        else:
            bits.append(f"{label} 차익의 15.4%")
    if not bits:
        return {"headline": "종목 목록에 없는 종목이라 세율을 정할 수 없음", "table": []}
    return {"headline": f"거래한 날짜의 세율 적용 ({', '.join(bits)})", "table": table}


def build_card(spec: Strategy, narration) -> dict:
    """전략 카드에 보일 값. narration 은 core.narrate.narrate(spec) 결과 (문장은 바꾸지 않는다)."""
    defaults = list(spec.defaults_applied)
    lines = core_lines(spec)
    used: set[str] = set()
    for ln in lines:
        mine = [d for d in defaults if ln["field"] and _is_under(d, ln["field"])]
        used.update(mine)
        ln["defaults"] = list(dict.fromkeys(default_label(d, spec) for d in mine))
    rest = list(dict.fromkeys(default_label(d, spec) for d in defaults if d not in used))
    common, seen = [], {}
    for p in narration.parts:
        if p.field == "tax":
            common.append({"label": "세금", "text": "", "default": False, "tax": tax_summary(spec)})
            continue
        k = seen.get(p.field, 0)
        seen[p.field] = k + 1
        names = _COMMON_LABELS.get(p.field, [p.field])
        common.append({"label": names[min(k, len(names) - 1)], "text": p.text, "default": p.is_default})
    return {"lines": lines, "other_defaults": rest, "common": common}


def card_of(m: dict) -> dict:
    """understood 메시지의 카드. 이전 판에서 저장한 대화(card 없음)는 전략서로 다시 만든다."""
    if m.get("card"):
        return m["card"]
    spec = Strategy.model_validate(m["spec"])
    return build_card(spec, narrate(spec, dividend_mentioned=DIVIDEND_NOTE_DEMO in m.get("notes", [])))


def checklist_summary(items: list[dict]) -> tuple[str, bool]:
    """("검사 5단계 · 통과 4 · 경고 1", 펼쳐 둘까). 막힘·되묻기가 있을 때만 펼친다."""
    order = ["passed", "warned", "questions", "failed", "skipped"]
    counts = {s: sum(1 for x in items if x["status"] == s) for s in order}
    bits = [f"{STATUS_LABELS[s]} {counts[s]}" for s in order if counts[s]]
    return " · ".join([f"검사 {len(items)}단계", *bits]), bool(counts["failed"] or counts["questions"])


# ---------------------------------------------------------------- 결과 카드

def _num(x, nd: int = 2):
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) else round(x, nd)


def _series(s) -> Optional[list]:
    return None if s is None else [_num(v) for v in s.to_numpy()]


def operand_label(indicator: str, window: Optional[int], of: str) -> str:
    kind = of_kind(of)
    if kind == "self":
        who = ""
    elif kind == "index":
        who = (OF_LABELS[of] + " ") if OF_LABELS[of] else ""
    else:
        info = ticker_info(of)
        who = f"{info.name if info else of} "
    return who + indicator_label(indicator, window)


def _thresholds(spec: Strategy) -> dict[tuple, list[float]]:
    """(지표, 기간, of) → 그 지표와 숫자로 비교한 기준값들 (보조 차트의 기준선)."""
    out: dict[tuple, list[float]] = {}
    if spec.rules is None:
        return out
    for _, c in [*iter_compares(spec.rules.entry, "e"), *iter_compares(spec.rules.exit, "x")]:
        if isinstance(c.right, Value):
            vals = out.setdefault((c.left.indicator, c.left.window, c.left.of), [])
            if c.right.value not in vals:
                vals.append(c.right.value)
    return out


def trade_chart_view(spec: Strategy, trial) -> list[dict]:
    """종목별 매매 지점 차트 값. 지표 값은 백테스트가 조건 계산에 쓴 것(trial.series)을 그대로 옮긴다.

    [{"ticker", "name", "close", "price_lines": [{"label", "values"}], "price_refs": [숫자],
      "panels": [{"lines": [{"label", "values"}], "refs": [숫자]}],
      "buys": [{"date", "signal_date", "price"}], "sells": [...]}]
    date 는 체결일(다음 날 시가), signal_date 는 판단한 날(종가 기준). 첫날 매수처럼 신호 없는 체결은 signal_date None.
    가격 지표(그 종목의 종가·이동평균)는 종가 차트에, 나머지(RSI, 다른 대상의 값 등)는 단위·대상별 보조 차트에.
    """
    series = getattr(trial, "series", None) or {}
    refs = _thresholds(spec)
    out = []
    for a in spec.assets:
        s = series.get(a.ticker)
        if s is None:
            continue
        price_lines, price_refs, panels = [], [], {}
        for ind in s["indicators"]:
            key = (ind["indicator"], ind["window"], ind["of"])
            line = {"label": operand_label(*key), "values": _series(ind["values"])}
            if INDICATORS[ind["indicator"]]["unit"] == "price" and ind["of"] == "self":
                if ind["indicator"] != "close":          # 종가는 이미 그린다
                    price_lines.append(line)
                price_refs += [v for v in refs.get(key, []) if v not in price_refs]
            else:
                p = panels.setdefault((ind["of"], INDICATORS[ind["indicator"]]["unit"]), {"lines": [], "refs": []})
                p["lines"].append(line)
                p["refs"] += [v for v in refs.get(key, []) if v not in p["refs"]]
        fills = [f for f in trial.fills if f["ticker"] == a.ticker]
        out.append({"ticker": a.ticker, "name": a.name, "close": _series(s["close"]),
                    "price_lines": price_lines, "price_refs": price_refs, "panels": list(panels.values()),
                    "buys": [_point(f) for f in fills if f["side"] == "buy"],
                    "sells": [_point(f) for f in fills if f["side"] == "sell"]})
    return out


def _point(f: dict) -> dict:
    return {"date": f["date"], "signal_date": f.get("signal_date"), "price": _num(f["price"])}


def signal_hover(p: dict) -> str:
    """보조 차트 세로 점선에 마우스를 올렸을 때 보이는 문구."""
    return f"신호 {p['signal_date']} 종가 기준 → 체결 {p['date']} 시가"


def trade_window(dates: list[str], t: dict, pad: int = 20) -> Optional[tuple[int, int]]:
    """매매 지점 차트를 처음 열 때 보일 구간 (dates 위치, 양 끝 포함): 첫 매매 pad 거래일 전 ~ 마지막 매매 pad 거래일 후.
    매매가 없으면 None (전체 기간)."""
    pos = {d: i for i, d in enumerate(dates)}
    idx = sorted(pos[p["date"]] for p in [*t.get("buys", []), *t.get("sells", [])] if p["date"] in pos)
    if not idx:
        return None
    return max(0, idx[0] - pad), min(len(dates) - 1, idx[-1] + pad)


def history_stamp(item: dict) -> str:
    """지난 대화 목록의 작은 날짜·시간 '11-02 10:08'. created 가 없으면 id(YYYYmmdd-HHMMSS-…)에서 읽는다."""
    for raw, fmt in ((item.get("created", ""), None), (item.get("id", "")[:15], "%Y%m%d-%H%M%S")):
        try:
            d = datetime.fromisoformat(raw) if fmt is None else datetime.strptime(raw, fmt)
            return d.strftime("%m-%d %H:%M")
        except (TypeError, ValueError):
            continue
    return ""


def trade_rows(trades: list[dict]) -> list[dict]:
    """거래 내역 → [{"ticker", "name", "entry_date", "entry_price", "exit_date", "exit_price", "holding_days",
    "return_pct"(비율), "open"}]. 백테스트 trades 와 이전 판에서 저장한 화면용 표(한국어 열 이름) 둘 다 받는다."""
    out = []
    for tr in trades:
        if "종목" in tr:                                  # 이전 판 형식
            r = tr.get("수익률(%)")
            tr = {"ticker": tr["종목"], "entry_date": tr["산 날"], "entry_price": tr["산 값"],
                  "exit_date": None if tr["판 날"] == "보유 중" else tr["판 날"], "exit_price": tr["판 값"],
                  "holding_days": tr["보유일"], "return_pct": None if r is None else r / 100}
        info = ticker_info(tr["ticker"])
        out.append({"ticker": tr["ticker"], "name": info.name if info else tr["ticker"],
                    "entry_date": tr["entry_date"], "entry_price": _num(tr["entry_price"]),
                    "exit_date": tr["exit_date"], "exit_price": _num(tr["exit_price"]),
                    "holding_days": tr["holding_days"], "return_pct": _num(tr["return_pct"], 6),
                    "open": tr["exit_date"] is None})
    return out


def result_view(res: PipelineResult, first_hash: Optional[str]) -> dict:
    t = res.report.trial
    m = t.metrics
    eq = t.equity
    dates = [d.strftime("%Y-%m-%d") for d in eq.index]
    spec = res.report.spec
    bt = spec.backtest
    warnings = [i.message for i in res.report.issues if i.stage == 5 and i.kind == "warn"]
    return {
        "metrics": {"total_return": m["total_return"], "cagr": m["cagr"], "mdd": m["mdd"], "n_trades": m["n_trades"],
                    "n_open_positions": m["n_open_positions"], "kospi_total_return": m["kospi_total_return"],
                    "buy_hold_total_return": m["buy_hold_total_return"], "start": m["start"], "end": m["end"]},
        "chart": {"dates": dates, "strategy": _series(eq), "kospi": _series(t.benchmarks.get("kospi")),
                  "buy_hold": _series(t.benchmarks.get("buy_hold"))},
        "trade_chart": trade_chart_view(spec, t),
        "warnings": warnings,
        "trades": trade_rows(t.trades),
        "hash": t.result_hash, "first_hash": first_hash, "match": first_hash == t.result_hash,
        "costs": f"수수료 {bt.commission_rate:.3%} · 체결 오차 {bt.slippage_rate:.2%} · 매도 세금 반영",
        "notes": list(t.notes),
        "collapsed": False,
    }


def fmt_pct(x: Optional[float], signed: bool = True) -> str:
    if x is None:
        return "—"
    return f"{x:+.1%}" if signed else f"{x:.1%}"


def fmt_won(x: Optional[float]) -> str:
    """74937.42 → '74,937원'."""
    return "—" if x is None else f"{int(round(x)):,}원"


def fmt_ret(x: Optional[float]) -> str:
    """0.04751 → '+4.75%'."""
    return "—" if x is None else f"{x * 100:+.2f}%"


# ---------------------------------------------------------------- 뒤로가기 · 다시하기

def source_card(conv: dict, idx: int) -> Optional[int]:
    """결과 카드(idx)를 만든 전략 카드의 위치. 이전 판 결과는 앞쪽에서 가장 가까운 '백테스트한' 카드."""
    msgs = conv["messages"]
    if msgs[idx].get("card_idx") is not None:
        return msgs[idx]["card_idx"]
    return next((i for i in range(idx - 1, -1, -1)
                 if msgs[i].get("kind") == "understood" and msgs[i].get("used") == "backtest"), None)


def retry_text(conv: dict, idx: int) -> str:
    """'조건 바꿔서 다시' / '고쳐 말하기': 입력창에 채워 둘 원래 문장."""
    m = conv["messages"][idx]
    if m.get("kind") == "understood":
        return m["text"]
    c = source_card(conv, idx)
    return conv["messages"][c]["text"] if c is not None else ""


def collapse_result(conv: dict, idx: int) -> Optional[int]:
    """'전략 카드로 돌아가기': 결과 카드를 접고, 스크롤할 전략 카드 위치를 돌려준다."""
    conv["messages"][idx]["collapsed"] = True
    return source_card(conv, idx)


def expand_result(conv: dict, idx: int) -> None:
    conv["messages"][idx]["collapsed"] = False


def _live_clarify_index(conv: dict) -> Optional[int]:
    for i in range(len(conv["messages"]) - 1, -1, -1):
        m = conv["messages"][i]
        if m.get("kind") == "clarify":
            return None if m.get("done") else i
    return None


def previous_question(conv: dict) -> Optional[int]:
    """지금 열린 되묻기 바로 앞의 되묻기 위치 (2번째 되묻기 → 1번째). 없으면 None.
    답하기는 [앞 되묻기, 사용자 답, 새 되묻기] 순서로 쌓는다."""
    i = _live_clarify_index(conv)
    if i is None or i < 2 or not conv.get("pending") or not conv["pending"]["answers"]:
        return None
    msgs = conv["messages"]
    prev, live = msgs[i - 2], msgs[i]
    if prev.get("kind") == "clarify" and msgs[i - 1]["role"] == "user" and prev["round"] == live["round"] - 1:
        return i - 2
    return None


def back_to_previous_question(conv: dict) -> dict:
    """'이전 질문으로': 지금 되묻기와 그 앞의 답을 지우고 앞 되묻기를 다시 연다 (전에 고른 답은 남겨 둔다)."""
    j = previous_question(conv)
    if j is None:
        return conv
    q = conv["messages"][j]
    del conv["messages"][j + 1:]
    q["done"] = False
    q["rev"] = q.get("rev", 0) + 1                  # 화면 위젯을 새로 만들어 전에 고른 답을 기본값으로 보인다
    conv["pending"] = {"text": conv["pending"]["text"], "answers": conv["pending"]["answers"][:-1]}
    return conv


# ---------------------------------------------------------------- 저장

def save(conv: dict, directory: Path = HISTORY_DIR) -> Optional[Path]:
    """말이 하나라도 있는 대화만 저장한다."""
    if not conv["messages"]:
        return None
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"{conv['id']}.json"
    p.write_text(json.dumps(conv, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load(conv_id: str, directory: Path = HISTORY_DIR) -> Optional[dict]:
    p = Path(directory) / f"{conv_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_history(directory: Path = HISTORY_DIR) -> list[dict]:
    """[{id, title, created}] 최근 것부터. 읽을 수 없는 파일은 건너뛴다."""
    out = []
    for p in Path(directory).glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": d["id"], "title": d.get("title") or "새 전략", "created": d.get("created", "")})
        except (OSError, ValueError, KeyError):
            continue
    return sorted(out, key=lambda x: x["id"], reverse=True)
