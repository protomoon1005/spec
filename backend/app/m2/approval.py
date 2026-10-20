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
#   7 그 실행이 지금 확정 범위로 돌았는가     아니면 409 — 실행 결과에 남은 종목별 확정
#     범위가 지금 spec_universe 와 같아야 하고, 결과에 검증 요약(validation)이 있으면 그
#     status 가 passed 여야 한다. 검증 전에 돌린 실행은 범위가 달라 걸린다. 재검증은 범위가
#     같으므로(검증기는 멱등) 다시 돌릴 필요가 없다.
#
# ── 시작 금액 · 모드 (결정 기록 D6) ───────────────────────────────────
# initial_cash 는 승인 근거가 된 실행의 seed_money 다. 사용자가 본 백테스트와 같은 금액으로
# 시작해야 "백테스트대로 굴린다"가 성립한다. 모드는 virtual(11월 데모 = 가상 체결까지).
from __future__ import annotations

import math
from dataclasses import dataclass

from app.repositories import backtests, portfolios, specs
from app.scoring import live

# 실행 결과의 확정 범위와 지금 범위를 비교할 때의 허용오차. JSON 왕복으로 생기는 오차만 본다.
BOUND_TOLERANCE = 1e-9

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


def approve(spec_id: str, *, user_id: int, run_id: int, user_comment: str | None = None) -> Approved:
    with portfolios.transaction() as conn:
        spec = portfolios.lock_spec(conn, spec_id)
        if spec is None or spec["user_id"] != user_id:
            raise ApprovalError(NOT_FOUND, "전략서를 찾을 수 없다")
        if spec["status"] != specs.DRAFT:
            raise ApprovalError(NOT_DRAFT, "draft 전략서만 승인할 수 있다")
        _require_validation(spec_id)
        run = _require_run(spec_id, run_id=run_id, user_id=user_id)

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


def _require_validation(spec_id: str) -> None:
    # 본 트리에서 specs.get_latest_validation 으로 이름이 통일될 수 있다.
    logs = specs.latest_validation(spec_id)
    if {row["stage"] for row in logs} != {1, 2, 3, 4} or not all(row["passed"] for row in logs):
        raise ApprovalError(NOT_VALIDATED, "검증을 4단까지 통과한 전략서만 승인할 수 있다")


def _require_run(spec_id: str, *, run_id: int, user_id: int) -> dict:
    run = backtests.get_run(run_id)
    if run is None or run["user_id"] != user_id:
        raise ApprovalError(RUN_NOT_FOUND, "실행 기록을 찾을 수 없다")
    if run["spec_id"] != spec_id:
        raise ApprovalError(RUN_OTHER_SPEC, "이 전략서로 돌린 실행이 아니다")
    if run["status"] != backtests.DONE:
        raise ApprovalError(RUN_NOT_DONE, "끝난 백테스트로만 승인할 수 있다")
    results = run["window_results"] or {}
    summary = results.get("validation")
    if summary is not None and summary.get("status") != "passed":
        raise ApprovalError(RUN_STALE, "검증 확정값으로 돌린 백테스트가 아니다 — 다시 돌려야 한다")
    if not _same_bounds(results.get("universe") or [], specs.get_spec_universe(spec_id)):
        raise ApprovalError(RUN_STALE, "백테스트가 지금 확정 범위로 돈 것이 아니다 — 다시 돌려야 한다")
    return run


def _same_bounds(used: list[dict], current: list[dict]) -> bool:
    def table(rows: list[dict]) -> dict[str, tuple]:
        return {r["ticker"]: (r.get("weight_min"), r.get("weight_max")) for r in rows}

    a, b = table(used), table(current)
    if set(a) != set(b):
        return False
    for ticker, pair in b.items():
        for x, y in zip(a[ticker], pair, strict=True):
            if x is None or y is None or not math.isclose(float(x), float(y), abs_tol=BOUND_TOLERANCE):
                return False
    return True
