"""ml_models 등록과 조회. docs/db-erd.md 가 정본이다.

시점 규약 대상이 아니다 — 모델 레지스트리는 시점 자산이 아니라 산출물 목록이다.
시점 무결성은 모델 버전(= cutoff)이 지킨다: 스코어러는 as_of 로 cutoff 를 정하고 그
버전을 콕 집어 읽는다. "지금 활성 모델" 은 실시간 판단 경로만 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text

from app.core.db import get_engine

_COLUMNS = """
    model_version, view_type, train_start, train_end, accuracy, brier_score,
    calibration_method, artifact_uri, is_active, trained_at
"""


@dataclass(frozen=True)
class MlModel:
    model_version: str
    view_type: str
    train_start: date | None
    train_end: date | None
    accuracy: float | None
    brier_score: float | None
    calibration_method: str | None
    artifact_uri: str | None
    is_active: bool
    trained_at: datetime | None


def register_model(
    *,
    model_version: str,
    view_type: str,
    train_start: date,
    train_end: date,
    accuracy: float,
    brier_score: float,
    calibration_method: str,
    artifact_uri: str,
) -> None:
    """새 모델을 활성으로 등록한다. 같은 view_type 의 기존 활성은 한 트랜잭션 안에서 내린다.

    활성이 둘이거나 0개인 순간이 읽는 쪽에 보이지 않게 하려는 것이다. 같은 버전을 다시
    등록하면 PK 충돌(IntegrityError)을 그대로 올린다 — 조용히 덮어쓰지 않는다.
    """
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE ml_models SET is_active = false WHERE view_type = :view_type AND is_active"),
            {"view_type": view_type},
        )
        conn.execute(
            text(
                """
                INSERT INTO ml_models
                    (model_version, view_type, train_start, train_end, accuracy, brier_score,
                     calibration_method, artifact_uri, is_active, trained_at)
                VALUES
                    (:model_version, :view_type, :train_start, :train_end, :accuracy, :brier_score,
                     :calibration_method, :artifact_uri, true, now())
                """
            ),
            {
                "model_version": model_version,
                "view_type": view_type,
                "train_start": train_start,
                "train_end": train_end,
                "accuracy": accuracy,
                "brier_score": brier_score,
                "calibration_method": calibration_method,
                "artifact_uri": artifact_uri,
            },
        )


def get_model(model_version: str) -> MlModel | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text(f"SELECT {_COLUMNS} FROM ml_models WHERE model_version = :model_version"),
            {"model_version": model_version},
        ).one_or_none()
    return None if row is None else _as_record(row)


def get_active_model(view_type: str) -> MlModel | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM ml_models"
                " WHERE view_type = :view_type AND is_active"
                " ORDER BY trained_at DESC LIMIT 1"
            ),
            {"view_type": view_type},
        ).one_or_none()
    return None if row is None else _as_record(row)


def _as_record(row) -> MlModel:
    data = dict(row._mapping)
    for key in ("accuracy", "brier_score"):
        if data[key] is not None:
            data[key] = float(data[key])
    return MlModel(**data)
