"""재서술 스냅샷 테스트 3개.

기대 문장은 tests/snapshots/narrate_*.txt 에 있다. 템플릿을 의도적으로 바꿨다면
  python -m tests.test_narrate --update
로 다시 쓰고, diff 를 사람이 읽고 확인한 뒤 커밋한다.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.narrate import narrate  # noqa: E402
from core.validate import validate  # noqa: E402

SNAP_DIR = Path(__file__).parent / "snapshots"

CASES = {
    # B2/B6 예시 그대로: 교차(cross_below) 조건, 종목·기간·비용 전부 기본값
    "single_cross_defaults": {
        "universe": [{"ticker": "005930", "name": "삼성전자", "weight": 1.0}],
        "entry": {"logic": "all", "conditions": [{"left": "RSI_14", "op": "cross_below", "right": 30}]},
        "exit": {"logic": "any", "conditions": [{"left": "CLOSE", "op": "cross_below", "right": "SMA_5"}]},
        "provenance": {"input_text": "RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘",
                       "defaults_applied": ["universe", "backtest", "costs"]},
    },
    # 반반(주식+ETF 세율 차이), 상태(lt) 조건, 손절·익절·보유기간
    "half_stock_etf_stops": {
        "universe": [{"ticker": "005930", "name": "삼성전자", "weight": 0.5},
                     {"ticker": "069500", "name": "KODEX 200", "weight": 0.5}],
        "entry": {"logic": "all", "conditions": [{"left": "RSI_14", "op": "lt", "right": 30}]},
        "exit": {"logic": "any", "conditions": [], "stop_loss": 0.05, "take_profit": 0.1, "max_hold_days": 20},
        "backtest": {"start": "2022-01-01", "end": "2024-12-31", "initial_cash": 50_000_000},
        "provenance": {"input_text": "삼성전자랑 KODEX 200 반반, RSI 30 아래면 사고 5% 떨어지면 손절, "
                                     "10% 오르면 익절, 20일 지나면 팔아",
                       "defaults_applied": ["costs"]},
    },
    # 골든크로스 기본 기간 표시, 조건 여러 개(all/any), 거래량 배수, 비중 일부 현금
    "multi_conditions_partial_weight": {
        "universe": [{"ticker": "000660", "name": "SK하이닉스", "weight": 0.7}],
        "entry": {"logic": "all", "conditions": [
            {"left": "SMA_5", "op": "cross_above", "right": "SMA_20"},
            {"left": "VOLUME", "op": "gt", "right": {"ref": "VOL_SMA_20", "mul": 2}}]},
        "exit": {"logic": "any", "conditions": [
            {"left": "CLOSE", "op": "cross_below", "right": "SMA_20"},
            {"left": "RSI_14", "op": "gt", "right": 70}], "take_profit": 0.15},
        "backtest": {"start": "2021-01-01", "end": "2025-12-31", "initial_cash": 30_000_000},
        "provenance": {"input_text": "하이닉스에 70%만, 골든크로스에 거래량 터지면 사고 "
                                     "20일선 깨지거나 RSI 70 위면 팔아, 15% 오르면 익절",
                       "defaults_applied": ["entry.conditions[0]", "backtest"]},
    },
}


def render(name: str) -> str:
    report = validate(CASES[name])
    assert report.ok, [str(i) for i in report.issues]
    return narrate(report.spec).text + "\n"


@pytest.mark.parametrize("name", list(CASES))
def test_narration_snapshot(name):
    expected = (SNAP_DIR / f"narrate_{name}.txt").read_text(encoding="utf-8")
    assert render(name) == expected


if __name__ == "__main__" and "--update" in sys.argv:
    SNAP_DIR.mkdir(exist_ok=True)
    for n in CASES:
        (SNAP_DIR / f"narrate_{n}.txt").write_text(render(n), encoding="utf-8", newline="\n")
        print(f"updated narrate_{n}.txt")
