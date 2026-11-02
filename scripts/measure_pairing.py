#!/usr/bin/env python3
"""AI 짝짓기 두 방식을 평가 데이터셋 개발용(dev) 문장으로 비교한다.

설계 문서 "확정 전에 잴 것" 2번, v0.2 구현 4단계(docs/v0.2-plan.md)의 끝난 기준이다.
최종 평가용(test) 문장은 읽지 않는다(eval/README.md).

  span   조각 번호 방식(app/m1/pairing.py). AI 는 조각 번호만 고른다
  quote  근거 글자 방식. AI 가 문장에서 글자를 옮겨 적고, 그 글자 안의 조각으로 바꿔 채점한다
두 방식 모두 근거 검사에서 걸리면 한 번 다시 짝짓는다. 모델 · 온도 · 시드 · 예시 문장은 같다.

채점(바로 진행 문장만)
  - 비교식: 정답 전략서의 살 때 · 팔 때 비교식마다, 같은 쪽에 지표 · 기간 · 비교 방향 · 숫자 · 누구의 값이
    맞는 비교가 있는지. 종가는 말하지 않아도 들어가므로 비어 있어도 맞는 것으로 본다
  - 종목: 짝지은 종목(universe, target)의 종목코드 집합이 정답과 같은지(업종 고르기 문장은 뺀다)
  - 문장 정답: 종목이 맞고 비교식이 모두 맞고 남는 비교가 없음
거절 사전에 걸리는 문장은 AI 를 부르지 않으므로 뺀다.

실행 (api 컨테이너, Ollama 필요. 응답은 /tmp/pairing-cache 에 남겨 다시 돌릴 때 쓴다):
    docker compose exec api python /repo/scripts/measure_pairing.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from app.llm.ollama_client import OllamaClient  # noqa: E402
from app.m1 import pairing, spans  # noqa: E402
from app.repositories import etf_master  # noqa: E402

DATASET = REPO / "eval" / "dataset.jsonl"
CACHE = Path("/tmp/pairing-cache")
DEFAULT_WINDOW = {"rsi": 14}


class CachedClient:
    def __init__(self, inner):
        self.inner = inner
        self.calls = 0
        self.seconds = 0.0
        CACHE.mkdir(parents=True, exist_ok=True)

    def generate_json(self, prompt, json_schema, *, system=None):
        key = hashlib.sha256(
            json.dumps([prompt, json_schema, system], ensure_ascii=False).encode()
        ).hexdigest()
        path = CACHE / f"{key}.json"
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
        else:
            start = time.time()
            try:
                answer = self.inner.generate_json(prompt, json_schema, system=system)
                error = None
            except Exception as exc:  # 형식이 깨진 JSON 도 결과로 센다
                answer, error = {}, f"{type(exc).__name__}: {exc}"
            saved = {"answer": answer, "error": error, "seconds": time.time() - start}
            path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        self.calls += 1
        self.seconds += saved["seconds"]
        if saved["error"]:
            self.errors = getattr(self, "errors", 0) + 1
        return saved["answer"]


# ── 근거 글자 방식 ────────────────────────────────────────────────────

QUOTE_SYSTEM = (
    "너는 한국어 투자 전략 문장을 전략서 빈칸에 나눠 담는 도구다. "
    "값은 문장에서 해당 부분을 글자 그대로 복사한다. 문장에 없는 말을 지어내지 않는다. "
    "문장의 중요한 말을 빠뜨리지 않는다. 설명하지 말고 JSON 만 낸다."
)


def _to_text(value):
    if isinstance(value, str) and ":" in value:
        return value.split(":", 1)[1]
    return value


def _quote_example() -> dict:
    out = copy.deepcopy(pairing.EXAMPLE_OUTPUT)
    out["universe"] = [_to_text(v) for v in out["universe"]]
    for rule in out["rules"]:
        for side in ("entry", "exit"):
            cond = rule[side]
            cond["actions"] = [_to_text(v) for v in cond["actions"]]
            for item in cond["items"]:
                for k in item:
                    item[k] = _to_text(item[k])
    return out


def quote_schema() -> dict:
    text = {"anyOf": [{"type": "null"}, {"type": "string"}]}
    texts = {"type": "array", "items": {"type": "string"}}
    obj = pairing._object
    item = obj({"subject": text, "left": text, "op": text, "right": text})
    cond = {
        "anyOf": [
            {"type": "null"},
            obj(
                {
                    "actions": texts,
                    "join": {"type": "string", "enum": ["one", "all", "any"]},
                    "items": {"type": "array", "items": item, "minItems": 1, "maxItems": pairing.MAX_ITEMS},
                }
            ),
        ]
    }
    rule = obj({"target": {"type": "string"}, "entry": cond, "exit": cond})
    quote = obj(
        {
            "text": {"type": "string"},
            "slot": {"type": "string"},
            "state": {"type": "string", "enum": list(pairing.QUOTE_STATES)},
        }
    )
    return obj(
        {
            "universe": texts,
            "weights": {"type": "array", "items": obj({"asset": text, "value": text})},
            "rebalance": texts,
            "rules": {"type": "array", "items": rule, "maxItems": pairing.MAX_RULES},
            "max_weight": text,
            "backtest": obj({"period": texts, "capital": texts, "dividends": texts}),
            "selection": texts,
            "quotes": {"type": "array", "items": quote},
        }
    )


def quote_prompt(result: spans.SpanResult, feedback=()) -> str:
    guide = pairing.GUIDE.replace("조각", "말").replace("target 에 그 종목 말을", "target 에 그 종목 이름을")
    parts = [
        "값은 모두 문장에서 글자 그대로 복사한다. target 은 each 또는 종목 이름이다.",
        guide,
        "",
        "예시",
        f"문장: {pairing.EXAMPLE_SENTENCE}",
        "출력:",
        json.dumps(_quote_example(), ensure_ascii=False),
        "",
        f"문장: {result.text}",
    ]
    if feedback:
        parts += ["", "앞의 답에서 고칠 것:", *(f"- {line}" for line in feedback)]
    return "\n".join([*parts, "출력:"])


def quote_to_labels(raw: dict, result: spans.SpanResult) -> tuple[dict, list[str]]:
    # 옮겨 적은 글자를 문장에서 찾아, 그 안에 든 조각 중 칸에 맞는 종류의 첫 조각으로 바꾼다
    missing: list[str] = []

    def label(value, field_name):
        if value is None:
            return None
        value = str(value).strip()
        if field_name == "target" and value == "each":
            return "each"
        pos = result.text.find(value)
        if pos < 0:
            missing.append(value)
            return None
        kinds = pairing.FIELD_KINDS[field_name]
        inside = [s for s in result.spans if s.start >= pos and s.end <= pos + len(value) and s.kind in kinds]
        return inside[0].label() if inside else None

    def labels(values, field_name):
        return [x for x in (label(v, field_name) for v in values or []) if x]

    out = {
        "universe": labels(raw.get("universe"), "universe"),
        "rebalance": labels(raw.get("rebalance"), "rebalance"),
        "selection": labels(raw.get("selection"), "selection"),
        "max_weight": label(raw.get("max_weight"), "max_weight"),
        "weights": [
            {"asset": label(w.get("asset"), "weight_asset"), "value": label(w.get("value"), "weight_value")}
            for w in raw.get("weights") or []
            if isinstance(w, dict)
        ],
        "backtest": {
            k: labels((raw.get("backtest") or {}).get(k), k) for k in ("period", "capital", "dividends")
        },
        "quotes": raw.get("quotes") or [],
        "rules": [],
    }
    for r in raw.get("rules") or []:
        if not isinstance(r, dict):
            continue
        rule = {"target": label(r.get("target"), "target") or "each"}
        for side in ("entry", "exit"):
            c = r.get(side)
            if not isinstance(c, dict):
                rule[side] = None
                continue
            rule[side] = {
                "actions": labels(c.get("actions"), "action"),
                "join": c.get("join"),
                "items": [
                    {k: label(i.get(k), k) for k in ("subject", "left", "op", "right")}
                    for i in c.get("items") or []
                    if isinstance(i, dict)
                ],
            }
        out["rules"].append(rule)
    return out, missing


def pair_quote(result: spans.SpanResult, client) -> pairing.PairingOutcome:
    schema = quote_schema()

    def attempt(feedback=()):
        raw = client.generate_json(quote_prompt(result, feedback), schema, system=QUOTE_SYSTEM)
        converted, missing = quote_to_labels(raw, result)
        parsed = pairing.normalize(pairing.parse(converted, result))
        issues = [pairing.EvidenceIssue("quote_not_in_text", m) for m in missing]
        issues += pairing.evidence_issues(parsed, result)
        return raw, parsed, tuple(issues)

    raw1, first, issues1 = attempt()
    if not issues1:
        return pairing.PairingOutcome(first, issues1, issues1, False, (raw1,))
    feedback = [
        f"문장의 '{i.text}' 가 반영되지 않았다"
        if i.code == "unused_span"
        else f"'{i.text}' 는 문장에 그대로 있지 않다"
        for i in issues1
    ]
    raw2, second, issues2 = attempt(feedback)
    return pairing.PairingOutcome(second, issues2, issues1, True, (raw1, raw2))


# ── 채점 ──────────────────────────────────────────────────────────────


def _compares(cond: dict | None) -> list[dict]:
    if cond is None:
        return []
    return cond.get("all") or cond.get("any") or [cond]


def _value_of(span, left_indicator):
    if span is None:
        return None
    m = span.meaning
    if not isinstance(m.get("value"), (int, float)):
        return None
    if m.get("unit") == "percent" and left_indicator in ("ret", "entry_return"):
        return m["value"] / 100
    return m["value"]


def _window_ok(expected: dict, got: dict) -> bool:
    # 말하지 않아 기본값으로 채우는 기간(RSI 14)은 조각에 기간이 없어도 맞다
    want, have = expected.get("window"), got.get("window")
    return have == want or (have is None and DEFAULT_WINDOW.get(expected["indicator"]) == want)


def _item_matches(exp: dict, item: pairing.Item, actions) -> bool:
    left, right, op = exp["left"], exp["right"], exp["op"]
    lm = item.left.meaning if item.left else {}
    hint_ops = {s.meaning.get("op") for s in actions if s.meaning.get("op")}
    exit_kinds = {s.meaning.get("exit_kind") for s in actions}
    # 왼쪽 지표
    if left["indicator"] == "close":
        left_ok = item.left is None or lm.get("indicator") == "close"
    elif left["indicator"] == "entry_return":
        left_ok = lm.get("indicator") == "entry_return" or (
            item.left is None and exit_kinds & {"take_profit", "stop_loss"}
        )
    else:
        left_ok = lm.get("indicator") == left["indicator"] and _window_ok(left, lm)
    # 누구의 값
    of = left.get("of", "self")
    sm = item.subject.meaning if item.subject else {}
    if of in ("KOSPI200", "KOSDAQ"):
        subject_ok = sm.get("of") == of
    elif of in ("self", "VIX"):
        subject_ok = item.subject is None or "ticker" in sm or of == "VIX"
    else:
        subject_ok = sm.get("ticker") == of
    # 오른쪽
    if "value" in right:
        got = _value_of(item.right, left["indicator"])
        if got is None and isinstance(lm.get("value"), (int, float)):
            got = lm["value"]
        right_ok = got is not None and abs(abs(got) - abs(right["value"])) < 1e-9
    else:
        rm = item.right.meaning if item.right else {}
        right_ok = rm.get("indicator") == right["indicator"] and _window_ok(right, rm)
    # 비교 방향
    got_op = item.op.meaning.get("op") if item.op else None
    op_ok = (
        got_op == op
        or (got_op is None and (op in hint_ops or lm.get("op") == op))
        or (left["indicator"] == "entry_return" and op in hint_ops)
    )
    return bool(left_ok and subject_ok and right_ok and op_ok)


def score(row: dict, outcome: pairing.PairingOutcome) -> dict:
    spec = row["expected_spec"]
    p = outcome.pairing
    matched = total = extra = 0
    for side in ("entry", "exit"):
        expected = [c for rule in spec["rules"] for c in _compares(rule.get(side))]
        predicted = []
        for rule in p.rules:
            cond = getattr(rule, side)
            if cond is not None:
                predicted.extend((item, cond.actions) for item in cond.items)
        used = set()
        for exp in expected:
            total += 1
            for k, (item, actions) in enumerate(predicted):
                if k not in used and _item_matches(exp, item, actions):
                    used.add(k)
                    matched += 1
                    break
        extra += len(predicted) - len(used)
    universe_ok = None
    if "tickers" in spec["universe"]:
        got = {s.meaning["ticker"] for s in p.universe if "ticker" in s.meaning}
        got |= {
            r.target.meaning["ticker"]
            for r in p.rules
            if r.target is not None and "ticker" in r.target.meaning
        }
        universe_ok = got == set(spec["universe"]["tickers"])
    return {
        "matched": matched,
        "total": total,
        "extra": extra,
        "universe_ok": universe_ok,
        "sentence_ok": universe_ok is not False and matched == total and extra == 0,
    }


def main() -> int:
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    dev = [r for r in rows if r["split"] == "dev"]
    dictionary = spans.load_dictionary()
    index = spans.InstrumentIndex(
        [(r.ticker, r.name) for r in etf_master.list_all(include_leveraged=True)], dictionary
    )
    methods = {"span": pairing.pair, "quote": pair_quote}
    clients = {name: CachedClient(OllamaClient()) for name in methods}
    summary = {
        name: {
            "matched": 0,
            "total": 0,
            "extra": 0,
            "universe_ok": 0,
            "universe_n": 0,
            "sentence_ok": 0,
            "ok_n": 0,
            "retried": 0,
            "first_issue_rows": 0,
            "final_issue_rows": 0,
            "invented": 0,
            "rows": 0,
        }
        for name in methods
    }
    for row in dev:
        result = spans.find_spans(row["text"], dictionary, index)
        negated = spans.negated_span_ids(result)
        if any(s.kind == "reject" and s.id not in negated for s in result.spans):
            print(f"-- {row['id']} 거절 사전, AI 안 부름")
            continue
        for name, method in methods.items():
            outcome = method(result, clients[name])
            s = summary[name]
            s["rows"] += 1
            s["retried"] += outcome.retried
            s["first_issue_rows"] += bool(outcome.first_issues)
            s["final_issue_rows"] += bool(outcome.issues)
            s["invented"] += sum(
                1 for i in outcome.first_issues if i.code in ("invalid_label", "quote_not_in_text")
            )
            line = (
                f"{row['id']} {row['type']:6} {name:5} retry={int(outcome.retried)} "
                f"남은검사={[i.message() for i in outcome.issues]}"
            )
            if row["type"] == "ok":
                sc = score(row, outcome)
                s["matched"] += sc["matched"]
                s["total"] += sc["total"]
                s["extra"] += sc["extra"]
                s["ok_n"] += 1
                s["sentence_ok"] += sc["sentence_ok"]
                if sc["universe_ok"] is not None:
                    s["universe_n"] += 1
                    s["universe_ok"] += sc["universe_ok"]
                line += (
                    f" 비교식 {sc['matched']}/{sc['total']} 남는비교 {sc['extra']} 종목 {sc['universe_ok']}"
                )
            print(line, flush=True)
    print()
    for name, s in summary.items():
        c = clients[name]
        print(
            f"{name:5} 문장정답 {s['sentence_ok']}/{s['ok_n']}  비교식 {s['matched']}/{s['total']}"
            f"  남는비교 {s['extra']}  종목 {s['universe_ok']}/{s['universe_n']}"
            f"  처음 근거검사 걸린 문장 {s['first_issue_rows']}/{s['rows']}"
            f"  재시도 뒤 {s['final_issue_rows']}"
            f"  지어낸 값 {s['invented']}  호출 {c.calls}회 평균 {c.seconds / max(c.calls, 1):.1f}초"
            f"  JSON 오류 {getattr(c, 'errors', 0)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
