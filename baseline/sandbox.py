"""LLM 이 만든 코드를 별도 프로세스에서 실행한다. Docker 는 쓰지 않는다 (docs/demo-scope.md).

막는 것 (세 겹)
  1) 실행 전 정적 검사 (이 파일 check_code): import 는 pandas·numpy(와 __future__)만,
     open·exec·eval·compile·__import__·getattr 류 이름과 __dunder__ 속성 접근 금지.
  2) 자식 프로세스의 제한된 builtins: open·exec·eval 없음, import 는 pandas·numpy 만 (baseline/_runner.py).
  3) 자식 프로세스의 감사 훅: 네트워크·프로세스 실행·ctypes·파일 쓰기 금지, 파일 읽기는 파이썬 설치 폴더만.
  + 타임아웃 30초 (넘으면 프로세스를 끝낸다). 자식은 `python -I`(환경 변수·사용자 site 무시)로, 임시 폴더에서 돈다.

한계 (데모 수준): 컨테이너가 아니라 운영체제 격리가 없다. 메모리 상한이 없다. 감사 훅은 같은 인터프리터 안의
방어라 C 확장을 통한 우회까지 막는다고 보장하지 않는다. 신뢰할 수 없는 코드를 돌리는 보안 경계로 쓰지 말 것.

실행 실패(정적 검사 걸림·예외·타임아웃·출력 형식 오류·모르는 종목)는 모두 오답으로 센다.
"""
from __future__ import annotations

import ast
import json
import pickle
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

RUNNER = Path(__file__).resolve().parent / "_runner.py"
TIMEOUT_S = 30
ALLOWED_IMPORTS = {"pandas", "numpy"}
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "globals", "locals", "vars",
                "getattr", "setattr", "delattr", "memoryview", "help", "exit", "quit", "__builtins__"}


@dataclass
class SandboxResult:
    ok: bool
    orders: list[dict] = field(default_factory=list)
    error: Optional[str] = None
    stage: Optional[str] = None          # check | load | run | timeout | output | process
    elapsed_s: float = 0.0
    stdout: str = ""
    traceback: str = ""


def check_code(code: str) -> list[str]:
    """정적 검사. 걸린 이유 목록 (비어 있으면 통과)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"문법 오류: {e.msg} ({e.lineno}행)"]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    out.append(f"import {a.name} (pandas, numpy 만 허용)")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level or (mod.split(".")[0] not in ALLOWED_IMPORTS and mod != "__future__"):
                out.append(f"from {'.' * node.level}{mod} import … (pandas, numpy 만 허용)")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            out.append(f"{node.id} 사용 금지")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            out.append(f".{node.attr} 접근 금지")
    return list(dict.fromkeys(out))


def run_code(code: str, prices: dict[str, pd.DataFrame], start: str, end: str, *, func: str = "strategy",
             known: Optional[set[str]] = None, timeout: float = TIMEOUT_S) -> SandboxResult:
    """생성 코드의 func(prices, start, end) 를 별도 프로세스에서 부른다. known: 사고팔 수 있는 종목코드."""
    t0 = time.perf_counter()
    problems = check_code(code)
    if problems:
        return SandboxResult(False, error="정적 검사: " + "; ".join(problems), stage="check")
    with tempfile.TemporaryDirectory(prefix="baseline-") as work:
        w = Path(work)
        (w / "code.py").write_text(code, encoding="utf-8")
        (w / "args.json").write_text(json.dumps({"func": func, "start": start, "end": end}), encoding="utf-8")
        with open(w / "data.pkl", "wb") as f:
            pickle.dump(prices, f, protocol=pickle.HIGHEST_PROTOCOL)
        try:
            p = subprocess.run([sys.executable, "-I", str(RUNNER), str(w)], cwd=w, capture_output=True,
                               timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return SandboxResult(False, error=f"타임아웃 ({timeout:g}초)", stage="timeout",
                                 elapsed_s=time.perf_counter() - t0)
    dt = time.perf_counter() - t0
    line = next((x for x in reversed(p.stdout.decode("utf-8", "replace").splitlines()) if x.startswith("{")), None)
    if line is None:
        err = p.stderr.decode("utf-8", "replace")[-800:]
        return SandboxResult(False, error=f"자식 프로세스가 결과 없이 끝남 (코드 {p.returncode}) {err}".strip(),
                             stage="process", elapsed_s=dt)
    r = json.loads(line)
    if not r["ok"]:
        return SandboxResult(False, error=r["error"], stage=r.get("stage", "run"), elapsed_s=dt,
                             stdout=r.get("stdout", ""), traceback=r.get("traceback", ""))
    orders = r["orders"]
    if known is not None:
        bad = sorted({o["ticker"] for o in orders} - known)
        if bad:
            return SandboxResult(False, orders, f"사고팔 수 없는 종목코드에 주문: {', '.join(bad)}", "output", dt,
                                 r.get("stdout", ""))
    return SandboxResult(True, orders, elapsed_s=dt, stdout=r.get("stdout", ""))
