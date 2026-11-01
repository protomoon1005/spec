"""블록 사전 로더 (docs/demo-spec.md §B3).

- 지표·연산자: 코드 상수. 지표 id 목록 자체는 core/indicators.INDICATOR_IDS 가 원본이고,
  여기서는 재서술·검증에 필요한 한국어 이름과 단위 계열만 붙인다.
- 표현·종목: data/expressions.json, data/tickers.json.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from legacy.indicators import INDICATOR_IDS

ROOT = Path(__file__).resolve().parents[1]
LEGACY = Path(__file__).resolve().parent
TICKERS_PATH = LEGACY / "data" / "tickers.json"
EXPRESSIONS_PATH = LEGACY / "data" / "expressions.json"

# family: 서로 비교 가능한 단위 묶음. price=원, volume=주, oscillator=0~100
INDICATORS: dict[str, dict] = {
    "CLOSE": {"label": "종가", "family": "price"},
    "OPEN": {"label": "시가", "family": "price"},
    "HIGH": {"label": "고가", "family": "price"},
    "LOW": {"label": "저가", "family": "price"},
    "VOLUME": {"label": "거래량", "family": "volume"},
    "SMA_5": {"label": "5일 이동평균", "family": "price"},
    "SMA_10": {"label": "10일 이동평균", "family": "price"},
    "SMA_20": {"label": "20일 이동평균", "family": "price"},
    "SMA_60": {"label": "60일 이동평균", "family": "price"},
    "SMA_120": {"label": "120일 이동평균", "family": "price"},
    "EMA_12": {"label": "12일 지수이동평균", "family": "price"},
    "EMA_26": {"label": "26일 지수이동평균", "family": "price"},
    "RSI_14": {"label": "RSI(14일)", "family": "oscillator", "range": (0.0, 100.0)},
    "BB_UPPER_20": {"label": "볼린저 밴드 상단(20일, 2σ)", "family": "price"},
    "BB_LOWER_20": {"label": "볼린저 밴드 하단(20일, 2σ)", "family": "price"},
    "VOL_SMA_20": {"label": "거래량 20일 평균", "family": "volume"},
    "HIGH_N_20": {"label": "직전 20일 최고가(당일 제외)", "family": "price"},
    "LOW_N_20": {"label": "직전 20일 최저가(당일 제외)", "family": "price"},
}
assert list(INDICATORS) == INDICATOR_IDS, "INDICATORS 와 indicators.INDICATOR_IDS 가 어긋남"

OPERATORS: dict[str, str] = {
    "lt": "아래 (상태)",
    "gt": "위 (상태)",
    "cross_above": "위로 올라감 (전일 이하 → 당일 초과)",
    "cross_below": "아래로 내려감 (전일 이상 → 당일 미만)",
}

REJECT_CODES: dict[str, str] = {
    "FUTURE_REF": "미래 데이터 참조",
    "UNSUPPORTED_CONCEPT": "사전에 없는 개념",
    "UNSUPPORTED_ACTION": "실행할 수 없는 매매 방식",
    "NO_EXIT_RULE": "매도 규칙 없음",
}


@dataclass(frozen=True)
class TickerInfo:
    ticker: str
    name: str
    asset_type: str          # "stock" | "etf"
    market: str
    aliases: tuple[str, ...]


@lru_cache(maxsize=None)
def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_tickers(path: Path | str = TICKERS_PATH) -> dict[str, TickerInfo]:
    raw = _load_json(str(path))
    out: dict[str, TickerInfo] = {}
    for t in raw["tickers"]:
        if t["ticker"] in out:
            raise ValueError(f"tickers.json 중복 종목코드: {t['ticker']}")
        if t["asset_type"] not in ("stock", "etf"):
            raise ValueError(f"tickers.json {t['ticker']}: asset_type 은 stock/etf 만 허용")
        out[t["ticker"]] = TickerInfo(t["ticker"], t["name"], t["asset_type"], t["market"], tuple(t["aliases"]))
    return out


def ticker_info(ticker: str) -> Optional[TickerInfo]:
    return load_tickers().get(ticker)


def asset_types() -> dict[str, str]:
    """종목코드 → stock/etf. 백테스트 세율 선택에 쓴다."""
    return {k: v.asset_type for k, v in load_tickers().items()}


def load_expressions(path: Path | str = EXPRESSIONS_PATH) -> dict:
    return _load_json(str(path))
