"""채팅 화면의 상태 처리. Streamlit 을 쓰지 않는다 (pytest 로 테스트).

화면(app.py)은 여기서 만든 대화(dict)를 그리기만 한다. 판정·검사·백테스트는 전부 core.pipeline.run 이 한다.
이 모듈은 run 결과를 화면에 그릴 수 있는 JSON 값(메시지)으로 옮겨 적고, 대화를 data/history 에 저장한다.

대화 (dict, JSON 으로 저장)
  id, title, created, messages: [메시지], pending: 되묻기 중이면 {"text", "answers"} 아니면 None

메시지
  {"role": "user", "text"}
  {"role": "assistant", "kind": "clarify",    "round", "max_rounds", "questions", "done"}
  {"role": "assistant", "kind": "reject",     "code", "label", "plain", "detail", "segments"}
  {"role": "assistant", "kind": "understood", "text", "answers", "parts", "defaults", "checklist", "spec",
                                              "trial_hash", "used"}      # used: None | backtest | revise
  {"role": "assistant", "kind": "result",     "metrics", "chart", "warnings", "trades", "hash", "first_hash",
                                              "match", "costs", "notes"}
  {"role": "assistant", "kind": "note" | "error", "text"}
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from core.decide import DIRECT, MAX_ROUNDS, RESTATE
from core.pipeline import PipelineResult, StepFn

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
    conv["messages"].append({"role": "assistant", "kind": "result", **result_view(res, card.get("trial_hash"))})
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
            "notes": list(res.narration.notes),
            "defaults": list(res.spec.get("defaults_applied", [])),
            "checklist": checklist(res.report),
            "spec": res.report.spec.model_dump(mode="json"),
            "trial_hash": trial.result_hash if trial else None,
            "used": None}


def _num(x, nd: int = 2):
    return None if x is None else round(float(x), nd)


def _series(s) -> Optional[list]:
    return None if s is None else [_num(v) for v in s.to_numpy()]


def result_view(res: PipelineResult, first_hash: Optional[str]) -> dict:
    t = res.report.trial
    m = t.metrics
    eq = t.equity
    dates = [d.strftime("%Y-%m-%d") for d in eq.index]
    pos = {d: i for i, d in enumerate(dates)}
    marks = {"buy": [], "sell": []}
    for f in t.fills:
        if f["side"] in marks and f["date"] in pos:
            marks[f["side"]].append({"date": f["date"], "value": _num(eq.iloc[pos[f["date"]]]),
                                     "ticker": f["ticker"], "price": _num(f.get("price"))})
    bt = res.report.spec.backtest
    warnings = [i.message for i in res.report.issues if i.stage == 5 and i.kind == "warn"]
    return {
        "metrics": {"total_return": m["total_return"], "cagr": m["cagr"], "mdd": m["mdd"], "n_trades": m["n_trades"],
                    "n_open_positions": m["n_open_positions"], "kospi_total_return": m["kospi_total_return"],
                    "buy_hold_total_return": m["buy_hold_total_return"], "start": m["start"], "end": m["end"]},
        "chart": {"dates": dates, "strategy": _series(eq), "kospi": _series(t.benchmarks.get("kospi")),
                  "buy_hold": _series(t.benchmarks.get("buy_hold")), "buys": marks["buy"], "sells": marks["sell"]},
        "warnings": warnings,
        "trades": [{"종목": tr["ticker"], "산 날": tr["entry_date"], "산 값": tr["entry_price"],
                    "판 날": tr["exit_date"] or "보유 중", "판 값": tr["exit_price"], "보유일": tr["holding_days"],
                    "수익률(%)": None if tr["return_pct"] is None else round(tr["return_pct"] * 100, 2)}
                   for tr in t.trades],
        "hash": t.result_hash, "first_hash": first_hash, "match": first_hash == t.result_hash,
        "costs": f"수수료 {bt.commission_rate:.3%} · 체결 오차 {bt.slippage_rate:.2%} · 매도 세금 반영",
        "notes": list(t.notes),
    }


def fmt_pct(x: Optional[float], signed: bool = True) -> str:
    if x is None:
        return "—"
    return f"{x:+.1%}" if signed else f"{x:.1%}"


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
