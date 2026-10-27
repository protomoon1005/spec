# 전략서 승인 → 포트폴리오 활성화 (FN-409 초기 가중치 포함).
#
# 설명을 주석에 두는 이유는 app/scoring/live.py 와 같다 — scripts/check_asof_guard.py 가
# app/ 전체 문자열에서 가드 대상 테이블명을 잡는다. 가중치 기록은 live.init_view_weights
# (→ 저장소)로만 한다.
#
# ── 흐름 (한 트랜잭션, 결정 기록 D3) ───────────────────────────────────
# 전략서 행 잠금(FOR UPDATE) → 전제조건 → 승인 기록 → draft→approved → 활성 포트폴리오
# → FN-409 균등 1/3. 어느 단계에서 실패해도 아무것도 남지 않는다. 순차 실행 + 보상은
# 트리거가 approved→draft 를 막아 보상이 불가능하고, Celery 뒤처리는 실패하면 가중치 없는
# 활성 포트폴리오를 남긴다(둘 다 실측).
#
# ── 전제조건 (결정 기록 D2) ───────────────────────────────────────────
#   1 내 전략서인가                         아니면 404 (남의 것도 404 — 다른 라우터와 같다)
#   2 draft 인가                           아니면 409 (두 번 누른 승인도 여기서 끝난다)
#   3 마지막 검증이 4단까지 통과했는가       아니면 409 — 검증을 통과한 명세만 집행한다
#   4 run_id 가 내 실행인가                 아니면 404
#   5 그 실행이 이 전략서의 것인가           아니면 422
#   6 그 실행이 끝났는가(done)               아니면 409
#   7 그 실행이 지금 확정값으로 M2 체인을 돌았는가   아니면 409 — 실행 결과의 체인 입력
#     (window_results.m2: 범위 · 현금 목표 · 1회 손실 · 최소 간격 · 하드캡 버전)이 마지막 검증
#     기록에서 나온 확정값과 같아야 한다. "지금 확정값"의 정의는 백테스트와 같은 함수
#     (backtest/m2_chain.recorded_policy)가 낸다. 막혀서 러너 기본 경로로 돈 실행(m2 없음)과
#     값이 다른 옛 실행은 걸린다. 기록 없이 메모리 재검증으로 돈 실행은 검증기가 결정적이라
#     값이 같으므로 다시 돌릴 필요가 없다(2026-10-20 통합 때 범위만 보던 비교를 넓혔다).
#
# ── 시작 금액 · 모드 (결정 기록 D6) ───────────────────────────────────
# initial_cash 는 승인 근거가 된 실행의 seed_money 다. 사용자가 본 백테스트와 같은 금액으로
# 시작해야 "백테스트대로 굴린다"가 성립한다. 모드는 virtual(11월 데모 = 가상 체결까지).
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from app.backtest import m2_chain
from app.repositories import backtests, portfolios, specs
from app.scoring import live

NOT_FOUND = "not_found"
NOT_DRAFT = "not_draft"
NOT_VALIDATED = "not_validated"
RUN_NOT_FOUND = "run_not_found"
RUN_OTHER_SPEC = "run_other_spec"
RUN_NOT_DONE = "run_not_done"
RUN_STALE = "run_stale"


class ApprovalError(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class Approved:
    portfolio: dict
    initial_weights: dict[str, float]


# 전략서당 "승인함" 하나(db/migrate/002). 잠금(FOR UPDATE)이 먼저 막으므로 정상 흐름에서는 걸리지
# 않는다 — 걸리면 잠금이 우회된 것이고, 사용자에게는 두 번 누른 승인과 같은 409 로 보인다.
ONE_APPROVAL_INDEX = "uq_approvals_spec_approved"


def approve(spec_id: str, *, user_id: int, run_id: int, user_comment: str | None = None) -> Approved:
    try:
        return _approve(spec_id, user_id=user_id, run_id=run_id, user_comment=user_comment)
    except IntegrityError as exc:
        if ONE_APPROVAL_INDEX not in str(exc.orig):
            raise
        raise ApprovalError(NOT_DRAFT, "draft 전략서만 승인할 수 있다") from exc


def _approve(spec_id: str, *, user_id: int, run_id: int, user_comment: str | None) -> Approved:
    with portfolios.transaction() as conn:
        spec = portfolios.lock_spec(conn, spec_id)
        if spec is None or spec["user_id"] != user_id:
            raise ApprovalError(NOT_FOUND, "전략서를 찾을 수 없다")
        if spec["status"] != specs.DRAFT:
            raise ApprovalError(NOT_DRAFT, "draft 전략서만 승인할 수 있다")
        # 아래 읽기는 전부 잠금을 쥔 이 연결에서 한다 — 풀에서 따로 빌리면 동시 요청이 몰릴 때
        # 잠금 대기자들이 풀을 다 잡아 모두 30초 멈춘다(2026-10-21 리뷰, 20건 재현).
        current = _require_validation(spec_id, conn=conn)
        run = _require_run(spec_id, run_id=run_id, user_id=user_id, current=current, conn=conn)

        row = portfolios.activate(
            conn,
            spec_id=spec_id,
            user_id=user_id,
            run_id=run_id,
            user_comment=user_comment,
            initial_cash=run["seed_money"],
        )
        weights = live.init_view_weights(row["portfolio_id"], as_of=row["weights_as_of"], conn=conn)
    return Approved(portfolio=row, initial_weights=weights)


def _require_validation(spec_id: str, *, conn: Connection) -> m2_chain.ConfirmedPolicy:
    try:
        return m2_chain.recorded_policy(spec_id, conn=conn)
    except m2_chain.ChainError as exc:
        raise ApprovalError(NOT_VALIDATED, "검증을 4단까지 통과한 전략서만 승인할 수 있다") from exc


def _require_run(
    spec_id: str, *, run_id: int, user_id: int, current: m2_chain.ConfirmedPolicy, conn: Connection
) -> dict:
    run = backtests.get_run(run_id, conn=conn)
    if run is None or run["user_id"] != user_id:
        raise ApprovalError(RUN_NOT_FOUND, "실행 기록을 찾을 수 없다")
    if run["spec_id"] != spec_id:
        raise ApprovalError(RUN_OTHER_SPEC, "이 전략서로 돌린 실행이 아니다")
    if run["status"] != backtests.DONE:
        raise ApprovalError(RUN_NOT_DONE, "끝난 백테스트로만 승인할 수 있다")
    if not m2_chain.same_policy((run["window_results"] or {}).get("m2"), current):
        raise ApprovalError(RUN_STALE, "백테스트가 지금 확정값으로 돈 것이 아니다 — 다시 돌려야 한다")
    return run
