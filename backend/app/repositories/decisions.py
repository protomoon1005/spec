"""판단 기록(라이브 모드) · 관점 점수 · 관점 성과 읽기·쓰기. docs/db-erd.md 가 정본이다.

라이브 판단(mode='live')만 다룬다. 백테스트 판단은 portfolio_id 가 없어서
지금 스키마(portfolio_id NOT NULL)로는 쓸 수 없다 — Q3 마이그레이션(run_id 축)
대기다.
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import text

from app.contracts.view_score import ViewScore
from app.core.db import get_engine

LIVE = "live"


# ── calibrated_prob(계약 ②) <-> calibrated_score(DB 컬럼) ─────────────
# 변환은 이 두 함수에만 있다. 둘은 같은 값이다 — 관점이 낸 "20거래일 뒤 종가가
# 오른다" 사건의 보정 확률, [0,1]. 이름만 다른 이유는 DB 컬럼이 계약 ②보다
# 먼저 정해졌기 때문이고, 척도 변환(로짓·백분율 등)은 없다. Brier 채점은 이
# 값을 확률로 그대로 쓰므로 척도를 바꾸고 싶으면 여기 두 함수만 고친다.
def calibrated_score_from_prob(prob: float) -> float:
    return float(prob)


def calibrated_prob_from_score(score) -> float:
    return float(score)


def get_portfolio_spec_id(portfolio_id: int) -> str | None:
    with get_engine().connect() as conn:
        return conn.execute(
            text("SELECT spec_id FROM portfolios WHERE portfolio_id = :pid"),
            {"pid": portfolio_id},
        ).scalar()


def insert_live_decision(
    *,
    portfolio_id: int,
    spec_id: str,
    as_of: date,
    integrated_signal: dict,
    view_weights_used: dict[str, float],
    model_version: str,
    feature_set_version: str,
    data_snapshot_asof: date | None,
    scores: list[ViewScore],
) -> int:
    """판단 한 건과 그 관점 점수 전부를 한 트랜잭션으로 넣고 decision_id 를 돌려준다.

    점수 없는 판단이 남으면 채점이 그 판단을 "관점이 아무 말도 안 했다"로 읽는다.
    risk_caps 이하 M2 칸은 비워 둔다 — M3 은 IntegratedSignal 에서 멈춘다.
    """
    with get_engine().begin() as conn:
        decision_id = conn.execute(
            text(
                """
                INSERT INTO decision_records (
                    spec_id, portfolio_id, as_of, mode, integrated_signal, view_weights_used,
                    model_version, feature_set_version, data_snapshot_asof
                ) VALUES (
                    :spec_id, :portfolio_id, :as_of, :mode,
                    CAST(:integrated_signal AS jsonb), CAST(:view_weights_used AS jsonb),
                    :model_version, :feature_set_version, :data_snapshot_asof
                )
                RETURNING decision_id
                """
            ),
            {
                "spec_id": spec_id,
                "portfolio_id": portfolio_id,
                "as_of": as_of,
                "mode": LIVE,
                "integrated_signal": json.dumps(integrated_signal, ensure_ascii=False),
                "view_weights_used": json.dumps(view_weights_used),
                "model_version": model_version,
                "feature_set_version": feature_set_version,
                "data_snapshot_asof": data_snapshot_asof,
            },
        ).scalar_one()
        if scores:
            conn.execute(
                text(
                    """
                    INSERT INTO view_scores
                        (decision_id, view_type, ticker, raw_score, calibrated_score, evidence)
                    VALUES
                        (:decision_id, :view_type, :ticker, :raw_score, :calibrated_score,
                         CAST(:evidence AS jsonb))
                    """
                ),
                [
                    {
                        "decision_id": decision_id,
                        "view_type": score.view_type,
                        "ticker": score.ticker,
                        "raw_score": score.raw_score,
                        "calibrated_score": calibrated_score_from_prob(score.calibrated_prob),
                        "evidence": json.dumps(score.evidence, ensure_ascii=False),
                    }
                    for score in scores
                ],
            )
    return decision_id


def list_live_decisions(portfolio_id: int, *, as_of: date) -> list[dict]:
    """as_of '미만' 판단일의 라이브 판단. 판단일마다 가장 나중 기록 하나, 오래된 것부터.

    같은 판단일을 다시 돌린 기록이 있으면 마지막 것만 채점 대상이다 — 같은 날을
    두 번 채점하면 그날의 손실이 두 배로 실린다.
    """
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT ON (as_of) decision_id, as_of
                  FROM decision_records
                 WHERE portfolio_id = :pid
                   AND mode = :mode
                   AND as_of < :as_of
                 ORDER BY as_of, decision_id DESC
                """
            ),
            {"pid": portfolio_id, "mode": LIVE, "as_of": as_of},
        ).all()
    return [dict(row._mapping) for row in rows]


def get_view_scores(decision_ids: list[int]) -> dict[int, list[dict]]:
    """{decision_id: [{view_type, ticker, raw_score, calibrated_prob}]}"""
    if not decision_ids:
        return {}
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT decision_id, view_type, ticker, raw_score, calibrated_score
                  FROM view_scores
                 WHERE decision_id = ANY(:ids)
                 ORDER BY decision_id, view_type, ticker
                """
            ),
            {"ids": list(decision_ids)},
        ).all()
    out: dict[int, list[dict]] = {}
    for row in rows:
        out.setdefault(row.decision_id, []).append(
            {
                "view_type": row.view_type,
                "ticker": row.ticker,
                "raw_score": float(row.raw_score),
                "calibrated_prob": calibrated_prob_from_score(row.calibrated_score),
            }
        )
    return out


def get_graded_decision_ids(portfolio_id: int) -> set[int]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT decision_id FROM view_performance WHERE portfolio_id = :pid"),
            {"pid": portfolio_id},
        ).all()
    return {row.decision_id for row in rows}


def insert_view_performance(*, portfolio_id: int, rows: list[dict]) -> int:
    """채점 결과를 한 트랜잭션으로 넣는다. rows 항목: decision_id · view_type ·
    as_of(판단일) · realized_at · hit_rate · contribution.

    판단 하나의 세 관점이 함께 들어가야 "채점됨" 판정(get_graded_decision_ids)이
    거짓말을 하지 않는다.
    """
    if not rows:
        return 0
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO view_performance
                    (portfolio_id, decision_id, view_type, as_of, realized_at, hit_rate, contribution)
                VALUES
                    (:portfolio_id, :decision_id, :view_type, :as_of, :realized_at,
                     :hit_rate, :contribution)
                """
            ),
            [{"portfolio_id": portfolio_id, **row} for row in rows],
        )
    return len(rows)
