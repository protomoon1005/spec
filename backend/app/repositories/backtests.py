"""백테스트 실행 기록과 결과 (backtest_runs · backtest_metrics).

시점 규약 대상이 아니다 — 실행 기록이지 시점 자산이 아니다.

## 결과 곡선과 실패 이유를 window_results 에 둔다 (2026-09-28 결정 D2-a)

자산곡선·리밸런싱 결정을 둘 칸이 표에 없어서 backtest_metrics.window_results 에
담는다. 칸 이름(워크포워드 창별 결과)과 뜻이 어긋난다 — docs/known-issues.md.
backtest_runs 에도 실패 이유 칸이 없어서, 실패하면 지표를 전부 비운 결과 행을
만들고 window_results 에 {"error": 이유} 를 넣는다.

상태는 queued → running → done | failed 한 방향이다.
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import text

from app.core.db import get_engine

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

_METRIC_FIELDS = ("cagr", "mdd", "sharpe", "sortino", "win_rate", "benchmark_cagr")


def create_run(
    spec_id: str,
    *,
    period_start: date,
    period_end: date,
    seed_money: float,
    feature_set_version: str,
) -> dict:
    """queued 상태로 한 건 넣는다. 비용 조건은 워커가 시작할 때 채운다(mark_running)."""
    with get_engine().begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO backtest_runs
                    (spec_id, period_start, period_end, seed_money, feature_set_version, status)
                VALUES (:spec_id, :period_start, :period_end, :seed_money, :feature_set_version, :status)
                RETURNING run_id, spec_id, period_start, period_end, status, started_at
                """
            ),
            {
                "spec_id": spec_id,
                "period_start": period_start,
                "period_end": period_end,
                "seed_money": seed_money,
                "feature_set_version": feature_set_version,
                "status": QUEUED,
            },
        ).one()
    return dict(row._mapping)


def get_run(run_id: int) -> dict | None:
    """실행 기록 + 결과 + 전략서 소유자(user_id). 없으면 None.

    결과 행이 아직 없으면 지표 칸과 window_results 는 None 이다.
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT r.run_id, r.spec_id, s.user_id, r.period_start, r.period_end,
                       r.seed_money, r.fee_rate, r.tax_rate, r.slippage_bp,
                       r.random_seed, r.data_snapshot_asof, r.feature_set_version,
                       r.status, r.started_at,
                       m.cagr, m.mdd, m.sharpe, m.sortino, m.win_rate, m.benchmark_cagr,
                       m.window_results
                  FROM backtest_runs r
                  JOIN strategy_specs s ON s.spec_id = r.spec_id
                  LEFT JOIN backtest_metrics m ON m.run_id = r.run_id
                 WHERE r.run_id = :run_id
                """
            ),
            {"run_id": run_id},
        ).one_or_none()
    if row is None:
        return None
    data = dict(row._mapping)
    for key in ("seed_money", "fee_rate", "tax_rate", "slippage_bp", *_METRIC_FIELDS):
        if data[key] is not None:
            data[key] = float(data[key])
    return data


def mark_running(run_id: int, *, fee_rate: float, tax_rate: float, slippage_bp: float) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                UPDATE backtest_runs
                   SET status = :status, started_at = now(),
                       fee_rate = :fee_rate, tax_rate = :tax_rate, slippage_bp = :slippage_bp
                 WHERE run_id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "status": RUNNING,
                "fee_rate": fee_rate,
                "tax_rate": tax_rate,
                "slippage_bp": slippage_bp,
            },
        )


def finish_run(run_id: int, *, data_snapshot_asof: date | str, metrics: dict, window_results: dict) -> None:
    """done 으로 두고 결과를 한 트랜잭션으로 넣는다. metrics 에 없는 지표는 NULL."""
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE backtest_runs SET status = :status, data_snapshot_asof = :asof WHERE run_id = :run_id"
            ),
            {"run_id": run_id, "status": DONE, "asof": data_snapshot_asof},
        )
        _upsert_metrics(conn, run_id, metrics, window_results)


def fail_run(run_id: int, *, reason: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE backtest_runs SET status = :status WHERE run_id = :run_id"),
            {"run_id": run_id, "status": FAILED},
        )
        _upsert_metrics(conn, run_id, {}, {"error": reason})


def _upsert_metrics(conn, run_id: int, metrics: dict, window_results: dict) -> None:
    conn.execute(
        text(
            """
            INSERT INTO backtest_metrics
                (run_id, cagr, mdd, sharpe, sortino, win_rate, benchmark_cagr, window_results)
            VALUES (:run_id, :cagr, :mdd, :sharpe, :sortino, :win_rate, :benchmark_cagr,
                    CAST(:window_results AS jsonb))
            ON CONFLICT (run_id) DO UPDATE
               SET cagr = EXCLUDED.cagr, mdd = EXCLUDED.mdd, sharpe = EXCLUDED.sharpe,
                   sortino = EXCLUDED.sortino, win_rate = EXCLUDED.win_rate,
                   benchmark_cagr = EXCLUDED.benchmark_cagr, window_results = EXCLUDED.window_results
            """
        ),
        {
            "run_id": run_id,
            **{key: metrics.get(key) for key in _METRIC_FIELDS},
            "window_results": json.dumps(window_results, ensure_ascii=False, default=str),
        },
    )
