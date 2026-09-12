# FN-408 관점 가중치 갱신(Hedge) · FN-409 초기 가중치 — 순수 함수.
#
# 설명을 docstring이 아니라 주석에 두는 이유는 integrator.py 와 같다:
# scripts/check_asof_guard.py 가 문자열 리터럴(docstring 포함)에서 가드 대상
# 테이블명을 찾으면 CI가 실패한다. 주석은 AST에 안 잡힌다.
#
# 이 모듈은 DB를 모른다. 손실 이력을 받아 가중치 dict를 돌려줄 뿐이고,
# 쓰기는 app/repositories/ 의 insert 함수가 한다. 의존성은 stdlib(math) 뿐이다 —
# numpy 도 쓰지 않는다. 그래야 수렴 회귀 테스트가 ML 의존성 없이 CI에서 항상 돈다.
#
# ── 롤링 재계산 (2026-09-12 확정 해석) ────────────────────────────────
# 평가 윈도우 60거래일을 "상태를 이어받아 매일 곱한다"가 아니라 "매 거래일
# 1/3 균등에서 다시 시작해 최근 60거래일 손실을 다시 적용한다"로 읽는다.
# 상태가 없으니 as_of 만 있으면 언제든 같은 값이 나오고, 백테스트를 중간부터
# 재실행해도 처음부터 돌린 것과 일치한다 — 재현성이 공짜로 따라온다.
#
# 곱셈 갱신은 지수가 합으로 접힌다:
#     Π_t exp(−η·L_k,t) = exp(−η · Σ_t L_k,t)
# 그래서 일자별 루프가 필요 없고 관점별 손실 합 S_k 하나면 된다.
#
# FN-409 초기 가중치(1/3 균등)에 별도 분기가 필요 없는 것도 같은 이유다 —
# 손실 이력이 비면 S_k 가 전부 0이라 exp(0) = 1 로 균등이 그대로 나온다.
#
# ── 지수에서 min S 를 빼는 이유 ──────────────────────────────────────
# 손실은 Brier score라 0..1 이고 윈도우가 60이므로 S_k 는 최대 60까지 간다.
# exp(−0.5 × 60) = 9.4e-14 로 float64 가 언더플로하지는 않지만 유효숫자를 크게
# 잃고, 윈도우나 η 가 커지면 실제로 0으로 떨어져 정규화에서 0 나눗셈이 난다.
# 모든 지수에서 min S 를 빼면 가장 좋은 관점이 exp(0) = 1 이 되고, 공통 인수
# exp(η·min S) 는 정규화에서 약분되므로 결과는 수학적으로 동일하다.
#
# ── 하한은 마지막에 한 번, water-filling ─────────────────────────────
# 상세설계서 1.5.5 의사코드의 "클립 후 재정규화" 순서를 그대로 쓰면 하한이
# 깨진다. 반례: w = (0.95, 0.03, 0.02) 를 0.10 으로 클립하면 (0.95, 0.10, 0.10)
# 이고 합이 1.15 라 재정규화하면 (0.826, 0.087, 0.087) 로 둘이 다시 0.10 밑이다.
# water-filling 은 하한에 걸린 관점을 하한에 고정하고 남은 몫만 나머지에 비례
# 배분하는 것을 더 걸리는 관점이 없을 때까지 반복한다 → (0.80, 0.10, 0.10).
# tests/test_hedge.py 가 이 반례를 직접 재현해 순서를 고정한다.
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from app.contracts.view_weights import WEIGHT_FLOOR

# 2026-09-08 확정본. 임의로 바꾸지 않는다.
ETA = 0.5
WINDOW_TRADING_DAYS = 60

# 합이 1인지 볼 때의 허용오차. integrate() 가 읽을 때 쓰는 값과 같다.
WEIGHT_SUM_TOLERANCE = 1e-9

# 해석 방식(rolling)이 문자열에 드러나야 한다 — 나중에 이 행이 어떤 규칙으로
# 계산된 값인지 DB만 보고 알 수 있어야 하기 때문이다.
UPDATE_RULE_VERSION = "hedge-rolling-v0.1-eta0.5-floor0.10-w60"


def brier_loss(prob: float, outcome: int) -> float:
    # L_k = (p_k − y)². p 는 isotonic 보정 후 상승 확률, y 는 실제 방향(상승 1).
    if not 0.0 <= prob <= 1.0:
        raise ValueError(f"확률은 [0,1] 이어야 한다: {prob!r}")
    if outcome not in (0, 1):
        raise ValueError(f"실제 방향은 0 또는 1 이어야 한다: {outcome!r}")
    return (prob - outcome) ** 2


def apply_floor(weights: Mapping[str, float], *, floor: float = WEIGHT_FLOOR) -> dict[str, float]:
    # water-filling. 하한에 걸린 관점을 하한으로 고정하고 남은 몫
    # (1 − 고정개수 × floor)을 걸리지 않은 관점들에 원래 비율대로 배분한다.
    # 그 결과 새로 걸리는 관점이 생기면 다시 고정하고 반복한다.
    names = sorted(weights)
    if not names:
        raise ValueError("가중치가 비어 있다")
    if floor < 0:
        raise ValueError(f"하한이 음수다: {floor!r}")
    if floor * len(names) > 1.0 + WEIGHT_SUM_TOLERANCE:
        raise ValueError(
            f"하한 {floor} × 관점 {len(names)}개 = {floor * len(names)} 라 합 1을 만족할 수 없다"
        )

    negative = sorted(name for name in names if weights[name] < 0)
    if negative:
        raise ValueError(f"음수 가중치: {negative}")

    total = math.fsum(weights[name] for name in names)
    if total <= 0:
        raise ValueError("가중치 합이 0 이하라 정규화할 수 없다")
    base = {name: weights[name] / total for name in names}

    pinned: set[str] = set()
    while True:
        free = [name for name in names if name not in pinned]
        if not free:
            # floor × n == 1 인 경계. 전부 하한이다.
            return {name: floor for name in names}

        remaining = 1.0 - floor * len(pinned)
        free_base = math.fsum(base[name] for name in free)
        if free_base <= 0:
            # 남은 관점들의 원래 비중이 전부 0이면 비례 배분할 근거가 없다. 균등.
            scaled = {name: remaining / len(free) for name in free}
        else:
            scaled = {name: remaining * base[name] / free_base for name in free}

        newly = [name for name in free if scaled[name] < floor - WEIGHT_SUM_TOLERANCE]
        if not newly:
            result = {name: floor for name in pinned}
            result.update(scaled)
            return {name: result[name] for name in names}
        pinned.update(newly)


def rolling_weights(
    losses: Mapping[str, Sequence[float]],
    *,
    eta: float = ETA,
    floor: float = WEIGHT_FLOOR,
    window: int = WINDOW_TRADING_DAYS,
) -> dict[str, float]:
    # losses: {관점: 시간순(오래된 것 -> 최근) 손실 목록}. 뒤에서 window 개만 본다.
    # 호출자가 "전날까지 확정된 실현치"만 담아 넘긴다 — 어느 날짜까지 담을지는
    # 이 함수가 아니라 호출자(Celery update_view_weights)의 책임이다.
    names = sorted(losses)
    if not names:
        raise ValueError("손실 이력이 비어 있다 (관점이 하나도 없다)")
    if window <= 0:
        raise ValueError(f"윈도우는 1 이상이어야 한다: {window!r}")

    lengths = {len(losses[name]) for name in names}
    if len(lengths) != 1:
        # 관점마다 이력 길이가 다르면 같은 기간을 채점한 게 아니라 비교가 불공정하다.
        raise ValueError(f"관점별 손실 이력 길이가 다르다: { {n: len(losses[n]) for n in names} }")

    sums: dict[str, float] = {}
    for name in names:
        recent = list(losses[name])[-window:]
        for value in recent:
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"손실은 [0,1](Brier) 이어야 한다: {name}={value!r}")
        sums[name] = math.fsum(recent)

    shift = min(sums.values())
    prior = 1.0 / len(names)  # FN-409 초기 균등. 정규화에서 약분되지만 명시해 둔다.
    raw = {name: prior * math.exp(-eta * (sums[name] - shift)) for name in names}

    total = math.fsum(raw.values())
    normalized = {name: raw[name] / total for name in names}
    return apply_floor(normalized, floor=floor)
