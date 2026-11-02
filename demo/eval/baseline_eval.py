"""우리 방식 vs 비교 대상(LLM 코드 직접 생성), 개발용 문장 전체. `python -m eval.run --baseline` 이 부른다.

문장마다 (모두 캐시 없이 실제 LLM 호출)
  우리 방식: core.pipeline.run 3회 → 첫 회 판정, 3회 결과 일치, 응답 시간(run 전체 벽시계).
             바로 진행이면 같은 미래 참조 검사(가격 섭동)를 우리 백테스트에도 한다.
  baseline : 코드 생성 + 샌드박스 실행 3회 → 첫 회 결과, 실행 실패, 미래 참조(서로 다른 코드마다), 3회 일치, 응답 시간
             (생성 + 실행 1회. 미래 참조 검사 시간은 빼고).
baseline 의 기간 인자는 항상 기본 기간(core.decide.DEFAULTS)이다 — 우리 방식이 문장에서 읽은 기간을 넘기지 않는다.
정답 전략서(expected_spec)가 dict 로 채워진 문장만 주문 일치 정확도를 센다. 지금 "TODO" 면 "정답 미작성".
"""
from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from baseline import compare as B
from baseline.codegen import MODEL, build_prompt
from baseline.orders import match_counts, orders_from_fills, same_orders
from baseline.sandbox import TIMEOUT_S
from core.backtest import run_backtest, slot_weights
from core.decide import DEFAULTS
from core.lookahead import perturb_after
from core.pipeline import load_prices, run
from core.schema import Strategy
from core.slot_fill import OPTIONS, THINK

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "out" / "baseline_run.jsonl"
REPORT = ROOT / "eval" / "report_baseline.md"
REPEATS = 3
OUR_TO_LABEL = {"proceed": "ok", "clarify": "clarify", "reject": "reject", "error": "error"}
BASE_TO_LABEL = {"code_ok": "ok", "code_fail": "fail", "clarify": "clarify", "reject": "reject", "error": "error"}


# ---------------------------------------------------------------- 우리 방식

def our_signature(r) -> str:
    return json.dumps({"verdict": r.verdict, "spec": r.spec if r.verdict == "proceed" else None,
                       "questions": sorted(q.id for q in r.questions),
                       "rejection": r.rejection.code if r.rejection else None}, ensure_ascii=False, sort_keys=True)


def our_orders(spec: Strategy, fills: list[dict], calendar: pd.DatetimeIndex) -> list[dict]:
    w = dict(zip([a.ticker for a in spec.assets], slot_weights(spec)))
    return orders_from_fills(fills, w, calendar)


def our_lookahead(spec: Strategy, prices: dict[str, pd.DataFrame]) -> dict:
    """우리 백테스트에 같은 섭동 검사. baseline 데이터(기본 기간 끝까지)로 돌린다."""
    try:
        base = run_backtest(spec, data=prices)
    except Exception as e:  # noqa: BLE001
        return {"leak": None, "error": str(e), "cuts": []}
    cal = B.window_calendar(prices, spec.backtest.start, spec.backtest.end)
    kcal = prices["KS11"].index
    a_all = our_orders(spec, base.fills, kcal)
    cuts = []
    for cut in B.cut_dates(cal):
        moved = run_backtest(spec, data={k: perturb_after(v, cut) for k, v in prices.items()})
        a = [o for o in a_all if o["date"] <= cut]
        b = [o for o in our_orders(spec, moved.fills, kcal) if o["date"] <= cut]
        cuts.append({"cut": cut, "status": "same" if same_orders(a, b, tol=1e-9) else "changed"})
    return {"leak": any(c["status"] == "changed" for c in cuts), "cuts": cuts}


def run_ours(text: str, prices: dict[str, pd.DataFrame]) -> dict:
    sigs, times, first = [], [], None
    for k in range(REPEATS):
        t0 = time.perf_counter()
        r = run(text, use_cache=False, prices=load_prices)
        times.append(time.perf_counter() - t0)
        sigs.append(our_signature(r))
        if k == 0:
            first = r
    out = {"verdict": first.verdict, "label": OUR_TO_LABEL[first.verdict], "consistent": len(set(sigs)) == 1,
           "verdicts": [json.loads(s)["verdict"] for s in sigs], "elapsed_s": [round(t, 2) for t in times],
           "spec": first.spec if first.verdict == "proceed" else None,
           "rejection": first.rejection.code if first.rejection else None,
           "questions": [q.id for q in first.questions], "orders": None, "lookahead": None}
    if first.verdict == "proceed" and first.strategy is not None:
        spec = first.strategy
        if spec.backtest.end <= DEFAULTS["end"]:          # baseline 데이터(기본 기간 끝까지) 안의 전략서만
            out["lookahead"] = our_lookahead(spec, prices)
        trial = first.report.trial
        if trial is not None:
            out["orders"] = our_orders(spec, trial.fills, prices["KS11"].index)
    return out


# ---------------------------------------------------------------- baseline

def _run_view(one: dict) -> dict:
    g, r = one["gen"], one["run"]
    return {"outcome": one["outcome"], "message": g.message, "code": g.code, "code_hash": g.code_hash,
            "gen_s": round(g.elapsed_s, 2), "wall_s": round(one["wall_s"], 2), "done_reason": g.meta.get("done_reason"),
            "ok": r.ok if r else None, "error": r.error if r else None, "stage": r.stage if r else None,
            "traceback": r.traceback if r else None, "n_orders": len(r.orders) if r and r.ok else None,
            "orders": r.orders if r and r.ok else None}


def run_baseline(text: str, prices: dict[str, pd.DataFrame]) -> dict:
    t = B.trial(text, prices, DEFAULTS["start"], DEFAULTS["end"], repeats=REPEATS, use_cache=False)
    runs = [_run_view(x) for x in t["runs"]]
    first = runs[0]
    la = t["lookahead"].get(first["code_hash"]) if first["code_hash"] else None
    return {"outcome": first["outcome"], "label": BASE_TO_LABEL[first["outcome"]], "consistent": t["consistent"],
            "codes_same": t["codes_same"], "outcomes": [r["outcome"] for r in runs],
            "elapsed_s": [r["wall_s"] for r in runs], "runs": runs, "lookahead": la,
            "lookahead_any": any(v["leak"] for v in t["lookahead"].values()),
            "lookahead_all": t["lookahead"]}


# ---------------------------------------------------------------- 정답 주문

def gold_orders(expected, prices) -> Optional[tuple[list[dict], str, str]]:
    if not isinstance(expected, dict):
        return None
    spec = Strategy.model_validate(expected)
    res = run_backtest(spec, data=prices)
    return our_orders(spec, res.fills, prices["KS11"].index), spec.backtest.start, spec.backtest.end


def _window(orders: Optional[list[dict]], start: str, end: str, cal: pd.DatetimeIndex) -> list[dict]:
    if orders is None:
        return []
    k = int(cal.searchsorted(pd.Timestamp(start)))
    lo = cal[max(0, k - 1)].strftime("%Y-%m-%d")       # 첫날 체결용 전 거래일 판단 포함
    return [o for o in orders if lo <= o["date"] <= end]


# ---------------------------------------------------------------- 보고서

def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({n / d:.0%})" if d else "0/0"


def _t(xs: list[float]) -> str:
    return f"평균 {statistics.mean(xs):.1f}s · 중앙값 {statistics.median(xs):.1f}s · 최대 {max(xs):.1f}s" if xs else "—"


def _err_kind(r: dict) -> str:
    if r.get("done_reason") == "length":
        return f"출력 상한 도달 (num_predict {OPTIONS['num_predict']}, 같은 줄 반복 생성)"
    if r["stage"] == "check" and "import" in (r["error"] or ""):
        return "허용 안 된 import (정적 검사)"
    if r["stage"] in ("check", "timeout", "output", "process"):
        return {"check": "정적 검사", "timeout": "타임아웃", "output": "출력 형식·종목", "process": "프로세스"}[r["stage"]]
    return (r["error"] or "").split(":")[0] or "기타"


def report(rows: list[dict], missing: list[str]) -> str:
    n = len(rows)
    gold = {r["id"]: r["gold"] for r in rows}
    L = []
    L.append("# 우리 방식 vs LLM 코드 직접 생성 (개발용 문장)\n")
    L.append(f"실행: {datetime.now().strftime('%Y-%m-%d %H:%M')} · `python -m eval.run --baseline` · 문장 {n}개 · "
             f"각 {REPEATS}회 (캐시 없이 실제 LLM 호출)\n")
    L.append("## 조건\n")
    L.append(f"- 모델·설정 (두 방식 같음): {MODEL}, options {json.dumps(OPTIONS)}, think {str(THINK).lower()}")
    L.append("- baseline 프롬프트: 데이터 형식·출력 형식·실행 환경(Python/pandas/numpy 판)만. 우리 사전·규칙·판정은 넣지 않음. "
             "모호하면 `NEED_CLARIFICATION`, 구현 불가면 `CANNOT_IMPLEMENT` 로 답할 수 있게 함 (전문은 아래). "
             "구조화 출력은 쓰지 않음 (코드를 자유롭게)")
    L.append(f"- baseline 실행: 별도 프로세스, import 는 pandas·numpy 만, 파일·네트워크 금지, 타임아웃 {TIMEOUT_S}초 "
             "(Docker 아님 — baseline/sandbox.py 의 한계 참고). 실행 실패는 오답")
    L.append(f"- baseline 의 기간 인자: 항상 기본 기간 {DEFAULTS['start']} ~ {DEFAULTS['end']}, 데이터는 그 끝날까지 "
             "(종목 20 + KS11·KS200·KQ11·VIX)" + (f". 받지 못한 데이터: {missing}" if missing else ""))
    L.append("- 미래 참조: 기간 거래일 20·40·60·80% 지점 t 마다 t 이후 가격(모든 데이터)을 바꿔 다시 실행, "
             "판단일 ≤ t 주문이 달라지면 미래 참조 (tests/test_lookahead.py 와 같은 섭동 함수). "
             "우리 방식도 바로 진행된 전략서에 같은 검사를 함")
    L.append("- 응답 시간: 우리 = run() 전체 (빈칸 채우기 + 판정 + 검사 1~5단계, 5단계 시험 백테스트 포함). "
             "baseline = 코드 생성 + 샌드박스 실행 1회 (미래 참조 검사 제외)")
    L.append("- 판정 대응 (baseline): 코드를 냄 → ok(진행), NEED_CLARIFICATION → clarify, CANNOT_IMPLEMENT → reject, "
             "그 밖의 응답 → error. 코드를 냈지만 실행에 실패하면 fail (오답)\n")

    ours_ok = [r for r in rows if r["ours"]["verdict"] == "proceed"]
    b_code = [r for r in rows if r["base"]["outcome"] in ("code_ok", "code_fail")]
    b_fail = [r for r in b_code if r["base"]["outcome"] == "code_fail"]
    b_leak = [r for r in rows if r["base"]["lookahead"] and r["base"]["lookahead"]["leak"]]
    b_leak_any = [r for r in rows if r["base"]["lookahead_any"]]
    o_leak = [r for r in ours_ok if r["ours"]["lookahead"] and r["ours"]["lookahead"]["leak"]]
    o_la_n = sum(1 for r in ours_ok if r["ours"]["lookahead"] and r["ours"]["lookahead"]["leak"] is not None)
    b_la_n = sum(1 for r in rows if r["base"]["lookahead"])
    not_ok = [r for r in rows if r["gold"] != "ok"]
    o_wrong_go = [r for r in not_ok if r["ours"]["verdict"] == "proceed"]
    b_wrong_go = [r for r in not_ok if r["base"]["outcome"] in ("code_ok", "code_fail")]
    o_acc = sum(r["ours"]["label"] == r["gold"] for r in rows)
    b_acc = sum(r["base"]["label"] == r["gold"] for r in rows)
    has_gold = [r for r in rows if r["gold_orders"] is not None]

    L.append("## 요약\n")
    L.append("| | 우리 방식 | LLM 코드 직접 생성 |\n|---|---|---|")
    L.append(f"| 판정 정확도 (ok/clarify/reject, 첫 회) | {_pct(o_acc, n)} | {_pct(b_acc, n)} |")
    L.append(f"| 되묻기·거절이 정답인데 그냥 진행 | {_pct(len(o_wrong_go), len(not_ok))} | "
             f"{_pct(len(b_wrong_go), len(not_ok))} (코드를 만듦) |")
    L.append(f"| 진행(전략서 / 코드) 건수 | {len(ours_ok)} | {len(b_code)} |")
    L.append(f"| 실행 실패 (진행한 것 중) | — (검사 5단계 시험 실행) | {_pct(len(b_fail), len(b_code))} |")
    L.append(f"| 미래 참조 (검사한 것 중, 첫 회) | {len(o_leak)}/{o_la_n} | {len(b_leak)}/{b_la_n} |")
    L.append(f"| 미래 참조 (3회 중 한 번이라도) | — | {len(b_leak_any)}건 |")
    L.append(f"| {REPEATS}회 반복 결과 일치 | {_pct(sum(r['ours']['consistent'] for r in rows), n)} | "
             f"{_pct(sum(r['base']['consistent'] for r in rows), n)} |")
    L.append(f"| {REPEATS}회 반복 코드 글자까지 같음 | — | {_pct(sum(r['base']['codes_same'] for r in b_code), len(b_code))} "
             f"(코드를 낸 문장 중) |")
    L.append(f"| 응답 시간 (모든 회) | {_t([t for r in rows for t in r['ours']['elapsed_s']])} | "
             f"{_t([t for r in rows for t in r['base']['elapsed_s']])} |")
    if has_gold:
        om = [match_counts(r["ours_orders_w"], r["gold_orders"]) for r in has_gold]
        bm = [match_counts(r["base_orders_w"], r["gold_orders"]) for r in has_gold]
        L.append(f"| 주문 일치 — 문장 단위 전부 일치 | "
                 f"{_pct(sum(same_orders(r['ours_orders_w'], r['gold_orders']) for r in has_gold), len(has_gold))} | "
                 f"{_pct(sum(same_orders(r['base_orders_w'], r['gold_orders']) for r in has_gold), len(has_gold))} |")
        L.append(f"| 주문 일치 — 주문 단위 (맞음/정답/예측) | {sum(x['matched'] for x in om)}/{sum(x['gold'] for x in om)}/"
                 f"{sum(x['pred'] for x in om)} | {sum(x['matched'] for x in bm)}/{sum(x['gold'] for x in bm)}/"
                 f"{sum(x['pred'] for x in bm)} |")
    else:
        L.append("| 주문 일치 정확도 | 정답 미작성 | 정답 미작성 |")
    L.append(f"\n주문 일치 정확도: 정답 전략서(expected_spec)가 채워진 문장 {len(has_gold)}개"
             + (" — 지금은 모두 TODO 라 **정답 미작성**. 채우면 같은 명령으로 계산된다." if not has_gold else "") + "\n")

    if b_fail:
        kinds = Counter(_err_kind(r["base"]["runs"][0]) for r in b_fail)
        L.append("### baseline 실행 실패 이유 (첫 회)\n")
        L.append("| 이유 | 건수 |\n|---|---|")
        for k, v in kinds.most_common():
            L.append(f"| {k} | {v} |")
        L.append("")

    L.append("## 판정 혼동행렬\n")
    for title, key in (("우리 방식", "ours"), ("LLM 코드 직접 생성", "base")):
        cols = ["ok", "clarify", "reject", "error"] + (["fail"] if key == "base" else [])
        L.append(f"**{title}**\n")
        L.append("| 정답 \\ 결과 | " + " | ".join(cols) + " |\n|" + "---|" * (len(cols) + 1))
        for g in ("ok", "clarify", "reject"):
            sub = [r for r in rows if gold[r["id"]] == g]
            L.append(f"| {g} ({len(sub)}) | " + " | ".join(str(sum(r[key]["label"] == c for r in sub)) for c in cols) + " |")
        L.append("")

    L.append("## 문장별\n")
    L.append("| id | 정답 | 우리 판정 | 우리 3회 | 우리 시간 | baseline 결과 | 주문 수 | 미래 참조 | baseline 3회 | baseline 시간 | 문장 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    mark = {"code_ok": "코드·실행 성공", "code_fail": "코드·**실행 실패**", "clarify": "되묻기", "reject": "거절",
            "error": "응답 이상"}
    for r in rows:
        o, b = r["ours"], r["base"]
        la = b["lookahead"]
        la_s = "—" if not la else ("**있음**" if la["leak"] else
                                   ("없음" if all(c["status"] == "same" for c in la["cuts"]) else "없음(일부 검사 불가)"))
        ov = o["verdict"] + (f" {o['rejection']}" if o["rejection"] else "")
        L.append(f"| {r['id']} | {r['gold']} | {ov} | {'일치' if o['consistent'] else '**불일치** ' + '/'.join(o['verdicts'])} | "
                 f"{statistics.mean(o['elapsed_s']):.1f}s | {mark[b['outcome']]} | "
                 f"{b['runs'][0]['n_orders'] if b['runs'][0]['n_orders'] is not None else '—'} | {la_s} | "
                 f"{'일치' if b['consistent'] else '**불일치** ' + '/'.join(b['outcomes'])} | "
                 f"{statistics.mean(b['elapsed_s']):.1f}s | {r['text']} |")
    L.append("")

    L.append("## baseline 실행 실패·미래 참조 상세 (첫 회)\n")
    for r in rows:
        b = r["base"]
        f = b["runs"][0]
        if f["outcome"] == "code_fail":
            L.append(f"- {r['id']} 실행 실패 [{_err_kind(f)}] {f['error']}")
        if b["lookahead"] and b["lookahead"]["leak"]:
            for c in b["lookahead"]["cuts"]:
                if c["status"] == "changed":
                    L.append(f"- {r['id']} 미래 참조: {c['detail']}")
        if f["outcome"] in ("clarify", "reject"):
            L.append(f"- {r['id']} {mark[f['outcome']]}: {f['message']}")
    L.append("\n원본(생성 코드 포함): eval/out/baseline_run.jsonl (git 제외)\n")
    L.append("## baseline 프롬프트 전문\n")
    L.append("````text\n" + build_prompt().strip() + "\n````")
    return "\n".join(L) + "\n"


def rereport(note: str = "") -> int:
    """저장된 원본(eval/out/baseline_run.jsonl)으로 보고서만 다시 쓴다. LLM 을 부르지 않는다.
    우리 쪽 미래 참조 검사는 저장된 전략서로 다시 계산한다 (전략서 + 같은 데이터 → 결정론적)."""
    prices, missing = B.load_data(DEFAULTS["end"])
    rows = [json.loads(x) for x in OUT.read_text(encoding="utf-8").splitlines() if x.strip()]
    for r in rows:
        o = r["ours"]
        if o["verdict"] == "proceed" and o["spec"] and o["spec"]["backtest"]["end"] <= DEFAULTS["end"]:
            o["lookahead"] = our_lookahead(Strategy.model_validate(o["spec"]), prices)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    text = report(rows, missing)
    if note:
        text = text.replace("\n## 조건\n", f"\n> {note}\n\n## 조건\n", 1)
    REPORT.write_text(text, encoding="utf-8", newline="\n")
    print(f"보고서 다시 씀: {REPORT.relative_to(ROOT)}")
    return 0


def main(sentences: list[dict]) -> int:
    prices, missing = B.load_data(DEFAULTS["end"])
    if "KS11" not in prices:
        print("코스피 지수(KS11) 데이터를 받지 못해 baseline 비교를 할 수 없습니다.")
        return 1
    kcal = prices["KS11"].index
    rows = []
    for s in sentences:
        ours = run_ours(s["text"], prices)
        base = run_baseline(s["text"], prices)
        g = gold_orders(s.get("expected_spec"), prices)
        row = {"id": s["id"], "text": s["text"], "gold": s["verdict"], "ours": ours, "base": base,
               "gold_orders": None, "ours_orders_w": None, "base_orders_w": None}
        if g is not None:
            go, gs, ge = g
            row.update(gold_orders=_window(go, gs, ge, kcal), ours_orders_w=_window(ours["orders"], gs, ge, kcal),
                       base_orders_w=_window(base["runs"][0]["orders"], gs, ge, kcal))
        rows.append(row)
        la = base["lookahead"]
        print(f"{s['id']:11s} 정답={s['verdict']:7s} 우리={ours['verdict']:8s}{'' if ours['consistent'] else '(불일치)':8s} "
              f"{statistics.mean(ours['elapsed_s']):5.1f}s | baseline={base['outcome']:9s}"
              f"{'' if base['consistent'] else '(불일치)':8s} {statistics.mean(base['elapsed_s']):5.1f}s "
              f"{'미래참조!' if la and la['leak'] else '':6s} {s['text']}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    REPORT.write_text(report(rows, missing), encoding="utf-8", newline="\n")
    print(f"\n보고서: {REPORT.relative_to(ROOT)} · 원본: {OUT.relative_to(ROOT)}")
    return 0
