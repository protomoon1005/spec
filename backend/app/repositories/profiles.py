"""투자 성향 설문과 성향 등급 저장. as_of 규약 대상은 아니지만(시점 자산이 아니라
사용자 프로필이다), app/core/db.py 의 규약을 지키기 위해 라우터도 이 리포지토리를
거친다 — users.py 와 같은 이유다.

## 답변은 쌓고, 최신 것만 유효로 본다

survey_responses 에는 UNIQUE 제약이 없다. 답을 고칠 때마다 새 행이 쌓이고,
같은 문항의 가장 최근 답만 유효한 답으로 읽는다. 답을 고치다가 확정하는 흐름
(설문 제출과 성향 확정이 API 두 개로 나뉜 이유)이 그대로 성립한다.

## 성향도 쌓는다

risk_profiles 에도 사용자당 한 행 제약이 없다. 다시 진단하면 새 행이 생기고
조회는 가장 최근 것을 본다. 덮어쓰지 않는 이유는 전략서가 profile_id 를 필수로
참조하기 때문이다 — 덮어쓰면 "그때 어떤 성향으로 만들어졌는지"가 사라진다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text

from app.core.db import get_engine
from app.repositories.presets import active_preset_version

# 기준표 버전 조회는 presets 쪽 한 곳만 둔다. 사본이 둘이면 정렬 규칙 같은 것을
# 한쪽만 고치게 된다. 성향을 확정할 때 이 값을 함께 박아 둔다 —
# 나중에 기준표가 바뀌어도 과거 성향의 산정 근거가 흔들리지 않는다.
__all__ = ["active_preset_version"]


@dataclass(frozen=True)
class ProfileRecord:
    profile_id: int
    user_id: int
    risk_level: int
    preset_version: str
    cash_min_default: float | None
    max_drawdown_default: float | None
    max_loss_per_trade_default: float | None
    created_at: datetime


def save_answers(user_id: int, answers: dict[str, tuple[str, int]]) -> int:
    """문항별 답과 점수를 쌓는다. 고친 답은 새 행으로 들어간다. 저장한 행 수를 돌려준다."""
    if not answers:
        return 0
    rows = [
        {"user_id": user_id, "question_code": code, "answer_code": answer, "score": score}
        for code, (answer, score) in answers.items()
    ]
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO survey_responses (user_id, question_code, answer_code, score, answered_at)
                VALUES (:user_id, :question_code, :answer_code, :score, now())
                """
            ),
            rows,
        )
    return len(rows)


def get_latest_answers(user_id: int) -> dict[str, str]:
    """문항별 최신 답 하나씩. {문항코드: 선택지코드}.

    같은 문항에 답이 여러 번 쌓여 있으면 가장 최근 것만 쓴다. 같은 시각에 두 행이
    들어간 경우를 대비해 response_id 로도 정렬한다 — 그래야 결과가 흔들리지 않는다.
    """
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT ON (question_code) question_code, answer_code
                  FROM survey_responses
                 WHERE user_id = :user_id
                 ORDER BY question_code, answered_at DESC, response_id DESC
                """
            ),
            {"user_id": user_id},
        ).all()
    return {row.question_code: row.answer_code for row in rows}


def get_profile_defaults(preset_version: str, risk_level: int) -> dict[str, float] | None:
    """성향별 제약 기본값(현금 하한·최대 낙폭·1회 최대 손실)."""
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT cash_min_default, max_drawdown_default, max_loss_per_trade_default
                  FROM risk_profile_defaults
                 WHERE preset_version = :preset_version AND risk_level = :risk_level
                """
            ),
            {"preset_version": preset_version, "risk_level": risk_level},
        ).one_or_none()
    if row is None:
        return None
    return {key: float(value) for key, value in row._mapping.items()}


def insert_profile(
    user_id: int,
    risk_level: int,
    preset_version: str,
    defaults: dict[str, float],
    provenance: dict,
) -> ProfileRecord:
    """성향 한 건을 새로 남긴다. 덮어쓰지 않는다."""
    with get_engine().begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO risk_profiles (
                    user_id, risk_level, preset_version,
                    cash_min_default, max_drawdown_default, max_loss_per_trade_default,
                    provenance
                )
                VALUES (
                    :user_id, :risk_level, :preset_version,
                    :cash_min_default, :max_drawdown_default, :max_loss_per_trade_default,
                    CAST(:provenance AS jsonb)
                )
                RETURNING profile_id, user_id, risk_level, preset_version,
                          cash_min_default, max_drawdown_default, max_loss_per_trade_default,
                          created_at
                """
            ),
            {
                "user_id": user_id,
                "risk_level": risk_level,
                "preset_version": preset_version,
                "provenance": json.dumps(provenance, ensure_ascii=False),
                **defaults,
            },
        ).one()
    return _as_record(row)


def get_latest_profile(user_id: int) -> ProfileRecord | None:
    """가장 최근에 확정된 성향. 재진단하면 새 행이 쌓이므로 최신 것을 본다."""
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT profile_id, user_id, risk_level, preset_version,
                       cash_min_default, max_drawdown_default, max_loss_per_trade_default,
                       created_at
                  FROM risk_profiles
                 WHERE user_id = :user_id
                 ORDER BY created_at DESC, profile_id DESC
                 LIMIT 1
                """
            ),
            {"user_id": user_id},
        ).one_or_none()
    return _as_record(row) if row is not None else None


def _as_record(row) -> ProfileRecord:
    data = dict(row._mapping)
    for key in ("cash_min_default", "max_drawdown_default", "max_loss_per_trade_default"):
        if data[key] is not None:
            data[key] = float(data[key])
    return ProfileRecord(**data)
