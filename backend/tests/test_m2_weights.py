"""M2 비중 산출 체인 — RiskSizer → WeightMapper → GroupCapEnforcer → OrderBuilder.

순수 함수 테스트라 DB 없이 돈다. 기준은 docs/m2-algorithms.md 2장(알고리즘)·3장(1.5.3 검산 예제)·
"RiskSizer v0.1" 소절이다. service 경로에서 두 번 돌려 같은지는 tests/test_backtest_api.py 가 본다.
"""

from __future__ import annotations

import math
import random
from datetime import date

import pytest

from app.contracts.target_weights import TargetWeights
from app.m2.orders import Order, build_orders
from app.m2.weights import (
    ATR_MULTIPLE,
    HORIZON_DAYS,
    AssetLimit,
    WeightChainError,
    WeightDecision,
    compute_target_weights,
    horizon_for,
    risk_caps,
)

AS_OF = date(2025, 6, 2)
TOL = 1e-9

# 1.5.3 검산 예제. 그룹은 문서 표 그대로 주입한다 — 실제 group_id 체계(EQUITY/COUNTRY_KR …)와 무관하게
# 알고리즘만 본다.
EXAMPLE_CAPS = {"국내주식형": 0.50, "해외주식형": 0.35, "채권형": 0.60}
EXAMPLE = [
    AssetLimit("069500", weight_min=0.10, weight_max=0.40, asset_group="국내주식형"),
    AssetLimit("232080", weight_min=0.05, weight_max=0.25, asset_group="국내주식형"),
    AssetLimit("133690", weight_min=0.10, weight_max=0.30, asset_group="해외주식형"),
    AssetLimit("148070", weight_min=0.15, weight_max=0.45, asset_group="채권형"),
]
EXAMPLE_SIGNALS = {"069500": 0.6, "232080": 0.8, "133690": 0.2, "148070": -0.4}


def _example() -> WeightDecision:
    return compute_target_weights(EXAMPLE, EXAMPLE_SIGNALS, as_of=AS_OF, cash_min=0.05, caps=EXAMPLE_CAPS)


def _r4(weights: dict[str, float]) -> list[float]:
    return [round(weights[a.ticker], 4) for a in EXAMPLE]


# --- 1. 1.5.3 검산 예제 -------------------------------------------------------


def test_검산예제_선형매핑():
    d = _example()
    assert _r4(d.linear) == [0.3400, 0.2300, 0.2200, 0.2400]
    assert round(sum(d.linear.values()), 4) == 1.0300


def test_검산예제_정규화_클램프():
    d = _example()
    assert _r4(d.target.mapped_weights) == [0.3136, 0.2121, 0.2029, 0.2214]
    assert round(sum(d.target.mapped_weights.values()), 4) == 0.9500
    # 4 반복 — 범위 밖으로 밀려난 종목이 없어 1회로 끝난다
    assert d.rounds == 1
    assert d.normalize_fix is None


def test_검산예제_그룹캡_최종():
    d = _example()
    assert _r4(d.target.weights) == [0.2982, 0.2018, 0.2029, 0.2214]
    assert round(sum(d.target.weights.values()), 4) == 0.9243
    assert round(d.target.cash, 4) == 0.0757

    [app] = d.target.group_cap_applications
    assert app.group_id == "국내주식형"
    assert round(app.sum_before, 4) == 0.5257
    assert app.cap == 0.50
    assert round(app.scale_factor, 5) == 0.95106


# --- 2. RiskSizer -------------------------------------------------------------


def test_리스크상한_공식_v01():
    # docs/m2-algorithms.md "RiskSizer v0.1": L / (k × ATR% × √h)
    assert ATR_MULTIPLE == 2
    assert HORIZON_DAYS == {"monthly": 21, "weekly": 5}
    caps = risk_caps({"A": 0.02, "B": None, "C": 0.0}, loss_limit=0.05, horizon_days=21)
    assert caps["A"] == pytest.approx(0.05 / (2 * 0.02 * math.sqrt(21)))
    # ATR 이 없거나 0 이하면 상한을 두지 않는다
    assert caps["B"] is None
    assert caps["C"] is None


def test_보유기간은_리밸런싱_규칙에서():
    assert horizon_for({"trigger": {"type": "calendar", "freq": "monthly", "day": 1}}) == 21
    assert horizon_for({"trigger": {"type": "calendar", "freq": "weekly", "day": 1}}) == 5
    with pytest.raises(WeightChainError):
        horizon_for({"trigger": {"type": "band"}})


def test_리스크상한이_weight_max보다_작으면_매핑상한이_된다():
    # 069500 상한 0.40 → 0.12. 신호 +1 이면 상한에 붙는다.
    signals = dict(EXAMPLE_SIGNALS, **{"069500": 1.0})
    d = compute_target_weights(
        EXAMPLE, signals, as_of=AS_OF, cash_min=0.05, caps=EXAMPLE_CAPS,
        risk_caps={"069500": 0.12, "232080": None, "133690": 0.90, "148070": None},
    )
    assert d.bounds["069500"] == (0.10, 0.12)
    assert d.linear["069500"] == pytest.approx(0.12)
    assert d.target.weights["069500"] <= 0.12 + TOL
    # weight_max 보다 큰 리스크 상한은 아무 일도 하지 않는다
    assert d.bounds["133690"] == (0.10, 0.30)


def test_리스크상한이_weight_min보다_작으면_하한이_내려간다():
    d = compute_target_weights(
        EXAMPLE, EXAMPLE_SIGNALS, as_of=AS_OF, cash_min=0.05, caps=EXAMPLE_CAPS,
        risk_caps={"148070": 0.08},
    )
    assert d.bounds["148070"] == (0.08, 0.08)
    assert d.target.weights["148070"] == pytest.approx(0.08)


# --- 3. 경계 ------------------------------------------------------------------


def test_신호_전부_마이너스1이면_weight_min_비율로_채운다():
    # 문서 2장 정규화는 양방향이다(2026-10-04 확인) — Σmin 0.40 을 target 0.95 까지 키운다.
    signals = {a.ticker: -1.0 for a in EXAMPLE}
    d = compute_target_weights(EXAMPLE, signals, as_of=AS_OF, cash_min=0.05, caps={})
    assert d.linear == {a.ticker: a.weight_min for a in EXAMPLE}
    scale = 0.95 / 0.40
    for a in EXAMPLE:
        assert d.target.weights[a.ticker] == pytest.approx(a.weight_min * scale)
        assert d.target.weights[a.ticker] >= a.weight_min
    assert d.target.cash == pytest.approx(0.05)


def test_신호_전부_플러스1이면_상한과_그룹캡에_걸린다():
    # Σmax 0.90 < 0.95 라 정규화가 키워도 클램프가 상한으로 되돌린다 → 전 종목 상한.
    # 그다음 G 묶음 0.60 이 캡 0.50 에 걸린다.
    assets = [
        AssetLimit("A", weight_min=0.05, weight_max=0.30, asset_group="G"),
        AssetLimit("B", weight_min=0.00, weight_max=0.30, asset_group="G"),
        AssetLimit("C", weight_min=0.10, weight_max=0.30, asset_group="H"),
    ]
    signals = {a.ticker: 1.0 for a in assets}
    d = compute_target_weights(assets, signals, as_of=AS_OF, cash_min=0.05, caps={"G": 0.50, "H": 0.60})
    assert d.linear == {"A": 0.30, "B": 0.30, "C": 0.30}
    assert d.target.mapped_weights == pytest.approx({"A": 0.30, "B": 0.30, "C": 0.30})
    [app] = d.target.group_cap_applications
    assert app.group_id == "G"
    assert d.target.weights == pytest.approx({"A": 0.25, "B": 0.25, "C": 0.30})
    assert d.target.cash == pytest.approx(0.20)


def test_그룹캡_축소후에도_weight_min_이상():
    # 비례 축소만 하면 B 가 0.20 × (0.30/0.60) = 0.10 으로 하한 0.18 아래로 떨어진다.
    assets = [
        AssetLimit("A", weight_min=0.00, weight_max=0.40, asset_group="G"),
        AssetLimit("B", weight_min=0.18, weight_max=0.40, asset_group="G"),
        AssetLimit("C", weight_min=0.00, weight_max=0.60, asset_group="H"),
    ]
    signals = {"A": 1.0, "B": -0.2, "C": 0.0}
    d = compute_target_weights(assets, signals, as_of=AS_OF, cash_min=0.05, caps={"G": 0.30})
    w = d.target.weights
    assert w["B"] >= 0.18 - TOL
    assert w["A"] + w["B"] == pytest.approx(0.30)
    assert w["A"] >= 0.0


def test_현금은_항상_cash_min_이상_무작위():
    rng = random.Random(20261004)
    groups = ["G1", "G2", "G3"]
    for _ in range(500):
        n = rng.randint(2, 8)
        cash_min = rng.choice([0.05, 0.10, 0.15, 0.20])
        assets = []
        sum_min = 0.0
        for i in range(n):
            hi = round(rng.uniform(0.05, 0.40), 4)
            lo = round(rng.uniform(0.0, hi), 4)
            # 실행가능성 불변식(1장)은 Validator 가 이미 보장한 입력이다
            if sum_min + lo > 1 - cash_min:
                lo = 0.0
            sum_min += lo
            assets.append(AssetLimit(f"T{i}", weight_min=lo, weight_max=hi, asset_group=rng.choice(groups)))
        signals = {a.ticker: rng.uniform(-1, 1) for a in assets}
        caps = {g: round(rng.uniform(0.2, 0.8), 2) for g in groups}
        rcaps = {a.ticker: rng.choice([None, rng.uniform(0.01, 0.5)]) for a in assets}
        d = compute_target_weights(
            assets, signals, as_of=AS_OF, cash_min=cash_min, caps=caps, risk_caps=rcaps
        )
        assert d.target.cash >= cash_min - TOL
        assert sum(d.target.weights.values()) + d.target.cash == pytest.approx(1.0)
        for a in assets:
            lo, hi = d.bounds[a.ticker]
            assert lo - TOL <= d.target.weights[a.ticker] <= hi + TOL


def test_3회안에_수렴하지_않으면_최종보정으로_현금하한을_지킨다():
    # 하한이 큰 종목이 클램프로 계속 끌어올려져 3회 뒤에도 Σw > 0.85 가 남는 입력 (2장 보완 규칙)
    assets = [
        AssetLimit("A", weight_min=0.30, weight_max=0.31),
        AssetLimit("B", weight_min=0.25, weight_max=0.26),
        AssetLimit("C", weight_min=0.00, weight_max=0.40),
        AssetLimit("D", weight_min=0.00, weight_max=0.40),
    ]
    signals = {"A": -1.0, "B": -1.0, "C": 1.0, "D": 1.0}
    d = compute_target_weights(assets, signals, as_of=AS_OF, cash_min=0.15, caps={})
    assert d.rounds == 3
    assert d.normalize_fix is not None
    assert d.target.cash == pytest.approx(0.15)
    assert d.target.weights["A"] >= 0.30 - TOL and d.target.weights["B"] >= 0.25 - TOL


def test_실행가능성이_깨진_입력은_거부한다():
    assets = [
        AssetLimit("A", weight_min=0.60, weight_max=0.70),
        AssetLimit("B", weight_min=0.50, weight_max=0.60),
    ]
    with pytest.raises(WeightChainError):
        compute_target_weights(assets, {"A": 0.0, "B": 0.0}, as_of=AS_OF, cash_min=0.05, caps={})


# --- 4. 계약 ④ ----------------------------------------------------------------


def test_출력이_계약4로_검증된다():
    d = _example()
    assert isinstance(d.target, TargetWeights)
    again = TargetWeights.model_validate(d.target.model_dump())
    assert again == d.target


# --- 5. 재현성 · OrderBuilder --------------------------------------------------


def test_같은_입력_두번이면_비중과_주문이_같다():
    first, second = _example(), _example()
    assert first.target == second.target
    assert build_orders({}, first.target) == build_orders({}, second.target)


def test_주문은_매도먼저_종목순():
    target = _example().target
    prev = {"069500": 0.40, "232080": 0.10, "133690": target.weights["133690"], "148070": 0.10}
    orders = build_orders(prev, target)
    assert [o.side for o in orders] == ["sell", "buy", "buy"]
    assert [o.ticker for o in orders] == ["069500", "148070", "232080"]
    # 133690 은 변화가 없어 주문이 없다
    assert all(isinstance(o, Order) for o in orders)
    assert orders[0].delta_weight == pytest.approx(0.2982 - 0.40, abs=1e-4)


def test_주문_최소거래폭_미만은_생략():
    prev = {"069500": 0.30, "232080": 0.20, "133690": 0.20, "148070": 0.22}
    orders = build_orders(prev, _example().target, min_trade=0.005)
    assert orders == ()


def test_목표에서_빠진_종목은_전량_매도():
    orders = build_orders({"999999": 0.10}, _example().target)
    assert orders[0] == Order(ticker="999999", side="sell", delta_weight=-0.10)
