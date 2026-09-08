"""feature_store as_of 질의. docs/db-erd.md 4.5가 정본이다."""
from __future__ import annotations

from datetime import date

from sqlalchemy import text

from app.core.db import get_engine


def get_features(ticker: str, *, as_of: date, feature_set_version: str) -> dict | None:
    """as_of 이후의 데이터는 어떤 경우에도 반환하지 않는다."""
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT features
                  FROM feature_store
                 WHERE ticker = :ticker
                   AND feature_set_version = :feature_set_version
                   AND as_of <= :as_of
                 ORDER BY as_of DESC
                 LIMIT 1
                """
            ),
            {"ticker": ticker, "feature_set_version": feature_set_version, "as_of": as_of},
        ).one_or_none()
    return row.features if row is not None else None
