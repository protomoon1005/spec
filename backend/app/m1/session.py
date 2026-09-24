# 되묻기 대화 상태 (M1 6단계).
#
# 설명을 주석에 두는 이유는 같은 폴더의 다른 파일과 같다 — as_of 가드 사정권이다.
#
# ── 왜 Redis 인가 (2026-09-20 팀 결정) ───────────────────────────────
# 파이썬 변수로는 안 된다. **컴파일은 worker 컨테이너에서 돌고 사용자 응답은 api 로
# 들어온다.** 프로세스가 달라 메모리를 공유할 수 없다. 되묻기는 "worker 가 묻고 →
# 사용자가 api 로 답하고 → worker 가 이어받는" 왕복이라 정확히 여기서 막힌다.
# 거기다 개발 중에는 코드를 고칠 때마다 프로세스가 다시 뜬다.
#
# 표를 새로 만들지 않은 이유도 있다. 스키마를 바꾸면 팀원 전원이 데이터베이스를
# 날려야 하고, 뉴스를 쌓는 사람은 백업까지 해야 한다. Redis 는 이미 떠 있고
# (Celery 가 쓴다) 만료를 걸 수 있어 버려진 대화가 쌓이지 않는다.
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field

import redis

from app.core.config import get_settings

KEY_PREFIX = "m1:compile:"
TTL_SECONDS = 30 * 60


@dataclass
class CompileSession:
    session_id: str
    user_id: int
    user_text: str
    # 되묻기에 사용자가 준 답. 순서대로 쌓인다.
    answers: list[str] = field(default_factory=list)
    # 같은 질문을 두 번 묻지 않기 위한 기록.
    asked: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        # 답을 처음 요청 뒤에 이어 붙이고 **처음부터 다시 돌린다.**
        # 중간 상태를 되살려 이어가는 것보다 단순하고, 사용자가 앞 내용을 뒤집는
        # 답을 해도(“역시 채권 말고 주식으로”) 그대로 반영된다.
        # 비용은 의도 추출 한 번을 더 부르는 것뿐이다.
        return "\n".join([self.user_text, *self.answers])


def new_session(user_id: int, user_text: str) -> CompileSession:
    return CompileSession(session_id=uuid.uuid4().hex, user_id=user_id, user_text=user_text)


def save(session: CompileSession) -> None:
    _client().set(_key(session.session_id), json.dumps(asdict(session), ensure_ascii=False),
                  ex=TTL_SECONDS)


def load(session_id: str) -> CompileSession | None:
    raw = _client().get(_key(session_id))
    if raw is None:
        return None
    return CompileSession(**json.loads(raw))


def drop(session_id: str) -> None:
    _client().delete(_key(session_id))


def _key(session_id: str) -> str:
    return f"{KEY_PREFIX}{session_id}"


def _client() -> redis.Redis:
    # 연결을 모듈 수준에 캐시하지 않는다. Celery 워커가 프로세스를 포크하면
    # 물려받은 연결이 깨진다 — 흔한 사고라 매번 만든다(비용은 무시할 수준이다).
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
