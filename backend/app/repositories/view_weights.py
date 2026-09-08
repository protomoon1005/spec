"""view_weights as_of 질의. docs/db-erd.md 4.5가 정본이다."""
from __future__ import annotations

from datetime import date

from sqlalchemy import text

from app.core.db import get_engine


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
