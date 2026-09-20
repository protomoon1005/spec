# 전략서 컴파일 파이프라인 — 되묻기 포함 (M1 6단계).
#
# 설명을 주석에 두는 이유는 같은 폴더의 다른 파일과 같다 — as_of 가드 사정권이다.
#
# ── 단계 여섯 ────────────────────────────────────────────────────────
#   1 성향 확인    확정된 성향이 없으면 시작할 수 없다
#   2 의도 파악    자연어 -> 우리가 아는 낱말
#   3 되묻기 판정  모호하면 여기서 멈추고 묻는다        ← 되돌아오는 자리
#   4 후보 선정    담을 수 있는 종목과 비중 범위
#   5 스키마·생성  좁힌 스키마로 LLM 호출
#   6 후처리·저장  파싱, 검사, draft 로 저장
#
# ── 되묻고 나면 처음부터 다시 돈다 ───────────────────────────────────
# 중간 상태를 되살려 3번부터 이어가지 않는다. 사용자의 답을 처음 요청 뒤에 이어 붙이고
# 1번부터 다시 돈다. 단순하고, 사용자가 앞 내용을 뒤집는 답을 해도("역시 채권 말고
# 주식으로") 그대로 반영된다. 비용은 의도 추출 한 번을 더 부르는 것뿐이다.
#
# ── 상태 머신 라이브러리를 쓰지 않았다 ───────────────────────────────
# 흐름이 한 줄이고 되돌아오는 자리가 하나뿐이라, 함수 여섯 개로 충분하다.
# 단계를 함수로 갈라 뒀으니 나중에 그래프로 감싸는 것은 얇은 껍데기 하나다.
from __future__ import annotations

from dataclasses import dataclass

from app.m1 import candidates, intent, postprocess, prompt, questions, schema_builder, session
from app.m1.postprocess import CompileError
from app.m1.questions import Question
from app.repositories import bbl, profiles, specs

MAX_BLOCKS_IN_PROMPT = 6

STATUS_NEED_ANSWER = "need_answer"
STATUS_COMPLETED = "completed"


@dataclass(frozen=True)
class NeedAnswer:
    session_id: str
    question: Question
    status: str = STATUS_NEED_ANSWER


@dataclass(frozen=True)
class Completed:
    session_id: str
    saved: specs.SavedSpec
    status: str = STATUS_COMPLETED


def start(user_id: int, user_text: str, llm_client) -> NeedAnswer | Completed:
    """새 요청. 되물을 것이 있으면 세션을 남기고 멈춘다."""
    return _run(session.new_session(user_id, user_text), llm_client)


def answer(session_id: str, reply: str, llm_client) -> NeedAnswer | Completed:
    """되묻기에 대한 답. 세션이 만료됐으면 처음부터 다시 요청해야 한다."""
    state = session.load(session_id)
    if state is None:
        raise CompileError("대화가 만료됐다 — 처음부터 다시 요청해야 한다")
    state.answers.append(reply)
    return _run(state, llm_client)


def _run(state: session.CompileSession, llm_client) -> NeedAnswer | Completed:
    profile = profiles.get_latest_profile(state.user_id)
    if profile is None:
        raise CompileError("확정된 투자 성향이 없다 — 설문을 먼저 마쳐야 한다")

    user_text = state.full_text
    parsed = intent.extract(user_text, llm_client)
    resolved = intent.resolve(parsed)

    question = questions.for_intent(resolved)
    if question is not None and question.kind not in state.asked:
        return _ask(state, question)

    candidate_set = candidates.select(
        risk_level=profile.risk_level,
        requested_tickers=list(resolved.requested_tickers),
        sectors=list(parsed.sectors),
        group_ids=list(parsed.asset_groups),
        preset_version=profile.preset_version,
    )
    if not candidate_set.candidates:
        # 같은 질문을 두 번 묻지 않는다. 두 번째에도 담을 게 없으면 포기하고
        # 사유를 그대로 올린다 — 무한히 되묻는 것보다 낫다.
        empty = questions.for_empty_candidates(candidate_set)
        if empty.kind in state.asked:
            raise CompileError(empty.text)
        return _ask(state, empty)

    blocks = bbl.search_blocks(list(resolved.tag_queries), limit=MAX_BLOCKS_IN_PROMPT)
    schema = schema_builder.build_compile_schema(candidate_set)
    text = prompt.build_compile_prompt(
        user_text=user_text,
        candidate_set=candidate_set,
        blocks=blocks,
        defaults=profiles.get_profile_defaults(candidate_set.preset_version, profile.risk_level),
    )

    raw = llm_client.generate_json(text, schema, system=prompt.SYSTEM_PROMPT)
    compiled = postprocess.parse(
        raw, candidate_set, spec_id=specs.new_spec_id(), user_id=state.user_id
    )
    saved = postprocess.save(
        compiled,
        user_id=state.user_id,
        profile_id=profile.profile_id,
        input_prompt=user_text,
    )
    session.drop(state.session_id)
    return Completed(session_id=state.session_id, saved=saved)


def _ask(state: session.CompileSession, question: Question) -> NeedAnswer:
    state.asked.append(question.kind)
    session.save(state)
    return NeedAnswer(session_id=state.session_id, question=question)
