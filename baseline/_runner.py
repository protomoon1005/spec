"""샌드박스 자식 프로세스 (baseline/sandbox.py 가 `python -I` 로 띄운다). 표준 라이브러리 + pandas·numpy 만 쓴다.

  python -I _runner.py <작업 폴더>     작업 폴더: code.py, data.pkl, args.json

1) 신뢰하는 준비: 데이터·코드를 읽고 pandas·numpy 를 미리 데워 둔다 (지연 import 를 먼저 끝낸다).
2) 감사 훅(sys.addaudithook)을 건다 — 이후로는 지울 수 없다:
   네트워크(socket)·프로세스 실행(subprocess, os.system/exec/spawn/fork)·ctypes·파일 쓰기·지우기·이름 바꾸기를 막고,
   파일 읽기는 파이썬 설치 폴더(모듈 파일) 안만 허용한다.
3) 생성 코드를 제한된 builtins(open·exec·eval·compile·input·breakpoint 없음, import 는 pandas·numpy 만)로 실행하고
   strategy(prices, start, end) 를 부른다. print 출력은 따로 모은다.
4) 결과를 JSON 한 줄로 진짜 stdout 에 쓴다: {"ok": true, "orders": [...], "stdout": "..."} / {"ok": false, "error": "..."}
"""
import builtins
import io
import json
import os
import pickle
import sys
import traceback

ALLOWED_IMPORTS = {"pandas", "numpy"}
# C 코드(예: Timestamp.strftime → time)가 호출한 쪽 frame 의 __import__ 를 쓰기 때문에 실행 중에는 아래 무해한
# 표준 모듈도 허용한다. 생성 코드가 직접 import 하는 것은 정적 검사(sandbox.check_code)가 pandas·numpy 로 막는다.
RUNTIME_OK = {"time", "datetime", "_datetime", "_strptime", "locale", "calendar", "math", "re", "decimal",
              "_pydecimal", "numbers", "copy", "warnings", "operator", "functools", "itertools", "collections",
              "enum", "typing", "zoneinfo", "unicodedata", "encodings", "codecs", "string", "_strptime"}
REMOVED_BUILTINS = {"open", "exec", "eval", "compile", "input", "breakpoint", "help", "exit", "quit",
                    "globals", "locals", "vars", "memoryview"}
BLOCKED_EVENTS = ("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork",
                  "os.kill", "os.remove", "os.unlink", "os.rename", "os.rmdir", "os.mkdir", "os.chmod",
                  "os.truncate", "os.putenv", "os.unsetenv", "os.startfile", "shutil.", "ctypes.", "winreg.",
                  "urllib.", "http.", "ftplib.", "smtplib.", "webbrowser.", "pty.", "msvcrt.", "_winapi.",
                  "os.chdir", "code.__new__", "function.__new__", "pickle.", "marshal.")
# sys._getframe 는 막지 않는다: pandas 가 경고를 낼 때 내부에서 쓴다 (2026-11-02, 막았더니 멀쩡한 코드가 실패).
# 생성 코드는 sys 를 import 할 수 없어 직접 부르지 못한다.
LIST_EVENTS = ("os.listdir", "os.scandir", "glob.glob", "glob.glob/2")   # 설치 폴더 안만 (모듈 찾기용)


def _emit(stream, obj) -> None:
    stream.write(json.dumps(obj, ensure_ascii=False) + "\n")
    stream.flush()


def _norm_orders(res, pd) -> list:
    if res is None:
        raise ValueError("strategy 가 None 을 반환했습니다 (주문 목록이 아님).")
    if isinstance(res, pd.DataFrame):
        res = res.to_dict("records")
    if not isinstance(res, (list, tuple)):
        raise ValueError(f"strategy 반환값이 list 가 아닙니다: {type(res).__name__}")
    out = []
    for k, o in enumerate(res):
        if not isinstance(o, dict):
            raise ValueError(f"주문 {k}번이 dict 가 아닙니다: {type(o).__name__}")
        missing = [x for x in ("date", "ticker", "side", "weight") if x not in o]
        if missing:
            raise ValueError(f"주문 {k}번에 {missing} 칸이 없습니다: {o!r}"[:300])
        d = pd.Timestamp(o["date"])
        if d is pd.NaT:
            raise ValueError(f"주문 {k}번 date 를 날짜로 읽을 수 없습니다: {o['date']!r}")
        side = str(o["side"]).strip().lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"주문 {k}번 side 가 buy/sell 이 아닙니다: {o['side']!r}")
        w = float(o["weight"])
        if not (w == w) or w < -1e-9 or w > 1 + 1e-9:
            raise ValueError(f"주문 {k}번 weight 가 0~1 이 아닙니다: {o['weight']!r}")
        out.append({"date": d.strftime("%Y-%m-%d"), "ticker": str(o["ticker"]).strip(), "side": side,
                    "weight": min(max(w, 0.0), 1.0)})
    return out


def main(work: str) -> int:
    real_out = sys.stdout
    try:
        with builtins.open(os.path.join(work, "code.py"), encoding="utf-8") as f:
            code = f.read()
        with builtins.open(os.path.join(work, "args.json"), encoding="utf-8") as f:
            args = json.load(f)
        with builtins.open(os.path.join(work, "data.pkl"), "rb") as f:
            prices = pickle.load(f)
        import numpy as np
        import pandas as pd
        # 지연 import 를 미리 끝낸다 (훅 이후에도 설치 폴더 읽기는 되지만 미리 해 두면 덜 흔들린다)
        s = next(iter(prices.values()))["Close"].astype(float)
        s.rolling(5).mean(); s.ewm(span=5).mean(); s.pct_change(); s.diff().clip(lower=0)
        s.resample("ME").last(); s.groupby(s.index.to_period("M")).first(); s.rolling(5).std()
        pd.concat([s, s], axis=1).dropna(); pd.to_datetime(["2024-01-02"]); np.where(s > 0, 1, 0)
        s.index.searchsorted(pd.Timestamp("2024-01-02")); pd.DataFrame({"a": s}).to_dict("records")
        compiled = builtins.compile(code, "<generated>", "exec")
    except Exception as e:  # noqa: BLE001
        _emit(real_out, {"ok": False, "stage": "load", "error": f"{type(e).__name__}: {e}"})
        return 0

    roots = {os.path.normcase(os.path.realpath(p)) for p in
             (sys.prefix, sys.base_prefix, sys.exec_prefix, os.path.dirname(np.__file__), os.path.dirname(pd.__file__))}

    def _allowed_read(path) -> bool:
        if isinstance(path, int):
            return False
        try:
            p = os.path.normcase(os.path.realpath(os.fsdecode(path)))
        except Exception:  # noqa: BLE001
            return False
        return any(p == r or p.startswith(r + os.sep) for r in roots)

    def hook(event, a):
        if event.startswith(BLOCKED_EVENTS):
            raise PermissionError(f"샌드박스: {event} 금지")
        if event in LIST_EVENTS and not _allowed_read(a[0] if a and a[0] is not None else "."):
            raise PermissionError(f"샌드박스: 폴더 보기 금지 ({a[0]!r})")
        if event == "open":
            path, mode = a[0], a[1] if len(a) > 1 else "r"
            writing = isinstance(mode, str) and any(c in mode for c in "wax+")
            if isinstance(mode, int):                       # os.open flags
                writing = bool(mode & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC))
            if writing or not _allowed_read(path):
                raise PermissionError(f"샌드박스: 파일 접근 금지 ({path!r})")
        if event == "import":
            top = (a[0] or "").split(".")[0]
            if top in {"socket", "subprocess", "ctypes", "_ctypes", "multiprocessing", "_socket", "ssl", "_ssl",
                       "urllib", "http", "asyncio", "_winapi", "winreg", "_posixsubprocess", "pty"}:
                raise PermissionError(f"샌드박스: import {a[0]} 금지")

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level != 0 or name.split(".")[0] not in ALLOWED_IMPORTS | RUNTIME_OK:
            raise ImportError(f"샌드박스: import {name} 금지 (pandas, numpy 만 허용)")
        return real_import(name, globals, locals, fromlist, level)

    safe = {k: v for k, v in vars(builtins).items() if k not in REMOVED_BUILTINS}
    safe["__import__"] = guarded_import
    env = {"__builtins__": safe, "__name__": "generated"}
    captured = io.StringIO()
    sys.addaudithook(hook)
    sys.stdout = sys.__stdout__ = captured                  # 생성 코드의 print 를 모은다 (결과 줄과 섞이지 않게)
    sys.stderr = sys.__stderr__ = captured
    try:
        builtins.exec(compiled, env)
        fn = env.get(args["func"])
        if not callable(fn):
            raise NameError(f"함수 {args['func']} 를 찾지 못했습니다.")
        res = fn(prices, args["start"], args["end"])
        orders = _norm_orders(res, pd)
        _emit(real_out, {"ok": True, "orders": orders, "stdout": captured.getvalue()[-2000:]})
    except BaseException as e:  # noqa: BLE001 — 생성 코드의 어떤 실패든 결과로 돌려준다
        tb = traceback.format_exception(type(e), e, e.__traceback__)
        tail = "".join(x for x in tb if "<generated>" in x or x is tb[-1])[-1500:]
        _emit(real_out, {"ok": False, "stage": "run", "error": f"{type(e).__name__}: {e}"[:500], "traceback": tail,
                         "stdout": captured.getvalue()[-2000:]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
