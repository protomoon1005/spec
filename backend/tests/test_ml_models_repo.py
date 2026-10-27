"""ml_models 등록·조회(app/repositories/ml_models.py). DB 가 필요하다.

실제 시장분석 모델 행을 건드리지 않으려고 쓰지 않는 view_type(sentiment)으로 돈다.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.repositories import ml_models

VIEW = "sentiment"


@pytest.fixture
def versions(engine):
    prefix = f"test-mlrepo-{uuid.uuid4().hex[:8]}"
    yield [f"{prefix}-a", f"{prefix}-b"]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ml_models WHERE model_version LIKE :p"), {"p": f"{prefix}%"})


def _register(version: str) -> None:
    ml_models.register_model(
        model_version=version,
        view_type=VIEW,
        train_start=date(2019, 1, 2),
        train_end=date(2024, 5, 10),
        accuracy=0.61,
        brier_score=0.23,
        calibration_method="isotonic",
        artifact_uri=f"s3://models/{version}.json",
    )


def test_register_switches_active(versions):
    first, second = versions
    _register(first)
    assert ml_models.get_active_model(VIEW).model_version == first

    _register(second)
    assert ml_models.get_active_model(VIEW).model_version == second
    assert ml_models.get_model(first).is_active is False

    record = ml_models.get_model(second)
    assert record.brier_score == pytest.approx(0.23)
    assert record.train_end == date(2024, 5, 10)
    assert record.trained_at is not None


def test_same_version_twice_raises_and_keeps_active(versions):
    first, _ = versions
    _register(first)
    with pytest.raises(IntegrityError):
        _register(first)
    # 실패한 트랜잭션이 기존 활성 해제까지 되돌렸어야 한다.
    assert ml_models.get_active_model(VIEW).model_version == first


def test_unknown_version_is_none():
    assert ml_models.get_model(f"no-such-{uuid.uuid4().hex}") is None
