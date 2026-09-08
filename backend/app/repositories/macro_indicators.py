"""macro_indicators as_of 질의. docs/db-erd.md 4.5가 정본이다."""
from __future__ import annotations

from datetime import date

from sqlalchemy import text

from app.core.db import get_engine


def get_macro(indicator_code: str, *, as_of: date) -> float | None:
    """거시지표는 공표 시차가 있으므로 released_at 기준으로도 걸러야 한다."""
    with get_engine().connect() as conn:
        value = conn.execute(
            text(
                """
                SELECT value
                  FROM macro_indicators
                 WHERE indicator_code = :indicator_code
                   AND as_of       <= :as_of
                   AND released_at <= :as_of
                 ORDER BY as_of DESC
                 LIMIT 1
                """
            ),
            {"indicator_code": indicator_code, "as_of": as_of},
        ).scalar_one_or_none()
    return float(value) if value is not None else None
