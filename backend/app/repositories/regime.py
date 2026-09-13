"""regime_snapshots 적재와 시점 경계 조회.

이 테이블은 아직 scripts/check_asof_guard.py 의 감시 대상이 아니다(Q5: T8 이후
현서가 별도 PR로 넓힌다). 그때 파일을 옮기지 않으려고 처음부터 저장소 계층에 뒀다.

PK 가 (as_of, trend_index) 라 추세 지수를 여러 개 동시에 볼 수 있는 구조다.
적재는 ON CONFLICT DO UPDATE 다 — 거시지표가 개정되면 그날의 국면 판정도 바뀐다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text

from app.core.db import get_engine


@dataclass(frozen=True)
class RegimeSnapshot:
    as_of: date
    trend_index: str
    regime_label: str
    intensity: float
    threshold_state: dict


def upsert_regime_snapshot(
    *,
    as_of: date,
    trend_index: str,
    regime_label: str,
    intensity: float,
    threshold_state: dict,
) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO regime_snapshots
                    (as_of, trend_index, regime_label, intensity, threshold_state)
                VALUES
                    (:as_of, :trend_index, :regime_label, :intensity,
                     CAST(:threshold_state AS jsonb))
                ON CONFLICT (as_of, trend_index)
                DO UPDATE SET regime_label = EXCLUDED.regime_label,
                              intensity = EXCLUDED.intensity,
                              threshold_state = EXCLUDED.threshold_state
                """
            ),
            {
                "as_of": as_of,
                "trend_index": trend_index,
                "regime_label": regime_label,
                "intensity": intensity,
                "threshold_state": json.dumps(threshold_state, ensure_ascii=False, allow_nan=False),
            },
        )


def get_regime_snapshot(trend_index: str, *, as_of: date) -> RegimeSnapshot | None:
    """as_of 이후에 계산된 스냅샷은 어떤 경우에도 반환하지 않는다."""
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT as_of, trend_index, regime_label, intensity, threshold_state
                  FROM regime_snapshots
                 WHERE trend_index = :trend_index
                   AND as_of <= :as_of
                 ORDER BY as_of DESC
                 LIMIT 1
                """
            ),
            {"trend_index": trend_index, "as_of": as_of},
        ).one_or_none()

    if row is None:
        return None
    return RegimeSnapshot(
        as_of=row.as_of,
        trend_index=row.trend_index,
        regime_label=row.regime_label,
        intensity=float(row.intensity),
        threshold_state=row.threshold_state,
    )
