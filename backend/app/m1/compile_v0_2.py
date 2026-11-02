# 전략서 v0.2 명세화 입구: 처음 요청(start)과 되묻기 답(answer).
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 같은 폴더의 다른 파일과 같다(as_of 가드 사정권).
#
# 흐름(설계 문서 "판단하는 순서", 그림 "입력 판정 흐름")
#   1. 조각 찾기(spans)
#   2. AI 전 판정(decide.precheck): 거절 사전, 투자 무관
#   3. AI 짝짓기와 근거 검사, 재시도 1회(pairing.pair)
#   4. 값 바꾸기와 조립(assemble)
#   5. 검사 1~3단계(m2/checks_v0_2.py)와 판정(decide.decide).
#      검사의 막힘은 거절 거리, 되묻기는 되물을 거리로 합친다
#   6. 바로 진행이면 draft 로 저장하고 검사 4단계(근거 검사) 기록을 남긴다. 되묻기면 대화 상태를 남긴다
# 답(answer)은 AI 를 다시 부르지 않는다. 대화 상태에 남긴 AI 응답 원본으로 짝짓기를 되살리고,
# 답을 overrides 에 더해 조립부터 다시 한다(설계 문서 "되묻기에 답하면").
#
# 대화 상태는 Redis 에 30분 둔다(m1/session.py 와 같은 이유: 묻는 쪽과 답하는 쪽 프로세스가 다르다).
# 저장소를 바꿔 끼울 수 있게 store 를 받는다. 테스트는 메모리 저장소를 쓴다.
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from app.m1 import assemble, decide, pairing, spans
from app.m1.assemble import Ask, Reject
from app.m1.decide import Choice, Question
from app.m2 import checks_v0_2
from app.repositories import etf_master, price_daily, specs

KEY_PREFIX = "m1:v0.2:"
TTL_SECONDS = 30 * 60


@dataclass
class CompileResult:
    status: str  # ok · ask · reject
    session_id: str
    strategy_id: str | None = None
    spec: dict | None = None
    questions: list[dict] = field(default_factory=list)
    round: int = 0
    remaining_total: int = 0
    rejection: dict | None = None
    retried: bool = False
    checks: list[dict] = field(default_factory=list)

    def public(self) -> dict:
        # 설계 문서 "판정 결과 응답 모양". 설명(explanation)은 7단계에서 더한다
        if self.status == "ok":
            return {
                "status": "ok",
                "session_id": self.session_id,
                "strategy_id": self.strategy_id,
                "spec": self.spec,
                "checks": self.checks,
            }
        if self.status == "ask":
            return {
                "status": "ask",
                "session_id": self.session_id,
                "round": self.round,
                "remaining_total": self.remaining_total,
                "questions": self.questions,
            }
        return {"status": "reject", "session_id": self.session_id, **(self.rejection or {})}


# ── 사전과 종목 색인: 프로세스마다 한 번 만든다 ───────────────────────
# 종목 원장을 다시 적재하면 프로세스를 다시 띄워야 새 이름이 잡힌다.


@lru_cache(maxsize=1)
def dictionary() -> spans.Dictionary:
    return spans.load_dictionary()


@lru_cache(maxsize=1)
def instrument_index() -> spans.InstrumentIndex:
    records = [(r.ticker, r.name) for r in etf_master.list_all(include_leveraged=True)]
    return spans.InstrumentIndex(records, dictionary())


def find(text: str) -> spans.SpanResult:
    return spans.find_spans(text, dictionary(), instrument_index())


def name_lookup(fragment: str) -> list[tuple[str, str]]:
    return [(r.ticker, r.name) for r in etf_master.find_by_name(fragment, limit=4)]


# ── 입구 ─────────────────────────────────────────────────────────────


def start(user_id: int, text: str, client, *, store=None) -> CompileResult:
    store = store or RedisStore()
    session_id = uuid.uuid4().hex
    result = find(text)
    early = decide.precheck(result)
    if early is not None:
        return CompileResult("reject", session_id, rejection=early.public())
    outcome = pairing.pair(result, client)
    state = {
        "session_id": session_id,
        "user_id": user_id,
        "text": text,
        "raw": outcome.raw[-1],
        "retried": outcome.retried,
        "first_issues": [asdict(i) for i in outcome.first_issues],
        "overrides": {},
        "rounds": 0,
        "questions": [],
    }
    return _continue(state, store)


def answer(session_id: str, answers: list[dict], *, store=None) -> CompileResult:
    store = store or RedisStore()
    state = store.load(session_id)
    if state is None:
        raise LookupError("대화가 만료됐어요. 처음부터 다시 요청해 주세요.")
    questions = [_question_from(q) for q in state["questions"]]
    added = decide.overrides_from_answers(
        questions, answers, read_text=lambda ask, text: decide.read_answer_text(ask, text, find)
    )
    state["overrides"].update(added)
    return _continue(state, store)


@dataclass
class Judgement:
    built: assemble.Assembly
    findings: list
    decision: decide.Decision


def judge(
    result: spans.SpanResult, paired: pairing.Pairing, *, overrides: dict, rounds_asked: int
) -> Judgement:
    # 조립, 검사 1~3단계, 판정. AI 를 부르지 않는다(scripts/measure_pipeline.py 도 쓴다)
    issues = tuple(pairing.evidence_issues(paired, result))
    built = assemble.assemble(result, paired, unresolved_issues=issues, overrides=overrides)
    findings = run_checks(built)
    extra_asks, extra_rejects = [], []
    for f in findings:
        position = built.positions.get(f.path.split(".")[0], len(result.text))
        if f.kind == "block":
            extra_rejects.append(Reject(f.code, f.message, position, f.stage))
        elif f.kind == "ask" and overrides.get(f"{f.code}:{f.path}") is None:
            extra_asks.append(Ask(f.code, f.path, position, dict(f.context)))
    decision = decide.decide(
        built,
        rounds_asked=rounds_asked,
        extra_asks=extra_asks,
        extra_rejects=extra_rejects,
        name_lookup=name_lookup,
    )
    return Judgement(built, findings, decision)


def run_checks(built: assemble.Assembly) -> list:
    findings = []
    if not built.asks:
        findings += checks_v0_2.check_shape(built.spec_dict)
    findings += checks_v0_2.check_existence(
        built.spec_dict,
        known_tickers=lambda tickers: set(etf_master.get_by_tickers(tickers)),
        coverage=lambda tickers, start, end: price_daily.get_price_coverage(tickers, start=start, as_of=end),
        first_trade_day=_first_trade_day,
        latest_trade_day=price_daily.get_latest_trade_date,
    )
    findings += checks_v0_2.check_logic(built.spec_dict)
    return findings


def _first_trade_day(start, end):
    days = price_daily.get_trade_dates(start=start, as_of=end)
    return days[0] if days else None


def _continue(state: dict, store) -> CompileResult:
    result = find(state["text"])
    paired = pairing.normalize(pairing.parse(state["raw"], result))
    judged = judge(result, paired, overrides=state["overrides"], rounds_asked=state["rounds"])
    built, decision = judged.built, judged.decision
    session_id = state["session_id"]

    if decision.status == "reject":
        store.drop(session_id)
        return CompileResult(
            "reject", session_id, rejection=decision.rejection.public(), retried=state["retried"]
        )
    if decision.status == "ask":
        state["rounds"] = decision.round
        state["questions"] = [_question_to(q) for q in decision.questions]
        store.save(session_id, state)
        return CompileResult(
            "ask",
            session_id,
            questions=[q.public() for q in decision.questions],
            round=decision.round,
            remaining_total=len(decision.all_asks),
            retried=state["retried"],
        )

    spec = built.spec
    spec_json = spec.model_dump(mode="json")
    tickers = spec.universe.held_tickers() or []
    weights = spec.weights if isinstance(spec.weights, dict) else {t: 1 / len(tickers) for t in tickers}
    stages = checks_v0_2.stage_results(judged.findings)
    evidence = _evidence_log(state)
    logs = [
        {"stage": s["stage"], "passed": s["result"] in ("pass", "warn"), "violations": s["items"]}
        for s in stages
    ] + [evidence]
    saved = specs.insert_strategy_v0_2(
        strategy_id=specs.new_strategy_id(),
        user_id=state["user_id"],
        name=_name(tickers, spec.is_rules_strategy),
        input_prompt=state["text"],
        spec=spec_json,
        asset_weights=[(t, weights.get(t)) for t in tickers],
        logs=logs,
    )
    store.drop(session_id)
    checks = stages + [
        {
            "stage": 4,
            "name": "근거",
            "result": "warn" if evidence["violations"] else "pass",
            "items": evidence["violations"],
        }
    ]
    return CompileResult(
        "ok",
        session_id,
        strategy_id=saved.strategy_id,
        spec=spec_json,
        retried=state["retried"],
        checks=checks,
    )


def _name(tickers: list[str], rules: bool) -> str:
    names = {t: r.name for t, r in etf_master.get_by_tickers(tickers).items()}
    head = names.get(tickers[0], tickers[0]) if tickers else "종목 없음"
    more = f" 외 {len(tickers) - 1}" if len(tickers) > 1 else ""
    return f"{head}{more} · {'매매 조건형' if rules else '자산 배분형'}"


def _evidence_log(state: dict) -> dict:
    # 검사 4단계(근거 검사) 기록. 바로 진행까지 왔으면 통과다.
    # 처음 짝짓기에서 걸려 다시 짝지었거나, 사용자가 '무시하기' 를 고른 조각은 경고(warn)로 남긴다
    violations = (
        [{"kind": "warn", "code": "retried", **issue} for issue in state["first_issues"]]
        if state["retried"]
        else []
    )
    for key, value in state["overrides"].items():
        if key.startswith(("evidence_unused:", "ambiguous_quote:")) and value == "ignore":
            violations.append({"kind": "warn", "code": "ignored_by_user", "key": key})
    return {"stage": 4, "passed": True, "violations": violations}


# ── 대화 상태 ────────────────────────────────────────────────────────


def _question_to(q: Question) -> dict:
    return {
        "id": q.id,
        "text": q.text,
        "allow_text": q.allow_text,
        "ask": {
            "kind": q.ask.kind,
            "path": q.ask.path,
            "position": q.ask.position,
            "context": dict(q.ask.context),
        },
        "choices": [{"id": c.id, "label": c.label, "value": c.value} for c in q.choices],
    }


def _question_from(data: dict) -> Question:
    ask = Ask(**data["ask"])
    choices = tuple(Choice(c["id"], c["label"], c["value"]) for c in data["choices"])
    return Question(data["id"], ask, data["text"], choices, data["allow_text"])


class RedisStore:
    def save(self, session_id: str, state: dict) -> None:
        _redis().set(KEY_PREFIX + session_id, json.dumps(state, ensure_ascii=False), ex=TTL_SECONDS)

    def load(self, session_id: str) -> dict | None:
        raw = _redis().get(KEY_PREFIX + session_id)
        return None if raw is None else json.loads(raw)

    def drop(self, session_id: str) -> None:
        _redis().delete(KEY_PREFIX + session_id)


class MemoryStore:
    # 테스트용. JSON 으로 한 번 오가게 해서 Redis 와 같은 모양(튜플이 목록이 되는 것까지)을 만든다
    def __init__(self):
        self.data: dict[str, str] = {}

    def save(self, session_id: str, state: dict) -> None:
        self.data[session_id] = json.dumps(state, ensure_ascii=False)

    def load(self, session_id: str) -> dict | None:
        raw = self.data.get(session_id)
        return None if raw is None else json.loads(raw)

    def drop(self, session_id: str) -> None:
        self.data.pop(session_id, None)


def _redis():
    import redis

    from app.core.config import get_settings

    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
