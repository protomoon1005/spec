"""전략 요소 사전 v0.2 (docs/strategy-spec-v0.2.md "비교에 쓸 수 있는 지표").

- 지표·기간·누구의 값(of)·단위·범위의 원본은 여기다. core/schema.py(형식)와
  core/indicators.py(계산), core/validate.py(검사), core/narrate.py(재서술)가 이 표를 따른다.
- 사전에 없는 지표·연산자는 추가하지 않는다 (v0.2 에서 거절 대상).
- 종목: data/tickers.json (데모 시드 목록), 표현 표: data/expressions.json.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
TICKERS_PATH = ROOT / "data" / "tickers.json"
EXPRESSIONS_PATH = ROOT / "data" / "expressions.json"

# of 에 쓸 수 있는 값: "self"(그 종목), 아래 지수 이름, 또는 종목코드("ticker" = 다른 종목)
INDEX_SYMBOLS: dict[str, str] = {"KOSPI200": "KS200", "KOSDAQ": "KQ11", "VIX": "VIX"}   # of 값 → 데이터 심볼
OF_LABELS: dict[str, str] = {"KOSPI200": "코스피200", "KOSDAQ": "코스닥", "VIX": ""}
BENCHMARK_SYMBOL = "KS11"     # 비교 기준: 코스피 지수
US_SYMBOLS = {"VIX"}          # 미국 날짜 기준 → 국내 t일에는 미국 t-1일 종가

_PRICE_OF = ["self", "KOSPI200", "KOSDAQ", "ticker"]

# unit: 같은 unit 끼리만 비교할 수 있다 (3단계 "단위가 다른 것끼리 비교").
# range: 숫자와 비교할 때 숫자가 들어갈 수 있는 범위 (None = 제한 없음).
# exit_only: 팔 때 조건에서만. position: 포지션 상태로 엔진이 계산 (가격 데이터 지표 아님).
INDICATORS: dict[str, dict] = {
    "close": {"label": "종가", "windows": [None], "of": _PRICE_OF, "unit": "price", "range": (0, None)},
    "ma": {"label": "{w}일 이동평균", "windows": [5, 10, 20, 60, 120], "of": _PRICE_OF, "unit": "price",
           "range": (0, None)},
    "rsi": {"label": "RSI({w}일)", "windows": [14], "of": _PRICE_OF, "unit": "score", "range": (0, 100)},
    "ret": {"label": "{w}일 수익률", "windows": [1, 5, 20, 60], "of": _PRICE_OF, "unit": "pct", "range": (-100, None)},
    "vol": {"label": "{w}일 변동성", "windows": [20], "of": _PRICE_OF, "unit": "vol", "range": (0, None)},
    "volume_ratio": {"label": "거래량 비율(직전 {w}일 평균 대비)", "windows": [20], "of": ["self", "ticker"],
                     "unit": "ratio", "range": (0, None)},
    "vix": {"label": "VIX", "windows": [None], "of": ["VIX"], "unit": "vix", "range": (0, None)},
    "entry_return": {"label": "산 가격 대비 수익률", "windows": [None], "of": ["self"], "unit": "pct",
                     "range": (-100, None), "exit_only": True, "position": True},
    "holding_days": {"label": "보유 일수", "windows": [None], "of": ["self"], "unit": "days",
                     "range": (0, None), "exit_only": True, "position": True},
}

# 숫자에 붙이는 단위 (재서술)
UNIT_SUFFIX = {"price": "원", "score": "", "pct": "%", "vol": "%", "ratio": "배", "vix": "", "days": "거래일"}

OPERATORS: dict[str, str] = {
    "<=": "이하 (~인 동안)",
    "<": "아래 (~인 동안)",
    ">=": "이상 (~인 동안)",
    ">": "위 (~인 동안)",
    "cross_above": "위로 올라가는 순간 (전날 이하 → 오늘 초과)",
    "cross_below": "아래로 내려가는 순간 (전날 이상 → 오늘 미만)",
}

REBALANCE: dict[str, str] = {
    "monthly_first": "매달 첫 거래일",
    "monthly_mid": "매달 중순(15일 이후 첫 거래일)",
    "quarterly": "분기마다(1·4·7·10월 첫 거래일)",
    "weekly": "매주(그 주 마지막 거래일)",
    "drift_5pp": "비중이 5%p 이상 벗어난 날",
}

# v0.2 "거절하는 경우"
REJECT_CODES: dict[str, str] = {
    "FUTURE_REF": "미래를 봐야 함",
    "UNKNOWN_INDICATOR": "없는 지표",
    "UNSUPPORTED_ASSET": "못 다루는 상품",
    "UNSUPPORTED_ACTION": "못 다루는 매매",
    "INTRADAY": "장중 매매",
    "NO_DATA": "데이터 없음",
    "OUT_OF_DOMAIN": "투자와 무관",
    "UNRESOLVED": "끝내 못 정함",
    "VALIDATION": "검사에서 막힘",
}

TAX_CATEGORIES = ("KOSPI_STOCK", "KOSDAQ_STOCK", "ETF_DOMESTIC_EQUITY", "ETF_OTHER")
TAX_CATEGORY_LABELS = {"KOSPI_STOCK": "코스피 주식", "KOSDAQ_STOCK": "코스닥 주식",
                       "ETF_DOMESTIC_EQUITY": "국내 주식형 ETF", "ETF_OTHER": "그 밖의 ETF"}


def indicator_label(name: str, window: Optional[int]) -> str:
    return INDICATORS[name]["label"].format(w=window)


def of_kind(of: str) -> str:
    """'self' | 'index' | 'ticker'."""
    if of == "self":
        return "self"
    if of in INDEX_SYMBOLS:
        return "index"
    return "ticker"


# ---------------------------------------------------------------- 종목

@dataclass(frozen=True)
class TickerInfo:
    ticker: str
    name: str
    kind: str                 # "STOCK" | "ETF"
    market: str               # "KOSPI" | "KOSDAQ"
    tax_type: Optional[str]   # ETF 만: "DOMESTIC_EQUITY" | "OTHER"
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
        if t["kind"] not in ("STOCK", "ETF") or t["market"] not in ("KOSPI", "KOSDAQ"):
            raise ValueError(f"tickers.json {t['ticker']}: kind 는 STOCK/ETF, market 은 KOSPI/KOSDAQ")
        tax_type = t.get("tax_type")
        if (t["kind"] == "ETF") != (tax_type in ("DOMESTIC_EQUITY", "OTHER")):
            raise ValueError(f"tickers.json {t['ticker']}: tax_type 은 ETF 에만, DOMESTIC_EQUITY/OTHER")
        out[t["ticker"]] = TickerInfo(t["ticker"], t["name"], t["kind"], t["market"], tax_type,
                                      tuple(t["aliases"]))
    return out


def ticker_info(ticker: str) -> Optional[TickerInfo]:
    return load_tickers().get(ticker)


def tax_category(ticker: str) -> Optional[str]:
    """tickers.json → 세율 표의 applies_to. 목록에 없으면 None."""
    t = ticker_info(ticker)
    if t is None:
        return None
    if t.kind == "STOCK":
        return f"{t.market}_STOCK"
    return "ETF_DOMESTIC_EQUITY" if t.tax_type == "DOMESTIC_EQUITY" else "ETF_OTHER"


def load_expressions(path: Path | str = EXPRESSIONS_PATH) -> dict:
    return _load_json(str(path))
