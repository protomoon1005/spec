"""비교 대상: LLM 이 전략 문장을 파이썬 함수로 직접 짠다 (docs/strategy-spec-v0.2.md "비교 대상(LLM 코드 직접 생성)").

같은 모델·같은 설정 (core.slot_fill 의 MODEL·OPTIONS·THINK 그대로: qwen3:8b, temperature 0, seed 42, think false,
num_ctx 16384, num_predict 2048). 구조화 출력(format)은 쓰지 않는다 — 코드를 자유롭게 쓰게 한다.

프롬프트에는 데이터 형식과 출력 형식만 적는다. 우리 사전(지표·연산자·표현 표·종목 별칭)·규칙·판정은 넣지 않는다.
  - 종목은 데이터 키(종목코드)와 정식 이름만 알려 준다 — 데이터에 무엇이 있는지는 데이터 형식이다.
  - "판단한 날 종가 → 다음 거래일 시가 체결"은 출력의 date 가 무슨 뜻인지(출력 형식)라 적는다.
  - 실행 환경(Python·pandas·numpy 판)을 적는다. 판을 모르면 pandas 3 에서 없어진 문법(Series[정수] 위치 접근)을
    써서 실패한다 (2026-11-02 관찰). 판만 알려 주고 어떻게 짜라는 힌트는 주지 않는다.
  - 공정하게: 문장이 모호하면 NEED_CLARIFICATION, 구현할 수 없으면 CANNOT_IMPLEMENT 로 답할 수 있는 길을 열어 둔다.
    (이 길이 없으면 '되묻기·거절 문장에서 그냥 코드를 만든 건수'가 구조적으로 100% 가 되어 비교가 공정하지 않다.)
캐시: (모델·옵션·프롬프트·문장) 해시 → data/cache/baseline/{hash}.json. 반복 일치율을 잴 때는 캐시를 끈다.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from core.dictionary import load_tickers
from core.slot_fill import MODEL, OPTIONS, THINK, OllamaUnavailable, _ollama_chat

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache" / "baseline"
FUNC_NAME = "strategy"
INDEX_KEYS = {"KS11": "코스피 지수", "KS200": "코스피200 지수", "KQ11": "코스닥 지수"}
US_KEYS = {"VIX": "미국 VIX 지수"}

ChatFn = Callable[[list[dict], Optional[dict]], dict]   # (messages, format) → Ollama /api/chat 응답 JSON


def tradable() -> list[tuple[str, str, str]]:
    """(종목코드, 이름, 주식/ETF) — 데이터 키. 별칭(aliases)은 우리 사전이라 넣지 않는다."""
    return [(t.ticker, t.name, "ETF" if t.kind == "ETF" else "주식") for t in load_tickers().values()]


def data_keys() -> list[str]:
    return [t for t, _, _ in tradable()] + list(INDEX_KEYS) + list(US_KEYS)


def build_prompt() -> str:
    table = "\n".join(f"  - \"{t}\": {n} ({k})" for t, n, k in tradable())
    idx = "\n".join(f"  - \"{k}\": {v}" for k, v in INDEX_KEYS.items())
    return f"""너는 파이썬 개발자다. 사용자가 한국어로 말한 매매 전략을 백테스트용 파이썬 함수 하나로 구현하라.

## 함수
def {FUNC_NAME}(prices: dict[str, pd.DataFrame], start: str, end: str) -> list[dict]:

## 입력
- prices: 키 → 일봉 DataFrame
  사고팔 수 있는 종목 (키는 6자리 종목코드):
{table}
  지수 (참고용, 사고팔 수 없음):
{idx}
  - "VIX": 미국 VIX 지수 (사고팔 수 없음). 인덱스가 미국 거래일 날짜다. 미국 장 마감은 한국 시간으로 다음 날 아침이다.
- 각 DataFrame: 인덱스는 거래일 DatetimeIndex(오름차순), 열은 "Open", "High", "Low", "Close", "Volume" (수정주가).
  end 까지의 데이터만 들어 있고, start 이전 과거 데이터도 들어 있다 (지표 계산용). 종목마다 첫 날짜가 다를 수 있다.
- start, end: 'YYYY-MM-DD' 문자열. 백테스트 기간이다. 문장에 기간이 따로 없으면 이 기간을 쓴다.

## 출력
주문 목록 (dict 의 list, 날짜 오름차순). 주문 하나는:
- "date": 'YYYY-MM-DD' — 주문을 판단한 거래일. 그날 종가까지의 데이터로 판단하고, 다음 거래일 시가에 체결된다.
  기간 첫 거래일 시가에 체결하려면 그 전 거래일 날짜로 적는다.
- "ticker": 사고팔 수 있는 종목의 종목코드
- "side": "buy" 또는 "sell"
- "weight": 체결 뒤 그 종목이 총자산에서 차지할 목표 비중 (0~1). 전부 팔면 0.
시작할 때는 전부 현금이다. 수수료·세금·체결 오차는 실행하는 쪽이 계산한다. 1주 단위로 체결되고 남는 돈은 현금으로 둔다.

## 답하는 법
- 실행 환경: Python {sys.version_info.major}.{sys.version_info.minor}, pandas {pd.__version__}, numpy {np.__version__}.
- import 는 pandas, numpy 만 쓸 수 있다. 파일·네트워크는 쓸 수 없다.
- 함수 {FUNC_NAME}(필요하면 보조 함수 포함)를 ```python 코드 블록 하나로만 답하라. 설명은 쓰지 마라.
- 문장의 뜻이 하나로 정해지지 않아 사용자에게 물어봐야 하면, 코드 대신 `NEED_CLARIFICATION: <질문>` 한 줄만 답하라.
- 위 데이터로 구현할 수 없는 전략이면, 코드 대신 `CANNOT_IMPLEMENT: <이유>` 한 줄만 답하라.
"""


@dataclass
class CodegenResult:
    text: str
    kind: str                       # code | clarify | reject | error
    code: Optional[str]
    message: str                    # clarify/reject 의 질문·이유, error 의 오류
    raw: Optional[str]
    elapsed_s: float
    cached: bool
    meta: dict = field(default_factory=dict)

    @property
    def code_hash(self) -> Optional[str]:
        return hashlib.sha256(self.code.encode("utf-8")).hexdigest() if self.code else None


_BLOCK = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)
_MARK = re.compile(r"^\s*`?(NEED_CLARIFICATION|CANNOT_IMPLEMENT)\s*[:：]\s*(.*)$", re.M)


def parse(raw: str) -> tuple[str, Optional[str], str]:
    """응답 → (kind, code, message). 코드 블록이 있으면 코드가 이긴다 (실제로 코드를 내놓았으므로)."""
    raw = raw or ""
    blocks = [b for b in _BLOCK.findall(raw) if f"def {FUNC_NAME}" in b]
    if blocks:
        return "code", blocks[0].strip() + "\n", ""
    if m := _MARK.search(raw):
        return ("clarify" if m.group(1) == "NEED_CLARIFICATION" else "reject"), None, m.group(2).strip().strip("`")
    if f"def {FUNC_NAME}" in raw:                       # 코드 블록 없이 코드만 냈거나, 출력 상한에 걸려 블록이 안 닫힘
        body = re.sub(r"^.*?```(?:python|py)?[ \t]*\n", "", raw, count=1, flags=re.S) if "```" in raw else raw
        return "code", body.strip() + "\n", ""
    return "error", None, "응답에서 함수 코드도, 되묻기·거절 표시도 찾지 못했습니다."


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def cache_key(text: str, prompt: str) -> str:
    return _sha(json.dumps({"model": MODEL, "options": OPTIONS, "think": THINK, "system": _sha(prompt),
                            "input": text.strip(), "kind": "baseline-codegen"}, sort_keys=True))


def _chat(messages: list[dict], fmt: Optional[dict]) -> dict:
    return _ollama_chat(messages, fmt)   # format=None → 자유 텍스트


def generate(text: str, *, use_cache: bool = True, chat_fn: Optional[ChatFn] = None,
             cache_dir: Path | str = CACHE_DIR) -> CodegenResult:
    text = text.strip()
    prompt = build_prompt()
    key = cache_key(text, prompt)
    path = Path(cache_dir) / f"{key}.json"
    if use_cache and path.exists():
        hit = json.loads(path.read_text(encoding="utf-8"))
        kind, code, msg = parse(hit["raw"])
        return CodegenResult(text, kind, code, msg, hit["raw"], hit["elapsed_s"], True, hit.get("meta", {}))
    chat = chat_fn or _chat
    t0 = time.perf_counter()
    try:
        resp = chat([{"role": "system", "content": prompt}, {"role": "user", "content": text}], None)
    except OllamaUnavailable as e:
        return CodegenResult(text, "error", None, str(e), None, time.perf_counter() - t0, False)
    dt = time.perf_counter() - t0
    raw = resp["message"]["content"]
    meta = {k: resp.get(k) for k in ("prompt_eval_count", "eval_count", "total_duration", "done_reason")}
    kind, code, msg = parse(raw)
    if use_cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"input": text, "raw": raw, "elapsed_s": dt, "meta": meta, "model": MODEL,
                                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    return CodegenResult(text, kind, code, msg, raw, dt, False, meta)
