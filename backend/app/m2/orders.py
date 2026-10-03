# OrderBuilder (FN-504) — 데모 범위.
#
# 현재 비중과 목표 비중의 차이를 주문으로 만든다. 백테스트에서는 vectorbt 가 가상 체결(소수 수량)을
# 하므로 수량 환산·단수 잔여 처리는 하지 않는다. 여기 주문은 "같은 입력 2회 → 주문 동일" 재현성
# 판정에 쓰는 비중 단위 주문이다. 실운용 연결(수량·OrderExecutor)은 데모 후.
#
# 최소 거래 폭: 문서(FN-504)에 "미달은 생략"만 있고 수치가 없다. 기본값 0.0 이고 값은 미정
# (docs/known-issues.md).
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from app.contracts.target_weights import TargetWeights

# 부동소수 잡음을 주문으로 만들지 않는 여유
TOLERANCE = 1e-9


@dataclass(frozen=True)
class Order:
    ticker: str
    side: Literal["buy", "sell"]
    delta_weight: float  # 목표 − 현재. 매도는 음수


def build_orders(
    current: Mapping[str, float], target: TargetWeights, *, min_trade: float = 0.0
) -> tuple[Order, ...]:
    """현재 비중 → 목표 비중 주문. 매도 먼저(현금 확보), 같은 쪽은 종목코드 순."""
    orders: list[Order] = []
    for ticker in sorted({*current, *target.weights}):
        delta = target.weights.get(ticker, 0.0) - current.get(ticker, 0.0)
        if abs(delta) <= TOLERANCE or abs(delta) < min_trade:
            continue
        orders.append(Order(ticker=ticker, side="buy" if delta > 0 else "sell", delta_weight=delta))
    orders.sort(key=lambda o: (o.side != "sell", o.ticker))
    return tuple(orders)
