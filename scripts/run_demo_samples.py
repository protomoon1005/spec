#!/usr/bin/env python3
"""11월 데모 샘플 케이스 일괄 실행 — 화면 흐름 그대로 HTTP 로 돈다.

가입(또는 로그인) → 설문 → 성향 확정 → 전략 컴파일(+되묻기 자동 응답) →
검증(POST /specs/{id}/validate) → 백테스트(POST /backtest/runs) → 결과 조회.

결과는 frontend/public/data/demo-samples.json (형식 고정 — 프론트가 이 형식으로 그린다).

    docker compose exec api python /repo/scripts/run_demo_samples.py
    docker compose exec api python /repo/scripts/run_demo_samples.py --only 2,S1
    docker compose exec api python /repo/scripts/run_demo_samples.py --skip-compile   # 이전 spec_id 재사용

DB 에 직접 쓰지 않는다. 계정은 demo-p1 … demo-p5 (있으면 로그인해 재사용).
Ollama 가 하나라 전부 순차 실행한다. 진행 로그는 stderr 로 찍는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "frontend" / "public" / "data" / "demo-samples.json"
ETF_MASTER = REPO_ROOT / "db" / "seeds" / "04_etf_master.csv"
API = os.environ.get("API_BASE_URL", "http://localhost:8000")

# 화면(frontend/app/backtest/page.tsx)이 쓰는 값. 시작일은 서버 기본값(2023-01-01).
PERIOD_START = "2023-01-01"
PERIOD_END = "2025-12-31"
MAX_ROUNDS = 3
STREAM_TIMEOUT = 700  # 서버 스트림 상한 600초 + 여유
BACKTEST_TIMEOUT = 1800
BASELINE_SECONDS = 30
BASELINE_DATE = "2026-08-22"

LEVEL_LABELS = {1: "안정투자형", 2: "안정추구형", 3: "위험중립형", 4: "성장투자형", 5: "공격투자형"}

# answers: 라운드별 미리 정한 답. 없거나 다 쓰면 policy 를 따른다.
#   policy "first" = 첫 선택지(선택지가 없으면 fallback), "stop" = 되묻기에서 멈춘다(그 질문이 곧 결과).
#   answers 의 "~낱말" 은 그 낱말이 든 선택지를 고른다. stop_when 낱말이 질문에 보이면 거기서 멈춘다.
# expect_block: 의도된 실패 단계. None 이면 통과가 기대값. "compile" · "validation".
CASES: list[dict] = [
    {
        "no": "1",
        "level": 1,
        "text": "돈 좀 안전하게 불려줘",
        "axis": "막연한 요청이 되묻기로 수렴",
        "expected": "채권형 비중 상단, 기술섹터 낮은 상한",
        "answers": ["안전하게"],
        "policy": "first",
        "expect_block": None,
        "check": "bond_heavy",
    },
    {
        "no": "2",
        "level": 3,
        "text": "요즘 반도체가 흥한다던데 거기 비중을 좀 싣되 크게 잃지는 않게 해줘",
        "axis": "섹터 지목+손실 제약",
        "expected": "반도체 ETF 편입 + max_drawdown 반영",
        "answers": [],
        "policy": "first",
        "expect_block": None,
        "check": "semis_mdd",
    },
    {
        "no": "3",
        "level": 2,
        "text": "요즘 시장이 불안한데 지키는 쪽으로 해줘",
        "axis": "방어 표현이 signal_rules 에",
        "expected": "변동성 지수 기반 온도 규칙 강화",
        "answers": ["안전하게"],
        "policy": "first",
        "expect_block": None,
        "check": "defensive_rules",
    },
    {
        "no": "4",
        "level": 5,
        "text": "수익률 높게 가고 손실은 어느 정도 감수할게",
        "axis": "공격형 상단 사용",
        "expected": "주식형 상한 근처",
        "answers": ["특정 업종에 집중"],
        "policy": "first",
        "expect_block": None,
        "check": "equity_heavy",
    },
    {
        "no": "5",
        "level": 4,
        "text": "국내만 말고 미국도 좀 섞어줘",
        "axis": "지역 분산",
        "expected": "해외주식형 편입",
        "answers": [],
        "policy": "first",
        "expect_block": None,
        "check": "us_equity",
    },
    {
        "no": "6",
        "level": 3,
        "text": "금리 내린다던데 채권 담아야 하나",
        "axis": "거시 참조",
        "expected": "채권형 후보",
        "answers": [],
        "policy": "first",
        "expect_block": None,
        "check": "has_bond",
    },
    {
        "no": "7",
        "level": 2,
        "text": "분기에 한 번만 손대고 싶어",
        "axis": "리밸런싱 트리거",
        "expected": "freq 분기",
        "answers": ["안전하게"],
        "policy": "first",
        "expect_block": None,
        "check": "quarterly",
    },
    {
        "no": "8",
        "level": 4,
        "text": "한 종목에 30% 넘게는 싫어",
        "axis": "사용자 제약",
        "expected": "max_weight 0.30",
        "answers": ["안전하게"],
        "policy": "first",
        "expect_block": None,
        "check": "max_weight_30",
    },
    {
        "no": "9",
        "level": 3,
        "text": "오늘 저녁 뭐 먹지 아무튼 투자 좀 해줘",
        "axis": "오프토픽 제거 후 되묻기 수렴",
        "expected": "되묻기 후 전략 생성",
        "answers": ["안전하게"],
        "policy": "first",
        "expect_block": None,
        "check": "asked_then_done",
    },
    {
        "no": "10",
        "level": 3,
        "text": "최대 5%만 잃고 싶은데 기술주로 대박 내줘",
        "axis": "모순 요청이 3단 논리 검증에서 차단",
        "expected": "검증 실패 + 사유",
        "answers": [],
        "policy": "first",
        "expect_block": "validation",
        "check": None,
    },
    {
        "no": "S1",
        "level": 1,
        "text": "수익률 높게 가고 손실은 어느 정도 감수할게",
        "axis": "성향 위반이 생성 단계에서 불가",
        # 성향 1 이 담을 수 있는 G4 주식(PLUS 고배당주 등)이 들어가는 것은 정상이다
        "expected": "고위험 등급(G1·G2) 제외가 excluded 에 사유와 함께 남는다",
        "answers": ["특정 업종에 집중"],
        "policy": "first",
        "expect_block": "compile",
        "check": "high_risk_excluded",
    },
    {
        "no": "S2",
        "level": 3,
        "texts": [
            "KODEX 200 하고 TIGER 미국S&P500 두 개만 담아줘. 한 종목에 20% 넘게는 싫어",
            "KODEX 200 하나만 담아줘. 최대 30%까지만",
            "TIGER 미국S&P500 하고 KODEX 국고채3년 두 개만, 각각 많아야 25%",
        ],
        "axis": "범위 자동 보정 (case B: 상한 합이 투자 가능 비중보다 작음)",
        "expected": "검증 3단에서 상한을 늘려 보정(adjusted_bounds.case = B), 통과",
        "answers": [],
        "policy": "first",
        "expect_block": None,
        "check": "case_b",
    },
    {
        "no": "S3",
        "level": 3,
        "text": "TIGER 방송통신 담아줘",
        "axis": "상장폐지 종목 지목",
        "expected": "생성 단계에서 '상장폐지' 사유로 제외",
        # 이름이 'TIGER' 로만 잡혀 되물으면 "(상장폐지)" 표시가 붙은 방송통신을 골라 완료까지 간다.
        # 완료 결과의 excluded 에 DELISTED 가 남고 전략서에 098560 이 없으면 막힌 것이다.
        "answers": ["~(상장폐지)"],
        "policy": "first",
        "expect_block": "compile",
        "check": "delisted",
    },
]
REPRO_COMPILE_CASE = "2"  # 같은 문장을 새로 두 번 컴파일해 비교할 케이스


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def load_master() -> dict[str, dict]:
    with ETF_MASTER.open(encoding="utf-8-sig") as f:
        return {row["ticker"]: row for row in csv.DictReader(f)}


MASTER = load_master()


# ── 계정 · 성향 ─────────────────────────────────────────────────────


class Client:
    def __init__(self) -> None:
        self.http = httpx.Client(base_url=API, timeout=60.0)
        self.tokens: dict[int, str] = {}

    def h(self, level: int) -> dict:
        return {"Authorization": f"Bearer {self.tokens[level]}"}

    def account(self, level: int) -> None:
        if level in self.tokens:
            return
        name = f"demo-p{level}"
        r = self.http.post("/auth/signup", json={"username": name})
        if r.status_code == 409:
            r = self.http.post("/auth/login", json={"username": name})
        r.raise_for_status()
        self.tokens[level] = r.json()["access_token"]
        me = self.http.get("/profile/me", headers=self.h(level))
        if me.status_code == 200 and me.json()["risk_level"] == level:
            log(f"{name}: 성향 {level} 확인(재사용)")
            return
        # 선택지 점수가 전부 level 이면 환산점이 (level-1)*25 → 정확히 그 등급 (app/profile/survey.py)
        form = self.http.get("/profile/survey/questions", headers=self.h(level)).json()
        answers = []
        for q in form["questions"]:
            # 선택지 순서가 점수 오름차순이거나 내림차순이다. 점수는 응답에 없어 코드 규칙으로 고른다.
            answers.append(
                {"question_code": q["question_code"], "answer_code": _answer_for(q["question_code"], level)}
            )
        self.http.post("/profile/survey", json=answers, headers=self.h(level)).raise_for_status()
        p = self.http.post("/profile", headers=self.h(level))
        p.raise_for_status()
        got = p.json()["risk_level"]
        if got != level:
            raise RuntimeError(f"{name}: 성향이 {got} 로 나왔다(기대 {level})")
        log(f"{name}: 설문 → 성향 {level} 확정")


# 점수가 내림차순인 문항(A1=5). 나머지는 A1=1 오름차순 (app/profile/survey.py QUESTIONS).
_DESCENDING = {"AGE", "ASSET_RATIO"}


def _answer_for(code: str, level: int) -> str:
    return f"A{6 - level}" if code in _DESCENDING else f"A{level}"


# ── 컴파일 ──────────────────────────────────────────────────────────


def _stream(c: Client, level: int, job_id: str) -> dict:
    with c.http.stream("GET", f"/jobs/{job_id}/stream", headers=c.h(level), timeout=STREAM_TIMEOUT) as r:
        event = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event in ("complete", "timeout"):
                data = json.loads(line.split(":", 1)[1])
                if event == "timeout":
                    return {"status": "failed", "reason": "작업 스트림 시간 초과"}
                if data["state"] == "FAILURE":
                    return {"status": "failed", "reason": f"워커 오류: {data.get('error')}"}
                return data["result"]
    return {"status": "failed", "reason": "스트림이 결과 없이 끊겼다"}


def compile_case(c: Client, case: dict, text: str) -> dict:
    level = case["level"]
    rounds: list[dict] = []
    processing = 0.0
    t0 = time.monotonic()
    body: dict = {"input_prompt": text}
    answers = list(case.get("answers", []))
    result: dict = {}
    for rnd in range(MAX_ROUNDS + 1):
        ts = time.monotonic()
        r = c.http.post("/specs/compile", json=body, headers=c.h(level))
        r.raise_for_status()
        result = _stream(c, level, r.json()["job_id"])
        processing += time.monotonic() - ts
        log(f"  컴파일 라운드 {rnd + 1}: {result.get('status')} ({time.monotonic() - ts:.1f}s)")
        if result.get("status") != "need_answer":
            break
        question, choices = result["question"], list(result.get("choices") or [])
        round_rec = {"question": question, "choices": choices, "answer": None}
        rounds.append(round_rec)
        seen = question + " " + " ".join(choices)
        stop_when = case.get("stop_when")
        if case.get("policy") == "stop" or (stop_when and stop_when in seen) or rnd >= MAX_ROUNDS - 1:
            break
        if answers:
            ans = answers.pop(0)
            if ans.startswith("~"):
                # "~낱말" = 그 낱말이 든 선택지를 고른다. 없으면 낱말 그대로 답한다.
                ans = next((ch for ch in choices if ans[1:] in ch), ans[1:])
        elif choices:
            ans = choices[0]
        else:
            ans = "안전하게"
        round_rec["answer"] = ans
        log(f"  되묻기: {question} → {ans}")
        body = {"session_id": result["session_id"], "answer": ans}
    total = time.monotonic() - t0
    status = result.get("status", "failed")
    return {
        "status": status,
        "rounds": rounds,
        "seconds_total": round(total, 2),
        "seconds_processing": round(processing, 2),
        "spec_id": result.get("spec_id") if status == "completed" else None,
        "n_universe": None,
        "universe": [],
        "rebalance": None,
        "failure_reason": result.get("reason") if status == "failed" else None,
        # 작업 결과 그대로 (app/m1/excluded.py · app/m1/constraints.py). 없으면 [] · null
        "excluded": (result.get("excluded") or []) if status == "completed" else [],
        "loss_limit": result.get("loss_limit") if status == "completed" else None,
        "_text": text,
    }


def fill_spec(c: Client, level: int, comp: dict) -> dict | None:
    if not comp.get("spec_id"):
        return None
    r = c.http.get(f"/specs/{comp['spec_id']}", headers=c.h(level))
    if r.status_code != 200:
        comp["failure_reason"] = f"전략서 조회 실패 HTTP {r.status_code}"
        return None
    spec = r.json()
    comp["n_universe"] = len(spec["universe"])
    comp["universe"] = [
        {
            "ticker": u["ticker"],
            "name": u["name"],
            "weight_min": u["weight_min_raw"] if u.get("weight_min_raw") is not None else u["weight_min"],
            "weight_max": u["weight_max_raw"] if u.get("weight_max_raw") is not None else u["weight_max"],
        }
        for u in spec["universe"]
    ]
    comp["rebalance"] = spec.get("rebalance")
    return spec


# ── 검증 · 백테스트 ─────────────────────────────────────────────────


def validate(c: Client, level: int, spec_id: str) -> dict:
    r = c.http.post(f"/specs/{spec_id}/validate", headers=c.h(level))
    if r.status_code != 200:
        return {
            "passed": None,
            "stage_failed": None,
            "reasons": [f"HTTP {r.status_code}: {r.text[:200]}"],
            "adjustments": [],
        }
    v = r.json()
    reasons, adjustments = [], []
    for s in v["stages"]:
        if s["status"] == "failed":
            reasons += [x["message"] for x in s["violations"]]
        ab = s.get("adjusted_bounds")
        if ab and ab.get("case"):
            adjustments.append({"kind": "bounds", "stage": s["stage"], **ab})
        for cf in s.get("clamped_fields") or []:
            adjustments.append({"kind": "hardcap", "stage": s["stage"], **cf})
    return {
        "passed": v["passed"],
        "stage_failed": v["blocked_at"],
        "reasons": reasons,
        "adjustments": adjustments,
    }


def backtest(c: Client, level: int, spec_id: str) -> tuple[dict, dict | None]:
    r = c.http.post(
        "/backtest/runs",
        json={"spec_id": spec_id, "period_start": PERIOD_START, "period_end": PERIOD_END},
        headers=c.h(level),
    )
    r.raise_for_status()
    run_id = r.json()["run_id"]
    log(f"  백테스트 run {run_id} 접수")
    t0 = time.monotonic()
    run: dict = {}
    while time.monotonic() - t0 < BACKTEST_TIMEOUT:
        run = c.http.get(f"/backtest/runs/{run_id}", headers=c.h(level)).json()
        if run["status"] in ("done", "failed"):
            break
        time.sleep(5)
    log(f"  백테스트 run {run_id}: {run.get('status')} ({time.monotonic() - t0:.0f}s)")
    m = run.get("metrics") or {}
    out = {
        "run_id": run_id,
        "status": run.get("status"),
        "cagr": m.get("cagr"),
        "mdd": m.get("mdd"),
        "sharpe": m.get("sharpe"),
        "view_sources": run.get("scorer_sources"),
        "failure_reason": run.get("reason") if run.get("status") != "done" else None,
    }
    if run.get("status") not in ("done", "failed"):
        out["failure_reason"] = f"{BACKTEST_TIMEOUT}초 안에 끝나지 않음"
    return out, run if run.get("status") == "done" else None


EMPTY_VALIDATION = {"passed": None, "stage_failed": None, "reasons": [], "adjustments": []}


def empty_backtest(reason: str | None) -> dict:
    return {
        "run_id": None,
        "status": None,
        "cagr": None,
        "mdd": None,
        "sharpe": None,
        "view_sources": None,
        "failure_reason": reason,
    }


# ── 판정 ────────────────────────────────────────────────────────────


def _group(t: str) -> str:
    return MASTER.get(t, {}).get("group_id", "?")


def content_check(kind: str | None, comp: dict, spec: dict | None, val: dict) -> tuple[bool | None, str]:
    """기대 내용이 실제로 나왔는지. (충족 여부, 설명). 판정 불가면 None."""
    uni = comp["universe"]
    if kind is None:
        return None, ""
    if kind == "asked_then_done":
        ok = bool(comp["rounds"]) and comp["status"] == "completed"
        return ok, f"되묻기 {len(comp['rounds'])}회 후 {comp['status']}"
    codes = [e.get("reason_code") for e in comp.get("excluded") or []]
    if kind == "delisted":
        delisted = [e for e in comp.get("excluded") or [] if e.get("reason_code") == "DELISTED"]
        held = any(u["ticker"] == "098560" for u in uni)
        ok = comp["status"] == "completed" and bool(delisted) and not held
        names = ", ".join(f"{e.get('ticker')} {e.get('reason')}" for e in delisted)
        return ok, f"컴파일 {comp['status']}, excluded DELISTED=[{names}], 098560 편입={held}"
    if kind == "high_risk_excluded":
        high = [u["ticker"] for u in uni if MASTER.get(u["ticker"], {}).get("risk_tag") in ("G1", "G2")]
        group = [e for e in comp.get("excluded") or [] if e.get("reason_code") == "GROUP_PRESET_ZERO"]
        ok = comp["status"] == "completed" and bool(group) and not high
        reason = group[0].get("reason") if group else None
        return ok, f"excluded 코드={codes}, 사유={reason}, G1·G2 편입={high or '없음'}"
    if kind == "case_b":
        bs = [a for a in val["adjustments"] if a.get("kind") == "bounds"]
        case = bs[0]["case"] if bs else None
        return case == "B", f"범위 보정 case={case}"
    if not uni:
        return False, "전략서 없음"
    by_group: dict[str, float] = {}
    for u in uni:
        by_group[_group(u["ticker"])] = by_group.get(_group(u["ticker"]), 0) + (u["weight_max"] or 0)
    gsum = ", ".join(f"{g} 상한합 {v:.2f}" for g, v in sorted(by_group.items()))
    if kind == "bond_heavy":
        ok = by_group.get("BOND", 0) >= max(v for g, v in by_group.items())
        return ok, gsum
    if kind == "equity_heavy":
        ok = by_group.get("EQUITY", 0) >= max(v for g, v in by_group.items())
        return ok, gsum
    if kind == "has_bond":
        return "BOND" in by_group, gsum
    if kind == "no_equity":
        return "EQUITY" not in by_group, gsum
    if kind == "us_equity":
        # 미국 '주식형'만 센다. 미국채(305080)·미국채혼합(284430)은 지역 분산이 아니다.
        us = [
            u["ticker"]
            for u in uni
            if _group(u["ticker"]) == "EQUITY" and MASTER.get(u["ticker"], {}).get("country") == "미국"
        ]
        return bool(us), f"미국 주식형 {len(us)}개{': ' + ', '.join(us) if us else ''} ({gsum})"
    if kind == "semis_mdd":
        semis = [u["ticker"] for u in uni if MASTER.get(u["ticker"], {}).get("sector") == "반도체"]
        mdd = (spec or {}).get("constraint_user", {}) or {}
        mdd_v = mdd.get("max_drawdown")
        return bool(semis), f"반도체 {len(semis)}개, constraint_user.max_drawdown={mdd_v}"
    if kind == "quarterly":
        rb = json.dumps(comp.get("rebalance"), ensure_ascii=False)
        ok = any(k in rb.lower() for k in ("quarter", "분기"))
        return ok, f"rebalance={rb}"
    if kind == "max_weight_30":
        top = max(u["weight_max"] or 0 for u in uni)
        cu = (spec or {}).get("constraint_user", {}) or {}
        mw = cu.get("max_weight_per_asset")
        ok = top <= 0.30 + 1e-9 or (mw is not None and abs(mw - 0.30) < 1e-9)
        return ok, f"종목 상한 최대 {top:.2f}, constraint_user.max_weight_per_asset={mw}"
    if kind == "defensive_rules":
        sr = json.dumps((spec or {}).get("signal_rules"), ensure_ascii=False)
        ok = any(k in sr.lower() for k in ("vix", "regime", "변동성", "temperature"))
        return ok, f"signal_rules={sr[:300]}"
    return None, ""


def _last_question(comp: dict) -> str:
    return comp["rounds"][-1]["question"] if comp["rounds"] else ""


def judge(case: dict, comp: dict, val: dict, bt: dict, check: tuple[bool | None, str]) -> tuple[str, str]:
    ok, detail = check
    expect = case["expect_block"]
    compiled = comp["status"] == "completed"
    passed = bool(val.get("passed"))
    done = bt.get("status") == "done"
    if expect == "compile":
        # S1: 주식형이 끝까지 못 들어오면 생성 단계에서 막힌 것. S3: 상장폐지 사유로 빠졌으면 막힌 것.
        if ok:
            return "blocked_expected", f"생성 단계에서 막힘 — {detail}"
        return "fail", f"생성 단계에서 기대대로 막히지 않음 — {detail}"
    if expect == "validation":
        if compiled and val.get("passed") is False and val.get("stage_failed") == 3:
            return "blocked_expected", "검증 3단(논리)에서 막힘"
        where = (
            "생성"
            if not compiled
            else f"검증 {val.get('stage_failed')}단"
            if val.get("passed") is False
            else "어디서도 안 막힘"
        )
        return "fail", f"의도된 단계(검증 3단)가 아닌 곳: {where}"
    if compiled and passed and done:
        if ok is False and case.get("check") == "us_equity":
            return "pass", f"내용 미충족: 미국 주식형 없음 — {detail}"
        if ok is False:
            # 케이스 5 만 내용 미충족을 통과로 둔다(위). 나머지는 흐름이 돌아도 실패로 센다(2026-10-28 결정).
            return "fail", f"흐름은 통과, 기대 내용 미충족 — {detail}"
        return "pass", detail
    if not compiled:
        return (
            "fail",
            f"컴파일 {comp['status']}: {comp.get('failure_reason') or _last_question(comp)}",
        )
    if not passed:
        return "fail", f"검증 {val.get('stage_failed')}단에서 막힘"
    return "fail", f"백테스트 {bt.get('status')}: {bt.get('failure_reason')}"


# ── 케이스 하나 ─────────────────────────────────────────────────────


def run_case(c: Client, case: dict, prev: dict | None, skip_compile: bool) -> dict:
    level = case["level"]
    c.account(level)
    texts = case.get("texts") or [case["text"]]
    out: dict = {}
    for i, text in enumerate(texts):
        log(f"케이스 {case['no']} (성향 {level}) 「{text}」")
        if skip_compile and prev is not None and (i == 0 or prev.get("text") == text):
            comp = dict(prev["compile"])
            text = prev["text"]
            log(f"  컴파일 생략 — spec_id {comp.get('spec_id')} 재사용")
        else:
            comp = _compile_with_retry(c, case, text)
        spec = fill_spec(c, level, comp)
        val = dict(EMPTY_VALIDATION)
        bt = empty_backtest("전략서가 없어 실행 안 함")
        if comp.get("spec_id"):
            val = validate(c, level, comp["spec_id"])
            log(f"  검증: passed={val['passed']} blocked_at={val['stage_failed']} {val['reasons'][:2]}")
            if val["passed"]:
                bt, _ = _backtest_with_retry(c, level, comp["spec_id"])
            else:
                bt = empty_backtest("검증에서 막혀 실행 안 함 (화면도 검증 실패 시 다시 요청으로 보낸다)")
        check = content_check(case.get("check"), comp, spec, val)
        result, note = judge(case, comp, val, bt, check)
        comp.pop("_text", None)
        out = {
            "no": case["no"],
            "profile_level": level,
            "profile_label": LEVEL_LABELS[level],
            "text": text,
            "axis": case["axis"],
            "expected": case["expected"],
            "compile": comp,
            "validation": val,
            "backtest": bt,
            "result": result,
            "note": note,
        }
        if len(texts) > 1:
            out["note"] = f"[입력 {i + 1}/{len(texts)}번째 시도] " + note
        log(f"  → {result}: {out['note']}")
        if case.get("check") != "case_b" or check[0]:
            break
    return out


def _compile_with_retry(c: Client, case: dict, text: str) -> dict:
    comp = compile_case(c, case, text)
    # 워커 재시작 등으로 작업이 끊겼으면 한 번만 다시 한다.
    if comp["status"] == "failed" and (comp["failure_reason"] or "").startswith(
        ("워커 오류", "스트림", "작업 스트림")
    ):
        log("  작업이 끊겨 다시 시도")
        comp = compile_case(c, case, text)
    return comp


def _backtest_with_retry(c: Client, level: int, spec_id: str) -> tuple[dict, dict | None]:
    bt, run = backtest(c, level, spec_id)
    if bt["status"] != "done" and bt["status"] != "failed":
        log("  백테스트가 끝나지 않아 다시 시도")
        bt, run = backtest(c, level, spec_id)
    return bt, run


# ── 재현성 ──────────────────────────────────────────────────────────


def _key_decision(d: dict) -> tuple:
    return d["date"], json.dumps(d.get("target"), sort_keys=True)


def repro_backtest(c: Client, level: int, spec_id: str, first_run_id: int | None) -> dict:
    runs = []
    if first_run_id is not None:
        runs.append(c.http.get(f"/backtest/runs/{first_run_id}", headers=c.h(level)).json())
    while len(runs) < 2:
        bt, run = _backtest_with_retry(c, level, spec_id)
        if run is None:
            return {
                "spec_id": spec_id,
                "run_ids": [r.get("run_id") for r in runs] + [bt["run_id"]],
                "same_weights": False,
                "same_orders": False,
                "same_curve": False,
                "n_decisions": 0,
                "sample_orders": [],
                "note": f"백테스트 실패: {bt['failure_reason']}",
            }
        runs.append(run)
    a, b = runs
    da, db = a.get("decisions") or [], b.get("decisions") or []
    same_w = [(x["date"], x.get("target")) for x in da] == [(x["date"], x.get("target")) for x in db]
    oa = [(x["date"], (x.get("m2") or {}).get("orders")) for x in da]
    ob = [(x["date"], (x.get("m2") or {}).get("orders")) for x in db]
    curve = [(p["date"], p["strategy"], p["control"], p["market"]) for p in a.get("series") or []]
    curve_b = [(p["date"], p["strategy"], p["control"], p["market"]) for p in b.get("series") or []]
    return {
        "spec_id": spec_id,
        "run_ids": [a["run_id"], b["run_id"]],
        "same_weights": same_w,
        "same_orders": oa == ob,
        "same_curve": curve == curve_b and bool(curve),
        "n_decisions": len(da),
        # 화면(frontend/lib/repro.ts)의 확정 모양: [{date, a: {티커: Δ비중}, b: {티커: Δ비중}}].
        "sample_orders": [
            {"date": x["date"], "a": _order_deltas(x), "b": _order_deltas(y)} for x, y in zip(da[:3], db[:3])
        ],
    }


def _order_deltas(d: dict) -> dict[str, float]:
    return {o["ticker"]: o["deltaWeight"] for o in (d.get("m2") or {}).get("orders") or []}


def _spec_signature(spec: dict) -> dict:
    return {
        "universe": sorted(
            (u["ticker"], u.get("weight_min_raw", u["weight_min"]), u.get("weight_max_raw", u["weight_max"]))
            for u in spec["universe"]
        ),
        "rebalance": spec.get("rebalance"),
        "signal_rules": spec.get("signal_rules"),
        "constraint_user": spec.get("constraint_user"),
    }


def repro_compile(c: Client, case: dict) -> dict:
    ids, sigs = [], []
    for k in range(2):
        log(f"재현성(컴파일) {k + 1}/2 「{case['text']}」")
        comp = _compile_with_retry(c, case, case["text"])
        ids.append(comp.get("spec_id"))
        spec = fill_spec(c, case["level"], comp)
        sigs.append(_spec_signature(spec) if spec else None)
    diff: list[str] = []
    if None in sigs:
        diff.append("한쪽 컴파일이 전략서를 만들지 못함")
    else:
        a, b = sigs
        ta, tb = {t[0]: t[1:] for t in a["universe"]}, {t[0]: t[1:] for t in b["universe"]}
        for t in sorted(set(ta) | set(tb)):
            if ta.get(t) != tb.get(t):
                diff.append(f"universe {t}: {ta.get(t)} ↔ {tb.get(t)}")
        for key in ("rebalance", "signal_rules", "constraint_user"):
            if a[key] != b[key]:
                left, right = json.dumps(a[key], ensure_ascii=False), json.dumps(b[key], ensure_ascii=False)
                diff.append(f"{key}: {left} ↔ {right}")
    return {"text": case["text"], "spec_ids": ids, "same_spec": not diff, "diff": diff}


# ── 메인 ────────────────────────────────────────────────────────────


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head = REPO_ROOT / ".git" / "HEAD"
        try:
            ref = head.read_text().strip()
            if ref.startswith("ref:"):
                return (REPO_ROOT / ".git" / ref.split(" ", 1)[1]).read_text().strip()[:7]
            return ref[:7]
        except OSError:
            return "unknown"


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="실행할 케이스 번호, 쉼표 구분 (예: 2,S1). repro 를 넣으면 재현성 측정")
    ap.add_argument(
        "--skip-compile", action="store_true", help="이전 결과의 spec_id 를 재사용해 검증·백테스트만 다시"
    )
    ap.add_argument("--skip-repro", action="store_true", help="재현성 측정을 건너뛴다(이전 값 유지)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    prev_doc: dict = {}
    if args.out.exists():
        prev_doc = json.loads(args.out.read_text(encoding="utf-8"))
    prev_cases = {x["no"]: x for x in prev_doc.get("cases", [])}

    only = {s.strip() for s in args.only.split(",")} if args.only else None
    selected = [k for k in CASES if only is None or k["no"] in only]
    if args.skip_compile:
        missing = [k["no"] for k in selected if k["no"] not in prev_cases]
        if missing:
            log(f"--skip-compile: 이전 결과에 없는 케이스는 건너뛴다: {missing}")
            selected = [k for k in selected if k["no"] in prev_cases]

    c = Client()
    model = c.http.get("/health").json()
    doc = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "llm_model": os.environ.get("OLLAMA_MODEL") or model.get("llm_model") or "qwen3:8b",
        "period": {"start": PERIOD_START, "end": PERIOD_END},
        "cases": [],
        "latency": prev_doc.get("latency"),
        "reproducibility": prev_doc.get("reproducibility") or {"backtest": None, "compile": None},
    }
    merged = dict(prev_cases)
    for case in selected:
        try:
            merged[case["no"]] = run_case(c, case, prev_cases.get(case["no"]), args.skip_compile)
        except Exception as exc:  # 한 케이스가 죽어도 나머지는 돈다
            log(f"케이스 {case['no']} 예외: {exc!r}")
            merged[case["no"]] = {
                **prev_cases.get(case["no"], {}),
                "no": case["no"],
                "result": "fail",
                "note": f"스크립트 예외: {exc!r}",
            }
        order = [k["no"] for k in CASES]
        doc["cases"] = sorted(merged.values(), key=lambda x: order.index(x["no"]) if x["no"] in order else 99)
        _write(args.out, doc)

    # 응답시간 — 컴파일을 실제로 한 케이스(전략서까지 간 것 + 막힌 것 모두)
    comps = [x["compile"] for x in doc["cases"] if x.get("compile") and x["compile"].get("seconds_total")]
    if comps:
        doc["latency"] = {
            "compile_seconds_median": round(statistics.median(x["seconds_total"] for x in comps), 2),
            "processing_seconds_median": round(statistics.median(x["seconds_processing"] for x in comps), 2),
            "n": len(comps),
            "baseline_seconds": BASELINE_SECONDS,
            "baseline_date": BASELINE_DATE,
        }

    run_repro = not args.skip_repro and (only is None or "repro" in only)
    if run_repro:
        base = next(
            (x for x in doc["cases"] if x.get("result") == "pass" and x["backtest"].get("run_id")), None
        )
        if base:
            log(f"재현성(백테스트) — 케이스 {base['no']} spec {base['compile']['spec_id']}")
            c.account(base["profile_level"])
            doc["reproducibility"]["backtest"] = repro_backtest(
                c, base["profile_level"], base["compile"]["spec_id"], base["backtest"]["run_id"]
            )
        if not args.skip_compile:
            case = next(k for k in CASES if k["no"] == REPRO_COMPILE_CASE)
            c.account(case["level"])
            doc["reproducibility"]["compile"] = repro_compile(c, case)
    _write(args.out, doc)
    log(f"완료 → {args.out}")
    for x in doc["cases"]:
        log(f"  {x['no']:>3} {x.get('result')}: {x.get('note')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
