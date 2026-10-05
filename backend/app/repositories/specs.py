"""전략서 저장 (M1 5단계).

시점 규약 대상이 아니다.

## 승인 전까지만 손댈 수 있다

`status='approved'` 가 되면 트리거가 수정을 물리적으로 막는다. M1 은 `draft` 로만
저장하고, 승인은 사용자가 별도로 한다.

## 원출력과 확정값을 둘 다 남긴다

`weight_min_raw`/`weight_max_raw` 는 LLM 이 낸 값, `weight_min`/`weight_max` 는
규칙을 적용한 값이다. M1 단계에서는 **스키마가 이미 허용 범위를 강제**했으므로 둘이
같고 `was_adjusted` 는 false 다. 하드캡으로 접는 일은 Validator 몫이고(2026-09-20
팀 결정), 그때 이 칸들이 갈라진다.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from sqlalchemy import text

from app.core.db import get_engine

SPEC_ID_PREFIX = "STR"
DRAFT = "draft"


@dataclass(frozen=True)
class SavedSpec:
    spec_id: str
    user_id: int
    profile_id: int
    status: str
    universe_size: int


def new_spec_id() -> str:
    return f"{SPEC_ID_PREFIX}-{uuid.uuid4().hex[:12]}"


def insert_spec(
    *,
    spec_id: str,
    user_id: int,
    profile_id: int,
    hardcap_version: str,
    spec_version: str,
    name: str,
    input_prompt: str | None,
    rebalance: dict,
    signal_rules: dict,
    constraint: dict,
    universe: list[dict],
) -> SavedSpec:
    """전략서 한 건과 종목별 허용범위를 한 트랜잭션으로 넣는다.

    universe 항목은 ticker · preset_id · weight_min · weight_max ·
    weight_min_raw · weight_max_raw · was_adjusted 를 가진다.
    둘로 나눠 저장하면 종목 없는 전략서가 남을 수 있어 한 번에 넣는다.
    """
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO strategy_specs (
                    spec_id, user_id, profile_id, hardcap_version, spec_version,
                    name, input_prompt, rebalance, signal_rules, constraint_user, status
                ) VALUES (
                    :spec_id, :user_id, :profile_id, :hardcap_version, :spec_version,
                    :name, :input_prompt,
                    CAST(:rebalance AS jsonb), CAST(:signal_rules AS jsonb),
                    CAST(:constraint_user AS jsonb), :status
                )
                """
            ),
            {
                "spec_id": spec_id,
                "user_id": user_id,
                "profile_id": profile_id,
                "hardcap_version": hardcap_version,
                "spec_version": spec_version,
                "name": name,
                "input_prompt": input_prompt,
                "rebalance": _dump(rebalance),
                "signal_rules": _dump(signal_rules),
                "constraint_user": _dump(constraint),
                "status": DRAFT,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO spec_universe (
                    spec_id, ticker, preset_id,
                    weight_min, weight_max, weight_min_raw, weight_max_raw, was_adjusted
                ) VALUES (
                    :spec_id, :ticker, :preset_id,
                    :weight_min, :weight_max, :weight_min_raw, :weight_max_raw, :was_adjusted
                )
                """
            ),
            [{"spec_id": spec_id, **item} for item in universe],
        )
    return SavedSpec(
        spec_id=spec_id,
        user_id=user_id,
        profile_id=profile_id,
        status=DRAFT,
        universe_size=len(universe),
    )


def get_spec(spec_id: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT spec_id, user_id, profile_id, hardcap_version, spec_version, name,
                       input_prompt, rebalance, signal_rules, constraint_user, status, created_at
                  FROM strategy_specs
                 WHERE spec_id = :spec_id
                """
            ),
            {"spec_id": spec_id},
        ).one_or_none()
    return dict(row._mapping) if row is not None else None


def get_spec_universe(spec_id: str) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT u.ticker, e.name, u.preset_id, u.weight_min, u.weight_max,
                       u.weight_min_raw, u.weight_max_raw, u.was_adjusted
                  FROM spec_universe u
                  JOIN etf_master e ON e.ticker = u.ticker
                 WHERE u.spec_id = :spec_id
                 ORDER BY u.ticker
                """
            ),
            {"spec_id": spec_id},
        ).all()
    return [dict(row._mapping) for row in rows]


def get_spec_risk_level(spec_id: str) -> int | None:
    """전략서가 만들어질 때 참조한 성향 등급. 사용자의 최신 성향이 아니다.

    재진단하면 risk_profiles 에 새 행이 쌓이지만, 전략서는 생성 당시의
    profile_id 를 들고 있으므로 그 행을 본다. 없는 전략서면 None.
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT p.risk_level
                  FROM strategy_specs s
                  JOIN risk_profiles p ON p.profile_id = s.profile_id
                 WHERE s.spec_id = :spec_id
                """
            ),
            {"spec_id": spec_id},
        ).one_or_none()
    return int(row.risk_level) if row is not None else None


def get_spec_profile(spec_id: str) -> tuple[int, str] | None:
    """전략서가 만들어질 때의 (성향 등급, 기준표 버전). 없는 전략서면 None.

    get_spec_risk_level 과 같은 행을 보되 기준표 버전까지 준다 — Validator 는
    성향 확정 때 박아 둔 기준표로 허용범위를 따져야 한다.
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT p.risk_level, p.preset_version
                  FROM strategy_specs s
                  JOIN risk_profiles p ON p.profile_id = s.profile_id
                 WHERE s.spec_id = :spec_id
                """
            ),
            {"spec_id": spec_id},
        ).one_or_none()
    return (int(row.risk_level), row.preset_version) if row is not None else None


def record_validation(spec_id: str, *, bounds: list[dict] | None, logs: list[dict]) -> None:
    """Validator 결과를 한 트랜잭션으로 남긴다. draft 가 아니면 ValueError.

    bounds: ticker · weight_min · weight_max · was_adjusted. None 이면 범위를 건드리지
    않는다(1~3단에서 막혀 확정값이 없다). **_raw 칸은 이 함수가 아예 다루지 않는다.**
    logs: stage · passed · violations · adjusted_bounds · clamped_fields. 단계마다 한 행.
    """
    with get_engine().begin() as conn:
        status = conn.execute(
            text("SELECT status FROM strategy_specs WHERE spec_id = :s FOR UPDATE"), {"s": spec_id}
        ).scalar()
        if status != DRAFT:
            raise ValueError(f"draft 전략서만 검증 결과를 남길 수 있다: {spec_id} ({status})")
        if bounds:
            conn.execute(
                text(
                    """
                    UPDATE spec_universe
                       SET weight_min = :weight_min, weight_max = :weight_max, was_adjusted = :was_adjusted
                     WHERE spec_id = :spec_id AND ticker = :ticker
                    """
                ),
                [{"spec_id": spec_id, **row} for row in bounds],
            )
        if logs:
            conn.execute(
                text(
                    """
                    INSERT INTO validation_logs
                        (spec_id, stage, passed, violations, adjusted_bounds, clamped_fields)
                    VALUES (:spec_id, :stage, :passed, CAST(:violations AS jsonb),
                            CAST(:adjusted_bounds AS jsonb), CAST(:clamped_fields AS jsonb))
                    """
                ),
                [
                    {
                        "spec_id": spec_id,
                        "stage": row["stage"],
                        "passed": row["passed"],
                        "violations": _dump(row["violations"]),
                        "adjusted_bounds": _dump_or_none(row.get("adjusted_bounds")),
                        "clamped_fields": _dump_or_none(row.get("clamped_fields")),
                    }
                    for row in logs
                ],
            )


def latest_validation(spec_id: str) -> list[dict]:
    """가장 최근 검증 한 번의 단계별 기록. 단계 순서대로. 검증한 적이 없으면 빈 목록.

    record_validation 은 한 번의 검증을 한 트랜잭션으로 넣으므로 그 행들의 checked_at
    (트랜잭션 시작 시각)이 같다. 가장 늦은 checked_at 의 행들이 마지막 검증이다.
    """
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT stage, passed, violations, adjusted_bounds, clamped_fields, checked_at
                  FROM validation_logs
                 WHERE spec_id = :spec_id
                   AND checked_at = (SELECT max(checked_at) FROM validation_logs WHERE spec_id = :spec_id)
                 ORDER BY stage
                """
            ),
            {"spec_id": spec_id},
        ).all()
    return [dict(row._mapping) for row in rows]


def list_specs(user_id: int) -> list[dict]:
    """사용자의 전략서 목록. 최근 것부터, 종목 수를 곁들인다."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT s.spec_id, s.name, s.status, s.created_at,
                       (SELECT count(*) FROM spec_universe u WHERE u.spec_id = s.spec_id) AS universe_size
                  FROM strategy_specs s
                 WHERE s.user_id = :user_id
                 ORDER BY s.created_at DESC
                """
            ),
            {"user_id": user_id},
        ).all()
    return [dict(row._mapping) for row in rows]


# draft 전략서가 지워질 때 함께 지우는 하위 기록. 나머지(백테스트·승인·포트폴리오·
# 판단 기록)가 붙은 전략서는 지우지 않는다 — 실제로 굴러간 흔적이라서다.
_OWNED_BY_DRAFT = ("spec_universe", "validation_logs")
_BLOCKS_DELETE = ("backtest_runs", "approvals", "portfolios", "decision_records")


def delete_draft_spec(spec_id: str, user_id: int) -> str:
    """draft 전략서를 하위 기록과 함께 한 트랜잭션으로 지운다.

    결과: "deleted" · "not_found"(없거나 남의 것) · "not_draft" · "has_records".
    FK 에 ON DELETE CASCADE 가 없으므로 하위 테이블을 먼저 지운다.
    """
    with get_engine().begin() as conn:
        row = conn.execute(
            text("SELECT user_id, status FROM strategy_specs WHERE spec_id = :s FOR UPDATE"),
            {"s": spec_id},
        ).one_or_none()
        if row is None or row.user_id != user_id:
            return "not_found"
        if row.status != DRAFT:
            return "not_draft"
        for table in _BLOCKS_DELETE:
            found = conn.execute(text(f"SELECT 1 FROM {table} WHERE spec_id = :s LIMIT 1"), {"s": spec_id})
            if found.first():
                return "has_records"
        for table in _OWNED_BY_DRAFT:
            conn.execute(text(f"DELETE FROM {table} WHERE spec_id = :s"), {"s": spec_id})
        conn.execute(text("DELETE FROM strategy_specs WHERE spec_id = :s"), {"s": spec_id})
    return "deleted"


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _dump_or_none(value) -> str | None:
    return None if value is None else _dump(value)
