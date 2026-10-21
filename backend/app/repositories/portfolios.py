"""승인(approvals) · 포트폴리오(portfolios) 읽기·쓰기. docs/db-erd.md 4.1 이 정본이다.

시점 규약 대상이 아니다 — 승인 기록과 운용 단위이지 시점 자산이 아니다.

## 승인 한 번 = 한 트랜잭션

승인 기록 → 전략서 draft→approved → 포트폴리오(활성) → FN-409 초기 가중치가 전부 한
트랜잭션이다(조립은 app/m2/approval.py). 그래서 쓰기 함수는 연결(conn)을 받고 커밋하지
않는다. transaction() 이 연 연결을 넘겨 쓴다.

순차 실행 뒤 보상으로는 못 되돌린다 — 트리거가 approved → draft 되돌리기를 막으므로
draft 로 되돌리는 보상 자체가 거부된다(2026-10-20 실측, 결정 기록 D3).

## 동시 승인

lock_spec 이 전략서 행을 SELECT … FOR UPDATE 로 잡는다. 같은 전략서를 동시에 승인하면
뒤 요청은 앞 요청이 끝날 때까지 기다렸다가 approved 를 보고 409 로 끝난다. 검증 기록
(specs.record_validation)도 같은 행 잠금을 쓰므로 승인과 재검증이 엇갈리지 않는다.

## 상태 값

portfolios.status 는 표에 CHECK 가 없어 여기 상수로 정한다. 지금 쓰는 값은 active 하나다.
전략서 상태는 승인 뒤 approved → running → closed 로 한 칸씩만 간다(db/migrate/003 트리거).
승인 API 는 approved 까지만 올리고, 지금 "운용 중"은 이 표의 status(active)로 본다.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

from sqlalchemy import Connection, text

from app.core.db import get_engine

ACTIVE = "active"
# 11월 데모 범위는 가상 체결까지(docs/m2-algorithms.md 7장). 모의계좌(paper)는 12월 이후.
VIRTUAL = "virtual"
# 활성화일(KST). DB 세션 시간대는 UTC 라 KST 00:00~09:00 에 승인하면 날짜가 하루 어긋난다.
KST = "Asia/Seoul"


@contextmanager
def transaction() -> Iterator[Connection]:
    with get_engine().begin() as conn:
        yield conn


def lock_spec(conn: Connection, spec_id: str) -> dict | None:
    """전략서 행을 잠그고 (user_id, status) 를 돌려준다. 없으면 None."""
    row = conn.execute(
        text("SELECT spec_id, user_id, status FROM strategy_specs WHERE spec_id = :s FOR UPDATE"),
        {"s": spec_id},
    ).one_or_none()
    return dict(row._mapping) if row is not None else None


def activate(
    conn: Connection,
    *,
    spec_id: str,
    user_id: int,
    run_id: int,
    user_comment: str | None,
    initial_cash: float,
) -> dict:
    """승인 기록 · 전략서 approved · 활성 포트폴리오를 넣는다. 커밋은 부르는 쪽이 한다.

    decided_at · activated_at 은 같은 now()(트랜잭션 시작 시각)다. weights_as_of 는
    그 시각의 KST 날짜 — FN-409 초기 가중치를 이 날짜로 기록한다.
    lock_spec 으로 잠근 draft 전략서에만 부른다.
    """
    approval = conn.execute(
        text(
            """
            INSERT INTO approvals (spec_id, user_id, run_id, approved, user_comment, decided_at)
            VALUES (:spec_id, :user_id, :run_id, true, :user_comment, now())
            RETURNING approval_id, decided_at
            """
        ),
        {"spec_id": spec_id, "user_id": user_id, "run_id": run_id, "user_comment": user_comment},
    ).one()
    conn.execute(
        text("UPDATE strategy_specs SET status = 'approved' WHERE spec_id = :s AND status = 'draft'"),
        {"s": spec_id},
    )
    portfolio = conn.execute(
        text(
            """
            INSERT INTO portfolios (spec_id, account_no, mode, initial_cash, status, activated_at)
            VALUES (:spec_id, NULL, :mode, :initial_cash, :status, now())
            RETURNING portfolio_id, activated_at,
                      (activated_at AT TIME ZONE :tz)::date AS weights_as_of
            """
        ),
        {"spec_id": spec_id, "mode": VIRTUAL, "initial_cash": initial_cash, "status": ACTIVE, "tz": KST},
    ).one()
    return {
        "portfolio_id": portfolio.portfolio_id,
        "spec_id": spec_id,
        "account_no": None,
        "mode": VIRTUAL,
        "initial_cash": float(initial_cash),
        "status": ACTIVE,
        "activated_at": portfolio.activated_at,
        "weights_as_of": portfolio.weights_as_of,
        "approval_id": approval.approval_id,
        "run_id": run_id,
        "user_comment": user_comment,
        "decided_at": approval.decided_at,
    }


def list_active() -> list[int]:
    """활성 포트폴리오 번호, 오래된 것부터. 매일 라이브 루프(app/scoring/live.run_live_day)가 돈다."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT portfolio_id FROM portfolios WHERE status = :s ORDER BY portfolio_id"), {"s": ACTIVE}
        ).all()
    return [row.portfolio_id for row in rows]


def activation_date(portfolio_id: int) -> date | None:
    """활성화일(KST). FN-409 초기 가중치가 이 날짜로 남는다. 활성화 시각이 없으면 None."""
    with get_engine().connect() as conn:
        return conn.execute(
            text("SELECT (activated_at AT TIME ZONE :tz)::date FROM portfolios WHERE portfolio_id = :pid"),
            {"pid": portfolio_id, "tz": KST},
        ).scalar_one_or_none()


def get_portfolio(portfolio_id: int) -> dict | None:
    """포트폴리오 + 전략서 소유자(user_id) + 승인 기록. 없으면 None.

    승인과 포트폴리오는 FK 없이 spec_id 로만 이어진다(ERD 는 1:1). 전략서 하나는
    draft→approved 를 한 번만 거치므로 approved=true 인 승인은 많아야 하나다.
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT p.portfolio_id, p.spec_id, s.user_id, p.account_no, p.mode, p.initial_cash,
                       p.status, p.activated_at,
                       a.approval_id, a.run_id, a.user_comment, a.decided_at
                  FROM portfolios p
                  JOIN strategy_specs s ON s.spec_id = p.spec_id
                  LEFT JOIN approvals a ON a.spec_id = p.spec_id AND a.approved
                 WHERE p.portfolio_id = :pid
                 ORDER BY a.decided_at DESC NULLS LAST
                 LIMIT 1
                """
            ),
            {"pid": portfolio_id},
        ).one_or_none()
    if row is None:
        return None
    data = dict(row._mapping)
    if data["initial_cash"] is not None:
        data["initial_cash"] = float(data["initial_cash"])
    return data
