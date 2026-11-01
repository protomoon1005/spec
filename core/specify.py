"""LLM 명세화 (docs/demo-spec.md §B3·B4). 이 저장소에서 LLM 을 쓰는 유일한 단계.

흐름: 한국어 문장 → Ollama qwen3:8b (JSON Schema 구조화 출력, temperature 0, seed 고정, think off)
      → core.validate 4단계 → SpecifyResult.next_step 로 다음 화면 결정.

- 형식(1단계) 실패 시에만 오류를 알려주고 1회 재시도한다. 2~4단계 실패는 재시도하지 않고 그대로 보여준다.
- 캐시: (모델·옵션·시스템 프롬프트·스키마·입력 문장) 해시 → data/cache/specify/{hash}.json.
  같은 문장은 LLM 을 다시 부르지 않는다 (시연 안정성). 캐시 결과도 매번 다시 검증한다.
- provenance.input_text 는 LLM 이 아니라 코드가 실제 입력으로 채운다.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from core.dictionary import INDICATORS, OPERATORS, REJECT_CODES, load_expressions, load_tickers
from core.schema import PROVENANCE_PATHS, DemoSpec, build_json_schema
from core.validate import ValidationReport, validate

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache" / "specify"

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen3:8b"
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 16384}
THINK = False          # 2026-11-02 확인: think=false 가 2~4배 빠르고 판정 품질이 나쁘지 않음
KEEP_ALIVE = "30m"
TIMEOUT_S = 600

ChatFn = Callable[[list[dict], dict], dict]   # (messages, schema) → Ollama /api/chat 응답 JSON


class OllamaUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------- 기본값

DEFAULT_UNIVERSE = [{"ticker": "005930", "name": "삼성전자", "weight": 1.0}]
DEFAULT_BACKTEST = {"start": "2021-01-01", "end": "2025-12-31", "initial_cash": 10_000_000}
DEFAULT_COSTS = {"commission_bps": 1.5, "tax_profile": "kr_default"}


def _spec(verdict="ok", questions=(), reject_reason=None, universe=(), entry=None, exit=None,
          backtest=None, costs=None, mappings=(), defaults=()) -> dict:
    return {
        "schema_version": "demo-0.1", "verdict": verdict, "questions": list(questions),
        "reject_reason": reject_reason, "universe": list(universe), "entry": entry, "exit": exit,
        "backtest": backtest or DEFAULT_BACKTEST, "costs": costs or DEFAULT_COSTS,
        "provenance": {"input_text": "", "mappings": [{"phrase": p, "field": f} for p, f in mappings],
                       "defaults_applied": list(defaults)},
    }


def _exit(conditions=(), stop_loss=None, take_profit=None, max_hold_days=None, logic="any") -> dict:
    return {"logic": logic, "conditions": list(conditions), "stop_loss": stop_loss,
            "take_profit": take_profit, "max_hold_days": max_hold_days}


# ---------------------------------------------------------------- 예시 6개 (eval/sentences.jsonl 문장과 겹치지 않게)

EXAMPLES: list[tuple[str, dict]] = [
    ("POSCO홀딩스 10일선 뚫으면 사고 10일선 무너지면 팔아, 손절은 7%",
     _spec(universe=[{"ticker": "005490", "name": "POSCO홀딩스", "weight": 1.0}],
           entry={"logic": "all", "conditions": [{"left": "CLOSE", "op": "cross_above", "right": "SMA_10"}]},
           exit=_exit([{"left": "CLOSE", "op": "cross_below", "right": "SMA_10"}], stop_loss=0.07),
           mappings=[("POSCO홀딩스", "universe[0]"), ("10일선 뚫으면", "entry.conditions[0]"),
                     ("10일선 무너지면", "exit.conditions[0]"), ("손절은 7%", "exit.stop_loss")],
           defaults=["backtest", "costs"])),
    ("KODEX 200이랑 TIGER 200 반반, RSI 25 이하면 사고 30일 지나면 팔아. 2023년부터 2024년까지 5천만 원으로",
     _spec(universe=[{"ticker": "069500", "name": "KODEX 200", "weight": 0.5},
                     {"ticker": "102110", "name": "TIGER 200", "weight": 0.5}],
           entry={"logic": "all", "conditions": [{"left": "RSI_14", "op": "lt", "right": 25}]},
           exit=_exit(max_hold_days=30),
           backtest={"start": "2023-01-01", "end": "2024-12-31", "initial_cash": 50_000_000},
           mappings=[("KODEX 200이랑 TIGER 200 반반", "universe"), ("RSI 25 이하면", "entry.conditions[0]"),
                     ("30일 지나면 팔아", "exit.max_hold_days"), ("2023년부터 2024년까지 5천만 원으로", "backtest")],
           defaults=["costs"])),
    ("애플 RSI 30 아래면 사고 5일선 깨지면 팔아",
     _spec("clarify", questions=["애플은 종목 사전에 없습니다. 사전에 있는 종목 중 어떤 것으로 할까요? (예: 삼성전자, KODEX 200)"])),
    ("주가 좀 빠지면 사고 많이 오르면 팔아",
     _spec("clarify", questions=["얼마나 빠졌을 때 살까요? (예: RSI 30 아래, 종가가 20일선 아래로 내려갔을 때)",
                                 "얼마나 오르면 팔까요? (예: 매수가 대비 10% 상승)"])),
    ("외국인 순매수 들어오면 사고 순매도로 바뀌면 팔아",
     _spec("reject", reject_reason={"code": "UNSUPPORTED_CONCEPT",
                                    "detail": "투자자별 매매 동향(외국인 순매수)은 지표 사전에 없습니다."})),
    ("골든크로스 나오면 사서 계속 들고 있어",
     _spec("reject", reject_reason={"code": "NO_EXIT_RULE",
                                    "detail": "매도 조건·손절·익절·보유기간이 없어 실행할 수 없습니다. 언제 팔지 알려 주세요."})),
]


# ---------------------------------------------------------------- 시스템 프롬프트

def _j(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def build_system_prompt() -> str:
    ind = "\n".join(f"- {k}: {v['label']}" for k, v in INDICATORS.items())
    ops = "\n".join(f"- {k}: {v}" for k, v in OPERATORS.items())
    tickers = "\n".join(f"- {t.ticker} {t.name} ({t.asset_type}) 별칭: {', '.join(t.aliases)}"
                        for t in load_tickers().values())
    ex = load_expressions()
    rejects = "\n".join(f"- {k}: {v}" for k, v in REJECT_CODES.items())
    examples = "\n\n".join(f"입력: {text}\n출력: {_j(out)}" for text, out in EXAMPLES)
    return f"""너는 한국어 매매 전략 문장을 JSON 명세(DemoSpec demo-0.1)로 바꾸는 변환기다.
주어진 JSON Schema 에 맞는 JSON 하나만 출력한다. 아래 사전에 있는 값만 쓴다. 사전에 없으면 추측하지 말고 clarify 또는 reject 로 판정한다.

## 지표 (left, right, ref 에 쓰는 id)
{ind}

## 연산자
{ops}

## 방향 표현 규칙 (중요)
- '사건'(교차): "아래로 떨어지면/아래로 내려가면" = cross_below, "위로 올라가면/올라가면/돌파하면" = cross_above
- '상태'(비교): "아래면/아래일 때/이하면" = lt, "위면/위일 때/이상이면" = gt
- "이하"는 lt, "이상"은 gt 로 처리한다. clarify 하지 않는다.
- right 가 숫자 기준값이면 숫자로 쓴다 (예: "RSI 30 아래면" → {{"left":"RSI_14","op":"lt","right":30}}). 숫자 대신 지표 id 를 넣지 않는다.
- "{{n}}일선 깨지면/이탈하면" = CLOSE cross_below SMA_n (종가 기준). "{{n}}일선 돌파/뚫으면/올라타면" = CLOSE cross_above SMA_n.
- 배수 표현 {{"ref": 지표, "mul": 배수}} 는 VOLUME 비교에만 쓴다 ("거래량 터지면" → VOLUME gt {{"ref":"VOL_SMA_20","mul":2}}).

## 한국어 표현 사전 (data/expressions.json)
{_j({"direction_rules": ex["direction_rules"], "indicator_aliases": ex["indicator_aliases"], "phrases": ex["phrases"]})}

## 종목 사전 (이 20개만 허용. 없는 종목이면 clarify)
{tickers}

## 판정 (verdict)
- ok: 매수 조건과 매도 수단(매도 조건 또는 손절·익절·보유기간)이 모두 사전 값으로 표현된다.
- clarify: 기간·기준·종목이 모호하거나 해석이 여러 개다. questions 에 한국어 질문 1~3개 (예시 답 포함).
- reject: 미래 데이터 참조, 사전에 없는 개념, 실행 불가. reject_reason.code 와 한국어 detail.
reject 사유 코드:
{rejects}
- clarify/reject 이면 universe=[], entry=null, exit=null, provenance.mappings=[], defaults_applied=[] 로 둔다.
- ok 이면 questions=[], reject_reason=null.

## 수치 표현
- 손절·익절은 0~1 소수: "5% 떨어지면 손절" → stop_loss 0.05, "10% 오르면 팔아" → take_profit 0.1
- "20일 지나면/20일 보유" → max_hold_days 20 (거래일)
- 비중은 0~1 소수: "반반" → 두 종목 0.5씩 (종목이 2개가 아니면 clarify), "70%만" → 0.7
- 금액: "5천만 원" → 50000000. 기간: "2023년부터 2024년까지" → 2023-01-01 ~ 2024-12-31

## 기본값 (문장에 없을 때)
- 종목 언급이 없으면 universe={_j(DEFAULT_UNIVERSE)} 이고 defaults_applied 에 "universe"
- backtest={_j(DEFAULT_BACKTEST)} 이고 defaults_applied 에 "backtest"
- costs={_j(DEFAULT_COSTS)} 이고 defaults_applied 에 "costs"
- "골든크로스/데드크로스"에 기간이 없으면 SMA_5 와 SMA_20 을 쓰고 defaults_applied 에 그 조건 경로(예: "entry.conditions[0]")
- 매수 조건 logic 은 보통 "all", 매도 조건 logic 은 보통 "any"

## 출처 (provenance)
- mappings: 문장의 구절(phrase, 원문 그대로)과 그 구절이 만든 명세 경로(field). field 는 다음 중 하나:
  {", ".join(PROVENANCE_PATHS)}
- input_text 는 빈 문자열로 둬도 된다 (코드가 채운다).

## 예시
{examples}
"""


# ---------------------------------------------------------------- Ollama 호출

def _ollama_chat(messages: list[dict], schema: dict) -> dict:
    body = {"model": MODEL, "messages": messages, "format": schema, "stream": False,
            "think": THINK, "options": OPTIONS, "keep_alive": KEEP_ALIVE}
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.load(r)
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise OllamaUnavailable(f"Ollama({OLLAMA_URL}) 호출 실패: {e}") from e


def ollama_status(timeout: float = 2.0) -> tuple[bool, str]:
    """(사용 가능 여부, 안내 메시지). UI 첫 화면 배너용."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=timeout) as r:
            names = [m["name"] for m in json.load(r).get("models", [])]
    except Exception as e:  # noqa: BLE001 — 어떤 실패든 '사용 불가'로 안내
        return False, f"Ollama 에 연결할 수 없습니다 ({OLLAMA_URL}). `ollama serve` 로 켜 주세요. ({e})"
    if MODEL not in names:
        return False, f"Ollama 에 {MODEL} 모델이 없습니다. `ollama pull {MODEL}` 이 필요합니다."
    return True, f"{MODEL} 사용 가능"


# ---------------------------------------------------------------- 결과

@dataclass
class SpecifyResult:
    input_text: str
    raw: Optional[str]                      # LLM 마지막 출력 원문
    report: Optional[ValidationReport]
    elapsed_s: float                        # LLM 호출 시간 합계 (캐시 적중이면 원래 호출 시간)
    attempts: int
    cached: bool
    cache_key: str
    meta: dict = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def spec(self) -> Optional[DemoSpec]:
        return self.report.spec if self.report else None

    @property
    def verdict(self) -> Optional[str]:
        return self.spec.verdict if self.spec else None

    @property
    def next_step(self) -> str:
        """confirm(③ 검증·확인으로) | clarify(질문) | reject(사유 표시) | invalid(검증 실패) | error"""
        if self.error or self.report is None:
            return "error"
        r = self.report
        if r.ok:
            return "confirm"
        if r.stages[0].status != "passed" or r.stages[1].status != "passed" or self.spec.verdict == "ok":
            return "invalid"
        # clarify/reject 는 3단계에서 'ok 판정만 백테스트' 로 멈추는 것이 정상. 그 외 문제가 있으면 invalid.
        if any(i.field != "verdict" for i in r.stages[2].issues):
            return "invalid"
        return self.spec.verdict


# ---------------------------------------------------------------- 캐시

def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def cache_key(text: str, system_prompt: str, schema: dict) -> str:
    return _sha(json.dumps({"model": MODEL, "options": OPTIONS, "think": THINK,
                            "system": _sha(system_prompt), "schema": _sha(_j(schema)),
                            "input": text.strip()}, sort_keys=True))


def _cache_read(key: str, cache_dir: Path) -> Optional[dict]:
    p = cache_dir / f"{key}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _cache_write(key: str, cache_dir: Path, entry: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 명세화

def _check(text: str, raw: str) -> ValidationReport:
    """LLM 출력 → (input_text 를 실제 입력으로 채운 뒤) 4단계 검증."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return validate(raw)                     # 1단계에서 JSON 오류로 실패
    if isinstance(data, dict) and isinstance(data.get("provenance"), dict):
        data["provenance"]["input_text"] = text
    return validate(data)


def specify(text: str, *, use_cache: bool = True, chat_fn: Optional[ChatFn] = None,
            cache_dir: Path | str = CACHE_DIR) -> SpecifyResult:
    """한국어 전략 문장 → 검증된 명세 결과."""
    text = text.strip()
    system = build_system_prompt()
    schema = build_json_schema()
    key = cache_key(text, system, schema)
    cache_dir = Path(cache_dir)

    if use_cache and (hit := _cache_read(key, cache_dir)):
        return SpecifyResult(text, hit["raw"], _check(text, hit["raw"]), hit["elapsed_s"], hit["attempts"],
                             True, key, hit.get("meta", {}))

    chat = chat_fn or _ollama_chat
    messages = [{"role": "system", "content": system}, {"role": "user", "content": text}]
    elapsed, attempts, raw, report, meta = 0.0, 0, None, None, {"calls": []}
    try:
        for attempt in range(2):                 # 최초 1회 + 형식 실패 시 재시도 1회
            attempts += 1
            t0 = time.perf_counter()
            resp = chat(messages, schema)
            dt = time.perf_counter() - t0
            elapsed += dt
            raw = resp["message"]["content"]
            meta["calls"].append({k: resp.get(k) for k in
                                  ("prompt_eval_count", "eval_count", "total_duration", "load_duration")}
                                 | {"wall_s": round(dt, 3)})
            report = _check(text, raw)
            if report.stages[0].status == "passed" or attempt == 1:
                break
            errors = "\n".join(str(i) for i in report.stages[0].issues)
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": f"위 출력이 스키마 형식 검증에 실패했다:\n{errors}\n"
                                                     f"같은 문장을 스키마에 맞게 다시 출력하라."}]
    except OllamaUnavailable as e:
        return SpecifyResult(text, raw, report, elapsed, attempts, False, key, meta, error=str(e))

    prompt_tokens = meta["calls"][0].get("prompt_eval_count")
    if prompt_tokens and prompt_tokens >= OPTIONS["num_ctx"]:
        meta["warning"] = f"프롬프트({prompt_tokens} 토큰)가 num_ctx({OPTIONS['num_ctx']})에 닿아 잘렸을 수 있습니다."
    result = SpecifyResult(text, raw, report, elapsed, attempts, False, key, meta)
    if use_cache:
        _cache_write(key, cache_dir, {"input": text, "raw": raw, "elapsed_s": elapsed, "attempts": attempts,
                                      "meta": meta, "model": MODEL,
                                      "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return result


def compose_refined_input(original: str, questions: Sequence[str], answers: Sequence[str]) -> str:
    if len(answers) != len(questions):
        raise ValueError(f"질문 {len(questions)}개에 답변 {len(answers)}개 — 개수가 같아야 합니다.")
    qa = "\n".join(f"- 질문: {q}\n  답변: {a.strip()}" for q, a in zip(questions, answers))
    return f"{original.strip()}\n\n[추가 질문에 대한 사용자 답변]\n{qa}"


def refine(spec: DemoSpec, answers: Sequence[str], **kwargs) -> SpecifyResult:
    """clarify 명세 + 사용자 답변 → 원문·질문·답을 합친 문장으로 다시 명세화."""
    if spec.verdict != "clarify" or not spec.questions:
        raise ValueError("clarify 판정이고 질문이 있는 명세만 refine 할 수 있습니다.")
    if spec.provenance is None or not spec.provenance.input_text:
        raise ValueError("명세에 원래 입력 문장(provenance.input_text)이 없습니다.")
    return specify(compose_refined_input(spec.provenance.input_text, spec.questions, answers), **kwargs)
