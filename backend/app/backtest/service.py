"""백테스트 실행의 단일 진입점.

워커 태스크·라우터는 이 모듈만 본다. runner·policy·inputs 를 직접 import 하지
않는다 — 비중 계산 경로가 바뀌어도 여기만 고치면 되게 하려는 것이다.

이 모듈의 최상단은 stdlib 과 app.views 만 import 한다. 라우터가 이 모듈을 읽고
라우터는 app.main 이 읽으므로, 여기서 pandas 를 끌어오면 백테스트 의존성이 없는
환경(CI 는 .[dev] 만 설치)에서 앱 전체가 안 뜬다. 러너는 함수 안에서 부른다.
"""
from __future__ import annotations

import math
from datetime import date

from app.views.market.features import CURRENT_FEATURE_SET_VERSION

DEFAULT_PERIOD_START = date(2023, 1, 1)  # 데모 계획 1.4
SEED_MONEY = 10_000_000  # scripts/run_backtest_vbt.py 의 INITIAL
FEATURE_SET_VERSION = CURRENT_FEATURE_SET_VERSION

# 지표 정의는 frontend/lib/data.ts 의 computeMetrics 와 같다. **정의가 두 곳이다** —
# 한쪽을 고치면 다른 쪽도 고쳐야 화면의 정적 결과와 API 결과가 같은 뜻을 갖는다.
PERIODS_PER_YEAR = 52  # 평가 시점이 주간이다
RF = 0.025  # 무위험수익률 연 2.5%


class BacktestFailed(Exception):
    """사용자에게 그대로 보여 줄 수 있는 실패. 메시지가 곧 이유다."""


def run_conditions() -> dict:
    """재현 조건 중 비용. 러너 상수를 그대로 옮긴다(슬리피지는 bp 로)."""
    from .runner import FEE, SLIPPAGE, TAX

    return {"fee_rate": FEE, "tax_rate": TAX, "slippage_bp": round(SLIPPAGE * 10_000, 6)}


# 비중은 M2 체인 RiskSizer → WeightMapper → GroupCapEnforcer → OrderBuilder
# (docs/m2-algorithms.md 2장, app/m2/weights.py)가 낸다. 범위·현금·손실 한도는 Validator
# 확정값이다(app/backtest/m2_chain.py). 러너에는 weigh 콜러블로 끼운다 — 러너의 기본 경로
# (map_signals_to_weights)는 스크립트·TS 대조가 계속 쓴다.
def run_spec_backtest(spec_id: str, *, period_start: date, period_end: date, seed_money: float) -> dict:
    """전략서 하나를 전략·대조군·시장 세 계열로 돌린다.

    반환: {"data_snapshot_asof", "metrics", "window_results"}.
    입력을 만들 수 없으면 BacktestFailed.
    """
    from app.contracts.view_weights import VIEW_TYPES
    from app.m2.weights import ATR_MULTIPLE, WeightChainError, horizon_for
    from app.views.bridge import SCORERS, scorer_source

    from .inputs import MARKET_TICKER, InputError, build_inputs
    from .m2_chain import AtrSource, ChainError, M2Weigher, load_bars, load_confirmed
    from .policy import CAP_EXEMPT_GROUPS, caps_for
    from .runner import run, run_buy_and_hold

    try:
        confirmed = load_confirmed(spec_id, as_of=period_end)
        inp = build_inputs(
            spec_id,
            period_start=period_start,
            period_end=period_end,
            min_interval_days=confirmed.min_interval_days,
        )
        horizon = horizon_for(inp.rebalance_rule)
    except (InputError, ChainError, WeightChainError) as exc:
        raise BacktestFailed(str(exc)) from exc

    atr = AtrSource(load_bars([h["ticker"] for h in inp.holdings], as_of=period_end))

    def weigher() -> M2Weigher:
        # 계열마다 새로 만든다 — 주문은 그 계열의 직전 목표 비중에서 나온다.
        return M2Weigher(
            inp.holdings,
            confirmed,
            caps=caps_for(inp.risk_level),
            exempt=CAP_EXEMPT_GROUPS,
            horizon_days=horizon,
            atr=atr,
        )

    args = (inp.prices_wide, inp.holdings, inp.risk_level, inp.valuation_dates, inp.rebalance_dates)
    strategy_w, control_w = weigher(), weigher()
    try:
        strategy = run(*args, seed_money, use_signals=True, weigh=strategy_w)
        control = run(*args, seed_money, use_signals=False, weigh=control_w)
    except WeightChainError as exc:
        raise BacktestFailed(f"비중 산출 사후 검증 실패: {exc}") from exc
    for series, w in ((strategy, strategy_w), (control, control_w)):
        for decision in series["decisions"]:
            decision["m2"] = w.records[decision["date"]]
    market = run_buy_and_hold(inp.prices_wide[MARKET_TICKER], inp.valuation_dates, seed_money)

    dates = inp.valuation_dates
    years = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days / 365.25
    curves = {
        "strategy": [p["equity"] for p in strategy["series"]],
        "control": [p["equity"] for p in control["series"]],
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
            "risk_level": inp.risk_level,
            "weight_path": "M2 체인 (RiskSizer v0.1 → WeightMapper → GroupCapEnforcer → OrderBuilder)",
            "m2": {
                **confirmed.as_dict(),
                # Validator 확정 범위. universe 의 키는 프론트 결과 파일과 맞춰 두므로 여기 따로 둔다.
                "bounds": {t: list(b) for t, b in confirmed.bounds.items()},
                "risk_sizer": {"version": "v0.1", "atr_multiple": ATR_MULTIPLE, "horizon_days": horizon},
            },
            "scorer_sources": {vt: scorer_source(SCORERS[vt]) for vt in VIEW_TYPES},
            "schedule": {
                "rule": inp.rebalance_rule,
                "note": inp.schedule_note,
                "rebalance_dates": inp.rebalance_dates,
                "skipped_rebalance_dates": inp.skipped_rebalance_dates,
            },
            "summary": summary,
            # 이 실행이 실제로 쓴 종목과 분류. etf_master 의 등급·분류는 나중에 바뀔 수
            # 있어서, 실행 기록 안에 그때 쓴 값을 그대로 남긴다 — 리포트가 같은 실행을
            # 언제 다시 열어도 같은 비중 표를 그리려면 여기서 읽어야 한다.
            # 키는 frontend/data/backtest-result.json 의 universe 와 같다.
            "universe": [
                {
                    "ticker": h["ticker"],
                    "name": h["name"],
                    "grade": h["grade"],
                    "asset_group": h["asset_group"],
                    "sector_group": h["sector_group"],
                    "country_group": h["country_group"],
                    "weight_min_raw": h["min_raw"],
                    "weight_max_raw": h["max_raw"],
                }
                for h in inp.holdings
            ],
            "series": [
                {"date": d, "strategy": s, "control": c, "market": m}
                for d, s, c, m in zip(dates, curves["strategy"], curves["control"], curves["market"])
            ],
            "decisions": strategy["decisions"],
            "control_decisions": control["decisions"],
        },
    }


def compute_metrics(values: list[float], years: float) -> dict:
    """frontend/lib/data.ts computeMetrics 의 파이썬 판. 주간 수익률, 표본 표준편차."""
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
