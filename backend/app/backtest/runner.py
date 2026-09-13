"""시점 주입 백테스트 러너 (vectorbt 어댑터).

lib/backtest.ts 와 같은 알고리즘이다. 비중 계산(신호 → 선형매핑 → 정규화 →
클램프 → 그룹캡)은 우리 도메인 로직이라 직접 구현하고, 주문·체결·현금·수수료
관리만 vectorbt 에 맡긴다.

미래 참조 차단: 신호는 `closes_up_to(t)` 로 t 시점까지 확정된 종가만 본다.
이 규칙이 깨지면 백테스트 결과 전체가 무의미해진다.
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

from app.views.bridge import BacktestJudge

from .policy import CAP_EXEMPT_GROUPS, caps_for, profile_for, resolve_bounds

FEE = 0.00015
SLIPPAGE = 0.0005
TAX = 0.0


# --- 지표 -------------------------------------------------------------------
# 아래 셋은 M3 판단 계층이 붙기 전까지 쓰던 임시 지표다. 지우지 않고 이름만
# _stub_ 로 바꿨다 — 같은 이름의 다른 값이 돌아다니지 않게 하려는 것이다.
def _stub_momentum(closes: np.ndarray, window: int) -> float | None:
    if len(closes) <= window:
        return None
    then = closes[-1 - window]
    return None if not then else float(closes[-1] / then - 1)


def _stub_rsi(closes: np.ndarray, window: int = 14) -> float | None:
    if len(closes) <= window:
        return None
    diffs = np.diff(closes[-window - 1 :])
    gain = float(diffs[diffs >= 0].sum())
    loss = float(-diffs[diffs < 0].sum())
    return 50.0 if gain + loss == 0 else 100.0 * gain / (gain + loss)


def _stub_signal_from(closes: np.ndarray) -> float:
    """종목 신호 s ∈ [-1, +1]. Spec 의 signal_rules 가 지정한 지표를 쓴다.

    M3(LightGBM) 가 붙으면 이 함수가 그 보정 확률을 받는 자리가 된다.
    """
    mom, r = _stub_momentum(closes, 20), _stub_rsi(closes, 14)
    if mom is None or r is None:
        return 0.0
    mom_score = max(-1.0, min(1.0, mom / 0.1))
    rsi_score = max(-1.0, min(1.0, (r - 50) / 30))
    return max(-1.0, min(1.0, 0.6 * mom_score + 0.4 * rsi_score))


# --- 비중 매핑 ---------------------------------------------------------------
def map_signals_to_weights(holdings, bounds, signals, cash_min, caps):
    """신호를 목표 비중으로 바꾼다. 반환은 (매핑직후, 최종, 현금, 캡적용내역)."""
    investable = 1 - cash_min
    tickers = [h["ticker"] for h in holdings]

    # 1 선형 매핑 w = min + (s+1)/2 × (max − min)
    w = np.array([bounds[t][0] + (signals[t] + 1) / 2 * (bounds[t][1] - bounds[t][0]) for t in tickers])

    # 2~4 정규화 + 클램프 (최대 3회)
    for _ in range(3):
        total = w.sum()
        if total <= 0:
            break
        w = w * investable / total
        w = np.array([min(max(v, bounds[t][0]), bounds[t][1]) for v, t in zip(w, tickers)])

    mapped = dict(zip(tickers, w.copy()))

    # 5 그룹 캡. 상위(자산군) → 하위(국가 → 섹터) 순서를 지킨다.
    cur = w.copy()
    applications: list[dict] = []

    def apply_level(key: str, stage: str) -> None:
        seen: list[str] = []
        for h in holdings:
            if h[key] not in seen:
                seen.append(h[key])
        for g in seen:
            if g in CAP_EXEMPT_GROUPS or g not in caps:
                continue
            idx = [i for i, h in enumerate(holdings) if h[key] == g]
            before = float(cur[idx].sum())
            if before > caps[g] + 1e-9:
                factor = caps[g] / before
                cur[idx] *= factor
                applications.append(
                    {"stage": stage, "groupId": g, "sumBefore": before, "cap": caps[g], "factor": factor}
                )

    apply_level("asset_group", "자산군")
    apply_level("country_group", "국가")
    apply_level("sector_group", "섹터")

    target = dict(zip(tickers, cur))
    return mapped, target, float(1 - cur.sum()), applications


# --- 일정 -------------------------------------------------------------------
def weekly_dates(dates: list[str]) -> list[str]:
    """각 주의 마지막 거래일.

    +4 는 1970-01-01(목)을 주 시작 요일에 맞추는 보정이고, 연도를 키에 넣어
    해를 넘는 주가 두 구간으로 갈리게 한다. lib/backtest 쪽 TS 구현과 같은
    규칙이어야 두 러너의 평가 시점이 일치한다.
    """
    out: list[str] = []
    last_key = ""
    epoch = date(1970, 1, 1)
    for d in dates:
        dt = datetime.strptime(d, "%Y-%m-%d").date()
        key = f"{dt.year}-{((dt - epoch).days + 4) // 7}"
        if key == last_key:
            out[-1] = d
        else:
            out.append(d)
            last_key = key
    return out


def monthly_first(dates: list[str]) -> list[str]:
    out: list[str] = []
    last = ""
    for d in dates:
        if d[:7] != last:
            out.append(d)
            last = d[:7]
    return out


# --- 러너 -------------------------------------------------------------------
def run(
    prices_wide: pd.DataFrame,
    holdings: list[dict],
    risk_level: int,
    valuation_dates: list[str],
    rebalance_dates: list[str],
    initial_cash: float,
    use_signals: bool = True,
) -> dict:
    """vectorbt 로 한 계열을 돌린다.

    use_signals=False 면 s=0 (허용범위 정중앙) — 신호만 뺀 대조군이다.
    """
    import vectorbt as vbt  # import 에 10초 넘게 걸려서 호출 시점으로 미룬다

    profile = profile_for(risk_level)
    caps = caps_for(risk_level)
    bounds = resolve_bounds(holdings, profile["grade_cap"])
    tickers = [h["ticker"] for h in holdings]
    rebal = set(rebalance_dates)
    judge = BacktestJudge(tickers)

    # 평가 시점 그리드로 맞춘다. 없는 날은 직전 종가를 쓴다(TS 의 priceOn 과 같은 규칙).
    px = (
        prices_wide.reindex(prices_wide.index.union(valuation_dates))
        .ffill()
        .reindex(valuation_dates)[tickers]
    )

    target = pd.DataFrame(np.nan, index=px.index, columns=tickers)
    decisions: list[dict] = []

    for d in valuation_dates:
        if d not in rebal:
            continue
        if use_signals:
            # as_of 이후 절단과 워밍업 120행 절단은 bridge 가 한다
            judge.record_outcome(prices_wide, as_of=d)
            signals = dict(judge.judge(prices_wide, as_of=d).signals)
        else:
            signals = {t: 0.0 for t in tickers}
        mapped, tgt, cash, apps = map_signals_to_weights(holdings, bounds, signals, profile["cash_min"], caps)
        target.loc[d] = [tgt[t] for t in tickers]
        decisions.append(
            {
                "date": d,
                "signals": signals,
                "mapped": mapped,
                "target": tgt,
                "cash": cash,
                "capApplications": apps,
            }
        )

    pf = vbt.Portfolio.from_orders(
        close=px,
        size=target,
        size_type="targetpercent",
        group_by=True,       # 여러 종목을 한 포트폴리오로 묶는다
        cash_sharing=True,   # 현금을 공유해야 비중이 의미를 갖는다
        init_cash=initial_cash,
        fees=FEE,
        slippage=SLIPPAGE,
        call_seq="auto",     # 매도를 먼저 처리해 현금을 확보한 뒤 매수
        freq="1D",
    )

    value = pf.value()
    return {
        "series": [{"date": d, "equity": float(value.loc[d])} for d in valuation_dates],
        "decisions": decisions,
    }


def run_buy_and_hold(bars: pd.Series, valuation_dates: list[str], initial_cash: float) -> list[dict]:
    """첫날 전액 매수 후 보유. 시장 참조용."""
    s = bars.reindex(bars.index.union(valuation_dates)).ffill().reindex(valuation_dates)
    first = float(s.iloc[0])
    qty = initial_cash * (1 - FEE) / (first * (1 + SLIPPAGE))
    return [{"date": d, "equity": float(qty * s.loc[d])} for d in valuation_dates]
