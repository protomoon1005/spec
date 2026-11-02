"""테스트 공용 헬퍼 (v0.2). 네트워크 없이 결정론적 합성 데이터만 사용한다 (random 금지)."""
from __future__ import annotations

import copy
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_ohlcv(closes, opens=None, start="2024-01-02", volume=None, index=None) -> pd.DataFrame:
    """종가 목록으로 OHLCV 생성. 시가 미지정 시 전일 종가, 고가/저가는 시가·종가의 ±1%."""
    closes = [float(c) for c in closes]
    if opens is None:
        opens = [closes[0]] + closes[:-1]
    opens = [float(o) for o in opens]
    highs = [max(o, c) * 1.01 for o, c in zip(opens, closes)]
    lows = [min(o, c) * 0.99 for o, c in zip(opens, closes)]
    if volume is None:
        volume = [1000 + (i % 7) * 100 for i in range(len(closes))]
    idx = index if index is not None else pd.bdate_range(start, periods=len(closes), name="Date")
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": [float(v) for v in volume]},
        index=pd.DatetimeIndex(idx, name="Date"),
    )


def wavy_ohlcv(n: int = 300, phase: float = 0.0, base: float = 10000.0) -> pd.DataFrame:
    """사인파 + 추세로 만든 결정론적 가격. RSI 과매도·이평 이탈이 여러 번 발생하도록."""
    closes = [
        base + 0.8 * base / 1000 * i + 0.15 * base * math.sin(i / 9.0 + phase) + 0.04 * base * math.sin(i / 2.3)
        for i in range(n)
    ]
    opens = [closes[0]] + [c + 0.003 * base * math.cos(i) for i, c in enumerate(closes[:-1])]
    vol = [100000 + int(50000 * (1 + math.sin(i / 3.0))) + (300000 if i % 37 == 0 else 0) for i in range(n)]
    return make_ohlcv(closes, opens=opens, volume=vol)


def cmp(left: dict, op: str, right) -> dict:
    """비교 하나. right 가 숫자면 {"value": n}."""
    if isinstance(right, (int, float)):
        right = {"value": right}
    return {"left": left, "op": op, "right": right}


def ind(indicator: str, window=None, of: str = "self") -> dict:
    d = {"indicator": indicator, "of": of}
    if window is not None:
        d["window"] = window
    return d


# 제안서 대표 예시: RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘
RSI_ENTRY = cmp(ind("rsi", 14), "cross_below", 30)
MA5_EXIT = cmp(ind("close"), "cross_below", ind("ma", 5))

# 합성 데이터의 기본 세율 분류 (실제 종목이 아니므로 tickers.json 대신 직접 준다)
STOCK = "KOSPI_STOCK"


def strategy_dict(**overrides) -> dict:
    d = {
        "schema_version": "0.2",
        "assets": [{"ticker": "TEST01", "name": "테스트", "weight": None}],
        "max_weight": None,
        "rebalance": None,
        "rules": {"target": "each", "entry": copy.deepcopy(RSI_ENTRY), "exit": copy.deepcopy(MA5_EXIT)},
        "backtest": {"start": "2000-01-01", "end": "2100-12-31", "initial_cash": 10_000_000,
                     "commission_rate": 0.00015, "slippage_rate": 0.0005},
    }
    for k, v in overrides.items():
        if k == "backtest":
            d["backtest"].update(v)
        else:
            d[k] = v
    return d
