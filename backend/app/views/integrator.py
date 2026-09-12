# FN-407 신호 통합 — 세 관점의 점수를 시점별 가중치로 묶어 종목별 신호 하나를 만든다.
#
# 설명을 docstring이 아니라 주석에 쓰는 이유: scripts/check_asof_guard.py 가
# 문자열 리터럴(=docstring 포함)에서 가드 대상 테이블명을 찾으면 CI를 실패시킨다.
# 주석은 ast.parse 가 노드로 만들지 않으므로 안전하다. 현서가
# app/contracts/view_weights.py 상단에서 쓴 수법과 같다.
#
# 이 모듈은 DB를 모른다. 이미 조회된 가중치 dict를 인자로 받는 순수 함수다 —
# 조회(M3 T10 daily_judge)와 통합을 분리해야 가드 사정권 밖에 있고 postgres 없이
# 테스트가 된다. portfolio_id 를 받지 않는 것도 같은 이유다.
#
# 의존성은 stdlib(math) + pydantic 뿐이다. numpy 를 쓰지 않는다 —
# backend/pyproject.toml base dependencies 에 numpy 가 없고, 여기서 끌어들이면
# "검산표 회귀 테스트가 CI에서 항상 돈다"는 전제가 깨진다. 스칼라 tanh 하나라
# math.tanh 로 충분하다.
#
# 확정 알고리즘 (상세설계서 1.5.4, 2026-09-08 확정본 — 수치 변경 금지):
#     1) 가중합   m = Σ w_k × s_k
#     2) 스케일   s = tanh(m / 0.5)
#     3) 데드존   |s| < 0.10 이면 s = 0
#     4) (표시용 반올림)
#
# ★ 연산 순서를 바꾸지 마라. 반올림을 데드존보다 앞에 두면 검산표가 깨진다:
#   tanh(0.05 / 0.5) = 0.099668 인데 2자리로 먼저 반올림하면 0.10 이 되어
#   |s| < 0.10 이 False 가 된다. 검산표의 m = ±0.05 -> 0 칸이 그대로 뒤집힌다.
#   여유가 0.00033 뿐인 자리다. tests/test_integrator.py 가 이 순서를 고정한다.
#
# ViewScore.calibrated_prob 는 신호 통합에 쓰지 않는다. 그건 Hedge(FN-408)가
# Brier score 손실로 쓰는 값이다. 여기서 쓰는 것은 raw_score 뿐이다.
#
# IntegratedSignal(출력 형식)은 2026-09-12 팀 합의로 계약 ⑤가 되어
# app/contracts/integrated_signal.py 로 옮겼다. 여기 남은 것은 구현이다 —
# 계약은 형식이고 구현은 M3 소유라는 경계를 지킨다.
from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date

from app.contracts.integrated_signal import IntegratedSignal
from app.contracts.view_score import ViewScore

# 2026-09-08 확정본. 임의로 바꾸지 않는다.
TANH_SCALE = 0.5
DEADZONE = 0.10

# 가중치 합 판정 허용오차. 1/3 세 개를 더하면 부동소수 오차로 1.0 에서
# 1e-16 쯤 벗어나므로 그건 통과시키고, 의미 있는 어긋남만 잡는다.
WEIGHT_SUM_TOLERANCE = 1e-9

# 출력 반올림 자릿수. 데드존(0.10)보다 네 자리 아래라 판정에 영향을 주지 않는다.
# 반드시 데드존 적용 "뒤"에만 쓴다.
OUTPUT_PRECISION = 6


def _validate_weights(weights: dict[str, float]) -> None:
    # 합이 1이 아닌 가중치는 예외로 막는다. 조용히 정규화하지 않는다 —
    # tanh 스케일 0.5 와 데드존 0.10 은 Σw = 1 을 전제로 잡힌 수치라, 합이 어긋난
    # 채로 통과시키면 모든 종목의 신호 크기가 소리 없이 틀어지고 데드존 경계도
    # 함께 밀린다. 부분 집합(예: 두 관점만)으로 돌리려면 호출자가 정규화해서
    # 넘긴다 — 그래야 "누가 정규화했는가"가 기록에 남는다.
    if not weights:
        raise ValueError("관점 가중치가 비어 있다")

    negative = sorted(view_type for view_type, weight in weights.items() if weight < 0)
    if negative:
        raise ValueError(f"음수 가중치: {negative}")

    total = math.fsum(weights.values())
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise ValueError(
            f"가중치 합이 1이 아니다 (Sigma w = {total!r}). "
            "정규화는 호출자 책임이다 — 통합기는 조용히 고치지 않는다"
        )


def _collect(scores: list[ViewScore], weights: dict[str, float]) -> dict[str, dict[str, float]]:
    # view_type -> {ticker: raw_score} 로 뒤집으면서 관점/종목 커버리지를 검사한다.
    per_view: dict[str, dict[str, float]] = {}
    for score in scores:
        bucket = per_view.setdefault(score.view_type, {})
        if score.ticker in bucket:
            raise ValueError(f"같은 (관점, 종목) 점수가 두 번 들어왔다: {score.view_type}/{score.ticker}")
        bucket[score.ticker] = float(score.raw_score)

    unweighted = sorted(set(per_view) - set(weights))
    if unweighted:
        raise ValueError(f"가중치가 없는 관점의 점수가 들어왔다: {unweighted}")

    unscored = sorted(set(weights) - set(per_view))
    if unscored:
        # 스코어러는 데이터가 없어도 중립(raw_score 0.0)을 반환하게 되어 있다.
        # 아예 빠진 것은 스코어러 쪽 버그이거나 호출자가 가중치를 잘못 넘긴 것이다.
        raise ValueError(f"가중치는 있는데 점수가 하나도 없는 관점: {unscored}")

    tickers = {ticker for bucket in per_view.values() for ticker in bucket}
    for view_type in sorted(weights):
        absent = sorted(tickers - set(per_view[view_type]))
        if absent:
            raise ValueError(f"관점 '{view_type}' 이 점수를 안 낸 종목이 있다: {absent}")

    return per_view


def integrate(
    view_scores: Iterable[ViewScore],
    *,
    as_of: date,
    weights: dict[str, float],
) -> IntegratedSignal:
    # weights 는 이미 조회된 {관점: 가중치} 다 (T10 daily_judge 가 조회해서 넘긴다).
    # 같은 입력에 항상 같은 출력을 내기 위해 관점·종목을 정렬해 순회한다 —
    # 합산 순서가 바뀌면 부동소수 결과가 달라질 수 있다.
    scores = list(view_scores)
    _validate_weights(weights)
    per_view = _collect(scores, weights)

    view_types = sorted(weights)
    tickers = sorted({ticker for bucket in per_view.values() for ticker in bucket})

    signals: dict[str, float] = {}
    deadzone_applied: list[str] = []
    for ticker in tickers:
        # 1) 가중합
        weighted = math.fsum(weights[view_type] * per_view[view_type][ticker] for view_type in view_types)
        # 2) 스케일
        signal = math.tanh(weighted / TANH_SCALE)
        # 3) 데드존 — 반드시 반올림 전에
        if abs(signal) < DEADZONE:
            signal = 0.0
            deadzone_applied.append(ticker)
        # 4) 표시용 반올림
        signals[ticker] = round(signal, OUTPUT_PRECISION)

    return IntegratedSignal(
        as_of=as_of,
        signals=signals,
        view_weights_used={view_type: float(weights[view_type]) for view_type in view_types},
        per_view_scores={
            view_type: {ticker: round(per_view[view_type][ticker], OUTPUT_PRECISION) for ticker in tickers}
            for view_type in view_types
        },
        deadzone_applied=deadzone_applied,
    )
