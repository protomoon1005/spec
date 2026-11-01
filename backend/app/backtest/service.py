"""백테스트 실행의 단일 진입점.

워커 태스크 · 라우터는 이 모듈만 본다. runner · inputs 를 직접 import 하지 않는다.

이 모듈의 최상단은 stdlib 만 import 한다. 라우터가 이 모듈을 읽고 라우터는 app.main 이
읽으므로, 여기서 pandas 를 끌어오면 백테스트 의존성이 없는 환경(CI 는 .[dev] 만 설치)에서
앱 전체가 안 뜬다. 러너는 함수 안에서 부른다.
"""
from __future__ import annotations

import math
from datetime import date

from .features import CURRENT_FEATURE_SET_VERSION

DEFAULT_PERIOD_START = date(2023, 1, 1)
SEED_MONEY = 10_000_000
FEATURE_SET_VERSION = CURRENT_FEATURE_SET_VERSION

PERIODS_PER_YEAR = 52  # 평가 시점이 주간이다
RF = 0.025  # 무위험수익률 연 2.5%


class BacktestFailed(Exception):
    """사용자에게 그대로 보여 줄 수 있는 실패. 메시지가 곧 이유다."""


def run_conditions() -> dict:
    """재현 조건 중 비용. 러너 상수를 그대로 옮긴다(슬리피지는 bp 로)."""
    from .runner import FEE, SLIPPAGE, TAX

    return {"fee_rate": FEE, "tax_rate": TAX, "slippage_bp": round(SLIPPAGE * 10_000, 6)}


def run_spec_backtest(strategy_id: str, *, period_start: date, period_end: date, seed_money: float) -> dict:
    """전략서 하나를 전략 · 시장 두 계열로 돌린다.

    반환: {"data_snapshot_asof", "metrics", "window_results"}.
    입력을 만들 수 없으면 BacktestFailed.
    """
    from .inputs import MARKET_TICKER, InputError, build_inputs
    from .runner import run, run_buy_and_hold

    try:
        inp = build_inputs(strategy_id, period_start=period_start, period_end=period_end)
    except InputError as exc:
        raise BacktestFailed(str(exc)) from exc

    tickers = [h["ticker"] for h in inp.holdings]
    strategy = run(
        inp.prices_wide,
        tickers,
        inp.final_ranges,
        inp.cash_min,
        inp.valuation_dates,
        inp.rebalance_dates,
        seed_money,
        drift=inp.drift,
    )
    market = run_buy_and_hold(inp.prices_wide[MARKET_TICKER], inp.valuation_dates, seed_money)

    dates = inp.valuation_dates
    years = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days / 365.25
    curves = {
        "strategy": [p["equity"] for p in strategy["series"]],
        "market": [p["equity"] for p in market],
    }
    summary = {name: compute_metrics(values, years) for name, values in curves.items()}

    return {
        "data_snapshot_asof": inp.data_snapshot_asof,
        "metrics": {
            **{key: summary["strategy"][key] for key in ("cagr", "mdd", "sharpe", "sortino")},
            "win_rate": None,  # 정의가 문서에 없다
            "benchmark_cagr": summary["market"]["cagr"],
        },
        "window_results": {
            "schedule": {
                "rule": inp.rebalance_rule,
                "note": inp.schedule_note,
                # 실제로 리밸런싱한 날. 달력형은 inputs 의 일정 그대로이고, threshold 는 러너가
                # 이탈로 정한 날이다
                "rebalance_dates": strategy["rebalance_dates"],
                "skipped_rebalance_dates": inp.skipped_rebalance_dates + strategy["skipped_rebalance_dates"],
            },
            "summary": summary,
            # 이 실행이 실제로 쓴 종목과 분류. instruments 의 분류는 나중에 바뀔 수 있어서
            # 실행 기록 안에 그때 쓴 값을 그대로 남긴다.
            "universe": [
                {
                    "ticker": h["ticker"],
                    "name": h["name"],
                    "asset_group": h["asset_group"],
                    "sector_group": h["sector_group"],
                    "country_group": h["country_group"],
                    "weight_min_raw": h["min_raw"],
                    "weight_max_raw": h["max_raw"],
                    "weight_min": inp.final_ranges[h["ticker"]][0],
                    "weight_max": inp.final_ranges[h["ticker"]][1],
                }
                for h in inp.holdings
            ],
            "series": [
                {"date": d, "strategy": s, "market": m}
                for d, s, m in zip(dates, curves["strategy"], curves["market"])
            ],
            "decisions": strategy["decisions"],
        },
    }


def compute_metrics(values: list[float], years: float) -> dict:
    """주간 수익률, 표본 표준편차로 지표를 계산한다."""
    first, last = values[0], values[-1]
    rets = [b / a - 1 for a, b in zip(values, values[1:])]
    annualize = math.sqrt(PERIODS_PER_YEAR)
    cagr = (last / first) ** (1 / years) - 1
    vol = _stdev(rets) * annualize
    down = _stdev([min(r, 0.0) for r in rets]) * annualize

    peak, mdd = first, 0.0
    for v in values:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)

    return {
        "total": last / first - 1,
        "cagr": cagr,
        "vol": vol,
        "sharpe": 0.0 if vol == 0 else (cagr - RF) / vol,
        "sortino": 0.0 if down == 0 else (cagr - RF) / down,
        "mdd": mdd,
        "final": last,
    }


def _stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
