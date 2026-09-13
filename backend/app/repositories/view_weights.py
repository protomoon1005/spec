"""view_weights as_of 질의와 append-only 쓰기. docs/db-erd.md 4.5가 정본이다."""
from __future__ import annotations

import math
from datetime import date

from sqlalchemy import text

from app.contracts.view_weights import WEIGHT_FLOOR
from app.core.db import get_engine

# 합이 1인지 볼 때의 허용오차. app/views/ 의 integrate() · hedge 가 쓰는 값과 같다.
WEIGHT_SUM_TOLERANCE = 1e-9


def get_view_weights(portfolio_id: int, *, as_of: date) -> dict[str, float]:
    """오늘 성과가 오늘 판단에 쓰이면 미래를 미리 보는 것이다.
    as_of '미만'(strict <)으로 계산된 행만 읽는다."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT ON (view_type) view_type, weight
                  FROM view_weights
                 WHERE portfolio_id = :portfolio_id
                   AND as_of < :as_of
                 ORDER BY view_type, as_of DESC
                """
            ),
            {"portfolio_id": portfolio_id, "as_of": as_of},
        ).all()
    return {row.view_type: float(row.weight) for row in rows}


def insert_view_weights(
    *,
    as_of: date,
    weights: dict[str, float],
    update_rule_version: str,
    portfolio_id: int | None = None,
    run_id: int | None = None,
) -> None:
    """관점 가중치 한 시점분을 append-only로 기록한다.

    이 테이블은 004_triggers가 모든 UPDATE를 거부한다. 재계산이 필요하면 같은
    행을 고치는 게 아니라 새 as_of 행을 쓴다 — 그래서 uq_view_weight 충돌은
    잡지 않고 그대로 올린다. ON CONFLICT DO NOTHING/DO UPDATE 를 쓰면 "언제
    무엇으로 계산했는가"의 이력이 거짓말이 된다.

    세 관점 행을 한 트랜잭션으로 넣는다. 일부만 들어간 시점은 읽는 쪽에서
    합이 1이 아닌 가중치로 보이므로, 전부 들어가거나 전부 안 들어가야 한다.

    run_id 인자는 백테스트 축이다(팀 확정 Q3, 2026-09-12). 마이그레이션이 아직
    들어오지 않아 지금은 받기만 하고 NotImplementedError를 낸다 — 나중에
    시그니처를 바꾸지 않으려고 최종형으로 미리 잡아 뒀다.
    """
    if (portfolio_id is None) == (run_id is None):
        raise ValueError("portfolio_id 와 run_id 중 정확히 하나만 줘야 한다")
    if run_id is not None:
        raise NotImplementedError(
            "run_id 축은 스키마 마이그레이션(Q3: run_id 컬럼 + portfolio_id NULLABLE "
            "+ 부분 UNIQUE 인덱스 2개)이 들어온 뒤에 구현한다"
        )

    _validate_weights(weights)

    rows = [
        {
            "portfolio_id": portfolio_id,
            "as_of": as_of,
            "view_type": view_type,
            "weight": weights[view_type],
            "weight_floor": WEIGHT_FLOOR,
            "update_rule_version": update_rule_version,
        }
        for view_type in sorted(weights)
    ]

    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO view_weights
                    (portfolio_id, as_of, view_type, weight, weight_floor, update_rule_version)
                VALUES
                    (:portfolio_id, :as_of, :view_type, :weight, :weight_floor, :update_rule_version)
                """
            ),
            rows,
        )


def _validate_weights(weights: dict[str, float]) -> None:
    """쓰기 전에 합이 1인지 본다.

    읽는 쪽(app/views/ 의 통합기)이 같은 검사를 하고 어긋나면 예외를 낸다.
    여기서 안 막으면 잘못된 행이 조용히 들어갔다가 한참 뒤 판단 시점에 터진다.
    쓰는 쪽에서 막는 게 맞다.
    """
    if not weights:
        raise ValueError("가중치가 비어 있다")

    negative = sorted(view_type for view_type, weight in weights.items() if weight < 0)
    if negative:
        raise ValueError(f"음수 가중치: {negative}")

    total = math.fsum(weights.values())
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise ValueError(f"가중치 합이 1이 아니다 (Sigma w = {total!r})")
