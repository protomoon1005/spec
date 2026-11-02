"""baseline 한 문장 실행: 코드 생성(LLM) → 샌드박스 실행 → 미래 참조 검사 → 체결(자산 곡선). eval 과 화면이 같이 쓴다.

미래 참조 검사 (tests/test_lookahead.py 와 같은 방식, 같은 섭동 함수 core.lookahead.perturb_after)
  기간 거래일의 20·40·60·80% 지점 t 마다, 모든 가격 데이터(종목·지수·VIX)의 t 이후를 바꿔 다시 실행한다.
  판단일이 t 이하인 주문이 하나라도 달라지면 미래 참조. 섭동한 데이터로 실행이 실패하면 '검사 불가'로 따로 적는다.
반복 일치: 같은 문장을 캐시 없이 여러 번 생성·실행해 결과(종류·주문)가 모두 같은지 본다.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

import pandas as pd

from baseline.codegen import FUNC_NAME, CodegenResult, data_keys, generate, tradable
from baseline.orders import execute_orders, same_orders
from baseline.sandbox import SandboxResult, run_code
from core.lookahead import perturb_after
from core.slot_fill import OPTIONS

CUT_FRACTIONS = (0.2, 0.4, 0.6, 0.8)
Loader = Callable[[str], pd.DataFrame]


def load_data(end: str, loader: Optional[Loader] = None) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """baseline 에 줄 데이터: 종목 20 + KS11·KS200·KQ11·VIX, end 까지. (데이터, 받지 못한 키)."""
    if loader is None:
        from core.pipeline import load_prices as loader
    out, missing = {}, []
    for k in data_keys():
        try:
            df = loader(k)
        except Exception:  # noqa: BLE001 — 받지 못한 데이터는 빼고 알린다
            missing.append(k)
            continue
        out[k] = df.loc[:pd.Timestamp(end)].copy()
    return out, missing


def window_calendar(prices: dict[str, pd.DataFrame], start: str, end: str) -> pd.DatetimeIndex:
    """기간 거래일 (코스피 지수 거래일). 자산 곡선·미래 참조 지점의 기준."""
    idx = prices["KS11"].index
    return idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]


def cut_dates(calendar: pd.DatetimeIndex) -> list[str]:
    n = len(calendar)
    return [calendar[min(n - 1, int(n * f))].strftime("%Y-%m-%d") for f in CUT_FRACTIONS]


def check_lookahead(code: str, prices: dict[str, pd.DataFrame], start: str, end: str, base: list[dict],
                    calendar: pd.DatetimeIndex, known: set[str]) -> dict:
    """{"leak": bool, "cuts": [{"cut", "status": same|changed|error, "detail"}]}"""
    cuts = []
    for cut in cut_dates(calendar):
        moved = {k: perturb_after(v, cut) for k, v in prices.items()}
        r = run_code(code, moved, start, end, func=FUNC_NAME, known=known)
        if not r.ok:
            cuts.append({"cut": cut, "status": "error", "detail": r.error})
            continue
        a = [o for o in base if o["date"] <= cut]
        b = [o for o in r.orders if o["date"] <= cut]
        if same_orders(a, b, tol=1e-9):
            cuts.append({"cut": cut, "status": "same", "detail": f"{cut} 이전 주문 {len(a)}건 그대로"})
        else:
            first = _first_diff(a, b)
            cuts.append({"cut": cut, "status": "changed",
                         "detail": f"{cut} 이후 가격만 바꿨는데 {cut} 이전 주문이 바뀜 ({len(a)}건 → {len(b)}건{first})"})
    return {"leak": any(c["status"] == "changed" for c in cuts), "cuts": cuts}


def _first_diff(a: list[dict], b: list[dict]) -> str:
    sa = {(o["date"], o["ticker"], o["side"], round(o["weight"], 9)) for o in a}
    sb = {(o["date"], o["ticker"], o["side"], round(o["weight"], 9)) for o in b}
    d = sorted(sa ^ sb)
    return f", 처음 다른 주문 {d[0][0]} {d[0][1]} {d[0][2]} {d[0][3]:g}" if d else ""


def run_once(text: str, prices: dict[str, pd.DataFrame], start: str, end: str, *, use_cache: bool,
             chat_fn=None) -> dict:
    """생성 1회 + 실행 1회. {"gen": CodegenResult, "run": SandboxResult|None, "outcome": code_ok|code_fail|clarify|reject|error}"""
    gen = generate(text, use_cache=use_cache, chat_fn=chat_fn)
    if gen.kind != "code":
        return {"gen": gen, "run": None, "outcome": gen.kind}
    r = run_code(gen.code, prices, start, end, func=FUNC_NAME, known={t for t, _, _ in tradable()})
    if not r.ok and gen.meta.get("done_reason") == "length":   # 출력 상한(num_predict)에 걸려 코드가 끝나지 않음
        r.error = f"출력 상한 도달 (num_predict {OPTIONS['num_predict']}) — 코드가 끝나지 않음. {r.error}"
    return {"gen": gen, "run": r, "outcome": "code_ok" if r.ok else "code_fail"}


def signature(one: dict) -> tuple:
    """반복 비교용: 종류 + (실행 성공이면) 주문 목록 / (실패면) 실패 단계."""
    if one["outcome"] == "code_ok":
        return ("code_ok", tuple((o["date"], o["ticker"], o["side"], round(o["weight"], 9)) for o in
                                 sorted(one["run"].orders, key=lambda o: (o["date"], o["ticker"], o["side"]))))
    if one["outcome"] == "code_fail":
        return ("code_fail", one["run"].stage)
    return (one["outcome"],)


def trial(text: str, prices: dict[str, pd.DataFrame], start: str, end: str, *, repeats: int = 3,
          use_cache: bool = False, lookahead: bool = True, chat_fn=None,
          on_step: Optional[Callable[[str], None]] = None) -> dict:
    """같은 문장을 repeats 번 생성·실행하고, 실행에 성공한 서로 다른 코드마다 미래 참조를 검사한다.
    {"runs": [...], "consistent": bool, "codes_same": bool, "lookahead": {code_hash: {...}}, "elapsed_s": [...]}"""
    step = on_step or (lambda _: None)
    runs, times = [], []
    for k in range(repeats):
        step(f"LLM 코드 생성·실행 ({k + 1}/{repeats})")
        t0 = time.perf_counter()
        one = run_once(text, prices, start, end, use_cache=use_cache and k == 0, chat_fn=chat_fn)
        one["wall_s"] = time.perf_counter() - t0
        runs.append(one)
        times.append(one["wall_s"])
    sigs = [signature(r) for r in runs]
    hashes = [r["gen"].code_hash for r in runs]
    la: dict[str, dict] = {}
    if lookahead and any(r["outcome"] == "code_ok" for r in runs):
        step("미래 참조 검사")
        cal = window_calendar(prices, start, end)
        known = {t for t, _, _ in tradable()}
        for r in runs:
            h = r["gen"].code_hash
            if r["outcome"] == "code_ok" and h not in la:
                la[h] = check_lookahead(r["gen"].code, prices, start, end, r["run"].orders, cal, known)
    return {"runs": runs, "consistent": len(set(sigs)) == 1, "codes_same": len(set(hashes)) == 1,
            "lookahead": la, "elapsed_s": times}


def execute(orders: list[dict], prices: dict[str, pd.DataFrame], calendar: pd.DatetimeIndex, *,
            initial_cash: float, commission_rate: float, slippage_rate: float) -> dict:
    return execute_orders(orders, prices, calendar, initial_cash=initial_cash, commission_rate=commission_rate,
                          slippage_rate=slippage_rate)


__all__ = ["CodegenResult", "SandboxResult", "load_data", "window_calendar", "cut_dates", "check_lookahead",
           "run_once", "trial", "execute", "signature"]
