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


def get_spec_universe(spec_id: str) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT ticker, preset_id, weight_min, weight_max,
                       weight_min_raw, weight_max_raw, was_adjusted
                  FROM spec_universe
                 WHERE spec_id = :spec_id
                 ORDER BY ticker
                """
            ),
            {"spec_id": spec_id},
        ).all()
    return [dict(row._mapping) for row in rows]


def _dump(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
