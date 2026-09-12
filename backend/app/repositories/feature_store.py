"""feature_store as_of 질의와 적재. docs/db-erd.md 4.5가 정본이다."""
from __future__ import annotations

import json
import math
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


def upsert_features(
    ticker: str, *, as_of: date, feature_set_version: str, features: dict
) -> None:
    """한 (종목, 시점, 피처셋 버전)의 피처를 적재한다. 같은 키면 덮어쓴다.

    **view_weights 와 달리 여기는 append-only 가 아니다.** 관점 가중치는 이력
    자체가 자산이라 UPDATE를 트리거가 막지만, 피처는 같은 시점을 다시 계산해
    덮어쓰는 것이 정상이다 — 계산이 틀렸거나 원본 가격이 정정되면 다시 계산해야
    한다. 이 테이블에는 트리거가 없고, PK(ticker, as_of, feature_set_version)
    충돌 시 ON CONFLICT DO UPDATE 로 덮어쓴다.

    같은 버전 문자열에 다른 정의가 섞이면 과거 결정을 재현할 수 없으므로,
    피처 목록이나 계산식이 바뀌면 덮어쓰는 게 아니라 feature_set_version 을
    올려야 한다. 그건 이 함수가 강제할 수 없고 호출자의 책임이다.

    ticker 에는 FK 가 없다 — etf_master 가 비어 있어도 이 경로는 막히지 않는다.
    """
    _validate_features(features)

    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO feature_store (ticker, as_of, feature_set_version, features)
                VALUES (:ticker, :as_of, :feature_set_version, CAST(:features AS jsonb))
                ON CONFLICT (ticker, as_of, feature_set_version)
                DO UPDATE SET features = EXCLUDED.features, computed_at = now()
                """
            ),
            {
                "ticker": ticker,
                "as_of": as_of,
                "feature_set_version": feature_set_version,
                "features": json.dumps(features, allow_nan=False, sort_keys=True),
            },
        )


def _validate_features(features: dict) -> None:
    """JSONB 로 들어갈 수 없는 값을 미리 막는다.

    NaN/Inf 는 JSON 표준에 없어서 그대로 보내면 적재 시점에 터진다. 결측은
    None(JSON null)으로 남긴다 — 0으로 채우면 "지표가 0이다"와 "아직 계산할
    이력이 모자란다"가 구분되지 않는다.
    """
    if not isinstance(features, dict):
        raise TypeError(f"features 는 dict 여야 한다: {type(features).__name__}")
    if not features:
        raise ValueError("features 가 비어 있다")

    for name, value in features.items():
        if not isinstance(name, str):
            raise TypeError(f"피처 이름은 문자열이어야 한다: {name!r}")
        if value is None or isinstance(value, (bool, int, str)):
            continue
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError(f"피처 '{name}' 이 유한하지 않다: {value!r} (결측은 None 으로)")
            continue
        raise TypeError(f"피처 '{name}' 의 타입을 JSONB 로 보낼 수 없다: {type(value).__name__}")
