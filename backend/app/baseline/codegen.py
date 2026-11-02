"""비교 대상: LLM 이 한국어 전략 문장을 파이썬 함수로 직접 짠다.

설계 문서 "비교 대상", docs/v0.2-plan.md 9단계.

demo/baseline/codegen.py 를 옮겼다(demo/ 는 9단계 d 에서 지웠다).
같은 모델 · 같은 설정(qwen3:8b, 온도 0, 시드 42, thinking 끔)이고
구조화 출력(format)은 쓰지 않는다. 코드를 자유롭게 쓰게 한다.

프롬프트에는 데이터 형식과 출력 형식만 적는다. 우리 사전(표현 표 · 거절 사전) · 규칙 · 판정은 넣지 않는다.
  - 종목: 문장에 나온 종목만 준다(2026-11-02 결정, A안). 종목 이름 찾기는 우리 것(app/m1/spans.py)을 같이 써서
    두 방식이 같은 종목을 본다. 그래서 비교는 "문장을 전략으로 해석하고 실행하는 능력" 에 집중된다.
    이름 해석 실수는 비교 대상 쪽에서 측정되지 않는다(한계로 밝힌다)
  - 지수 · VIX 는 데모 DB 에 없어 주지 않는다. 그런 문장은 구현할 수 없다고 답할 길을 열어 둔다
  - "판단한 날 종가 → 다음 거래일 시가 체결" 은 출력의 date 뜻이라 적는다. 실행 환경
    (Python · pandas · numpy 판)도 적는다. 판을 모르면 없어진 문법을 써서 실패한다(데모 관찰).
    짜는 법 힌트는 주지 않는다
  - 문장이 모호하면 NEED_CLARIFICATION, 구현할 수 없으면 CANNOT_IMPLEMENT 로 답할 수 있다. 이 길이 없으면
    되묻기 · 거절 문장에서도 코드를 낼 수밖에 없어 비교가 공정하지 않다
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

FUNC_NAME = "strategy"
OPTIONS = {"num_ctx": 8192, "num_predict": 2048}


@dataclass(frozen=True)
class CodegenResult:
    kind: str  # code · clarify · reject · error
    code: str | None
    message: str
    raw: str | None
    elapsed_s: float
    cached: bool


def build_prompt(tickers: list[tuple[str, str, str]], start: str, end: str) -> str:
    table = "\n".join(f'  - "{t}": {name} ({kind})' for t, name, kind in tickers) or "  (없음)"
    return f"""너는 파이썬 개발자다. 사용자가 한국어로 말한 매매 전략을 백테스트용 파이썬 함수 하나로
구현하라.

## 함수
def {FUNC_NAME}(prices: dict[str, pd.DataFrame], start: str, end: str) -> list[dict]:

## 입력
- prices: 종목코드(6자리) → 일봉 DataFrame. 들어 있는 종목:
{table}
- 각 DataFrame: 인덱스는 거래일 DatetimeIndex(오름차순), 열은 "Open", "High", "Low", "Close", "Volume"
  (수정주가).
  end 까지의 데이터만 들어 있고, start 이전 과거 데이터도 들어 있다(지표 계산용).
  종목마다 첫 날짜가 다를 수 있다.
  지수(코스피, 코스닥)와 VIX 데이터는 없다.
- start, end: 'YYYY-MM-DD' 문자열. 백테스트 기간이다(기본 {start} ~ {end}).
  문장에 기간이 따로 있으면 그 기간을 쓴다.

## 출력
주문 목록(dict 의 list, 날짜 오름차순). 주문 하나는:
- "date": 'YYYY-MM-DD' — 주문을 판단한 거래일. 그날 종가까지의 데이터로 판단하고, 다음 거래일 시가에 체결된다.
  기간 첫 거래일 시가에 체결하려면 그 전 거래일 날짜로 적는다.
- "ticker": 종목코드
- "side": "buy" 또는 "sell"
- "weight": 체결 뒤 그 종목이 총자산에서 차지할 목표 비중(0~1). 전부 팔면 0.
시작할 때는 전부 현금이다. 수수료 · 세금 · 체결 오차는 실행하는 쪽이 계산한다.
1주 단위로 체결되고 남는 돈은 현금으로 둔다.

## 답하는 법
- 실행 환경: Python {sys.version_info.major}.{sys.version_info.minor}, pandas {pd.__version__},
  numpy {np.__version__}.
- import 는 pandas, numpy 만 쓸 수 있다. 파일 · 네트워크는 쓸 수 없다.
- 함수 {FUNC_NAME}(필요하면 보조 함수 포함)를 ```python 코드 블록 하나로만 답하라. 설명은 쓰지 마라.
- 문장의 뜻이 하나로 정해지지 않아 사용자에게 물어봐야 하면,
  코드 대신 `NEED_CLARIFICATION: <질문>` 한 줄만 답하라.
- 위 데이터로 구현할 수 없는 전략이면, 코드 대신 `CANNOT_IMPLEMENT: <이유>` 한 줄만 답하라.
"""


_BLOCK = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)
_MARK = re.compile(r"^\s*`?(NEED_CLARIFICATION|CANNOT_IMPLEMENT)\s*[:：]\s*(.*)$", re.M)


def parse(raw: str) -> tuple[str, str | None, str]:
    """응답에서 (kind, code, message). 코드 블록이 있으면 코드가 이긴다(실제로 코드를 내놓았으므로)."""
    raw = raw or ""
    blocks = [b for b in _BLOCK.findall(raw) if f"def {FUNC_NAME}" in b]
    if blocks:
        return "code", blocks[0].strip() + "\n", ""
    if m := _MARK.search(raw):
        return (
            ("clarify" if m.group(1) == "NEED_CLARIFICATION" else "reject"),
            None,
            m.group(2).strip().strip("`"),
        )
    if f"def {FUNC_NAME}" in raw:  # 코드 블록 없이 코드만 냈거나 출력 상한에 걸려 블록이 안 닫혔다
        body = re.sub(r"^.*?```(?:python|py)?[ \t]*\n", "", raw, count=1, flags=re.S) if "```" in raw else raw
        return "code", body.strip() + "\n", ""
    return "error", None, "응답에서 함수 코드도, 되묻기 · 거절 표시도 찾지 못했습니다."


def generate(text: str, prompt: str, client, *, cache_dir: Path | None = None) -> CodegenResult:
    key = hashlib.sha256(json.dumps([prompt, text.strip(), OPTIONS], ensure_ascii=False).encode()).hexdigest()
    path = cache_dir / f"{key}.json" if cache_dir else None
    if path is not None and path.exists():
        hit = json.loads(path.read_text(encoding="utf-8"))
        kind, code, message = parse(hit["raw"])
        return CodegenResult(kind, code, message, hit["raw"], hit["elapsed_s"], True)
    started = time.perf_counter()
    try:
        raw = client.generate_text(text.strip(), system=prompt, options=OPTIONS)
    except Exception as exc:  # 모델 호출 실패도 오답으로 센다
        return CodegenResult(
            "error", None, f"{type(exc).__name__}: {exc}", None, time.perf_counter() - started, False
        )
    elapsed = time.perf_counter() - started
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"raw": raw, "elapsed_s": elapsed}, ensure_ascii=False), encoding="utf-8")
    kind, code, message = parse(raw)
    return CodegenResult(kind, code, message, raw, elapsed, False)
