"""테스트 공용 헬퍼. 네트워크 없이 결정론적 합성 데이터만 사용한다 (random 금지)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_ohlcv(closes, opens=None, start="2024-01-02", volume=None) -> pd.DataFrame:
    """종가 목록으로 OHLCV 생성. 시가 미지정 시 전일 종가, 고가/저가는 시가·종가의 ±1%."""
    closes = [float(c) for c in closes]
    if opens is None:
        opens = [closes[0]] + closes[:-1]
    opens = [float(o) for o in opens]
    highs = [max(o, c) * 1.01 for o, c in zip(opens, closes)]
    lows = [min(o, c) * 0.99 for o, c in zip(opens, closes)]
    if volume is None:
        volume = [1000 + (i % 7) * 100 for i in range(len(closes))]
    idx = pd.bdate_range(start, periods=len(closes), name="Date")
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volume},
        index=idx,
    )


def wavy_ohlcv(n: int = 300) -> pd.DataFrame:
    """사인파 + 추세로 만든 결정론적 가격. RSI 과매도·이평 이탈이 여러 번 발생하도록."""
    closes = [
        10000 + 8 * i + 1500 * math.sin(i / 9.0) + 400 * math.sin(i / 2.3)
        for i in range(n)
    ]
    opens = [closes[0]] + [c + 30 * math.cos(i) for i, c in enumerate(closes[:-1])]
    vol = [100000 + int(50000 * (1 + math.sin(i / 3.0))) + (300000 if i % 37 == 0 else 0) for i in range(n)]
    return make_ohlcv(closes, opens=opens, volume=vol)


def base_spec_dict(**overrides) -> dict:
    d = {
        "schema_version": "demo-0.1",
        "verdict": "ok",
        "questions": [],
        "reject_reason": None,
        "universe": [{"ticker": "TEST01", "name": "테스트", "weight": 1.0}],
        "entry": {"logic": "all", "conditions": [{"left": "RSI_14", "op": "cross_below", "right": 30}]},
        "exit": {
            "logic": "any",
            "conditions": [{"left": "CLOSE", "op": "cross_below", "right": "SMA_5"}],
            "stop_loss": None,
            "take_profit": None,
            "max_hold_days": None,
        },
        "backtest": {"start": "2000-01-01", "end": "2100-12-31", "initial_cash": 10_000_000},
        "costs": {"commission_bps": 1.5, "tax_profile": "kr_default"},
        "provenance": None,
    }
    d.update(overrides)
    return d
