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
from app.views.integrator import DEADZONE

from .policy import CAP_EXEMPT_GROUPS, caps_for, profile_for, resolve_bounds

FEE = 0.00015
SLIPPAGE = 0.0005
TAX = 0.0


# 지표 계산은 M3 가 소유한다. 예전에 여기 있던 momentum/rsi/signal_from 은
# app/views/bridge.py 가 붙으면서 죽은 코드가 됐고, 특히 rsi 는 Wilder 평활이
# 아닌 단순비율이라 M3 의 rsi_14 와 같은 이름으로 다른 값을 냈다(069500 실측
# 5.71 차이). 같은 이름의 다른 정의가 돌아다니는 편이 없는 편보다 위험해서
# 지운다 — 신호는 BacktestJudge.judge() 한 곳에서만 나온다.

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


def monthly_on_or_after(dates: list[str], day: int) -> list[str]:
    """각 달에서 day 일 이후(포함) 첫 날짜. 그 달에 그런 날짜가 없으면 그 달은 건너뛴다.

    day=1 이면 monthly_first 와 같다. lib/backtest.ts 의 monthlyOnOrAfter 와 같은 규칙이다.
    """
    out: list[str] = []
    done = ""
    for d in dates:
        if d[:7] != done and int(d[8:10]) >= day:
            out.append(d)
            done = d[:7]
    return out


def quarterly_first(dates: list[str]) -> list[str]:
    """각 분기(1·4·7·10월 시작)의 첫 날짜. lib/backtest.ts 의 quarterlyFirst 와 같다."""
    out: list[str] = []
    last = ""
    for d in dates:
        key = f"{d[:4]}-Q{(int(d[5:7]) - 1) // 3}"
        if key != last:
            out.append(d)
            last = key
    return out


# --- 비중 이탈(threshold) ----------------------------------------------------
# RB_THRESHOLD_5 "목표 비중에서 5%p 이상 벌어진 종목이 생기면 되돌린다"(db/seeds/05_bbl.sql).
# 전략서 trigger 에는 임계값 칸이 없고(TriggerSpec extra=forbid) threshold 블록은 이것 하나라
# 값은 블록 정의에서 온다. lib/backtest.ts 의 DRIFT_THRESHOLD 와 같아야 한다.
DRIFT_THRESHOLD = 0.05


def drifted_weights(
    target: dict[str, float], prev_px: dict[str, float], now_px: dict[str, float], tickers: list[str]
) -> dict[str, float]:
    """직전 리밸런싱 목표 비중이 그 뒤 가격 변화로 흘러간 추정 비중.

    w_i = t_i·r_i / (Σ_j t_j·r_j + (1 − Σ_j t_j)),  r_i = P_i(지금) / P_i(직전 리밸런싱)
    현금은 움직이지 않는다고 보고, 비용·체결 오차는 무시한다 — 두 러너가 체결 엔진과 무관하게
    같은 값을 내도록 실제 보유 수량이 아니라 이 추정식을 쓴다. 가격이 없거나 0 이하이면 r=1.
    합은 tickers 순서로 하나씩 더한다(numpy 합은 순서가 달라 TS 와 끝자리가 갈릴 수 있다).
    lib/backtest.ts 의 driftedWeights 와 같은 식이다.
    """
    grown: dict[str, float] = {}
    invested = 0.0
    total_target = 0.0
    for t in tickers:
        a, b = prev_px.get(t), now_px.get(t)
        r = b / a if a is not None and b is not None and a > 0 and b > 0 else 1.0
        grown[t] = target[t] * r
        invested += grown[t]
        total_target += target[t]
    denom = invested + (1.0 - total_target)
    return {t: grown[t] / denom for t in tickers}


def max_drift(
    target: dict[str, float], prev_px: dict[str, float], now_px: dict[str, float], tickers: list[str]
) -> float:
    """종목별 |흘러간 비중 − 목표 비중| 의 최댓값(절대 %p, 0.05 = 5%p)."""
    w = drifted_weights(target, prev_px, now_px, tickers)
    return max(abs(w[t] - target[t]) for t in tickers)


# --- 신호 방향(signal) --------------------------------------------------------
def signal_directions(signals: dict[str, float], tickers: list[str]) -> dict[str, int]:
    """RB_SIGNAL 의 "신호 방향". |s| < 데드존(0.10) 은 0, 그 밖은 부호(+1 · −1)."""
    return {t: 0 if abs(signals[t]) < DEADZONE else (1 if signals[t] > 0 else -1) for t in tickers}


# --- 러너 -------------------------------------------------------------------
def run(
    prices_wide: pd.DataFrame,
    holdings: list[dict],
    risk_level: int,
    valuation_dates: list[str],
    rebalance_dates: list[str],
    initial_cash: float,
    use_signals: bool = True,
    *,
    weigh=None,
    drift: tuple[float, int] | None = None,
    signal_interval: int | None = None,
) -> dict:
    """vectorbt 로 한 계열을 돌린다.

    drift=(임계값, 최소 간격 일수) 를 주면 rebalance_dates 는 "판정 후보일" 이 된다. 첫 후보일은
    무조건 리밸런싱하고, 그 뒤로는 직전 목표 비중의 추정 이탈(max_drift)이 임계값 이상이고 직전
    리밸런싱과 최소 간격 이상 떨어진 후보일에만 리밸런싱한다. 이탈했는데 간격이 모자란 날은
    skipped_rebalance_dates 에 남는다. 일정이 비중에 따라 바뀌므로 미리 걸러 둘 수 없어서다.
    signal_interval=최소 간격 일수를 주면(신호형) 역시 rebalance_dates 는 판정 후보일이다. 후보일마다
    judge.preview_rebalance 로 "지금 리밸런싱하면 나올 신호" 를 상태 변경 없이 구하고, 종목별 방향
    (signal_directions)이 직전 리밸런싱 때와 하나라도 다르고 간격이 되면 그 판단을 commit 해 쓴다 —
    관점 가중치 채점 · 갱신은 리밸런싱일에만 일어나고 같은 날 두 번 판단하지 않는다. use_signals=True 일
    때만 뜻이 있다(대조군은 s ≡ 0 이라 방향이 안 바뀐다 — service 는 전략 계열의 날짜를 대조군에 준다).
    None 이면 rebalance_dates 가 곧 리밸런싱일이다(달력형 — 최소 간격은 inputs 가 이미 적용).

    use_signals=False 면 s=0 (허용범위 정중앙) — 신호만 뺀 대조군이다.
    weigh 를 주면 비중 계산을 weigh(date, signals) 로 바꿔 끼운다(M2 체인, service 경로).
    반환은 map_signals_to_weights 와 같은 4-튜플이어야 한다. None 이면 _raw 를 resolve_bounds 로 접고
    성향 기본 현금을 쓰는 기본 경로다 — 데모 스크립트 · TS 러너 대조 · 검증에서 막힌 전략서가 쓴다.
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
    skipped: list[str] = []
    last: tuple[str, dict[str, float], dict[str, float]] | None = None  # (날짜, 목표, 그날 가격)
    if signal_interval is not None and not use_signals:
        raise ValueError("신호형 리밸런싱은 신호를 쓰는 계열에서만 판정할 수 있다")
    last_dirs: dict[str, int] | None = None

    for d in valuation_dates:
        if d not in rebal:
            continue
        now_px = {t: float(px.at[d, t]) for t in tickers}
        if drift is not None and last is not None:
            threshold, min_interval = drift
            if max_drift(last[1], last[2], now_px, tickers) < threshold:
                continue
            if (date.fromisoformat(d) - date.fromisoformat(last[0])).days < min_interval:
                skipped.append(d)
                continue
        if signal_interval is not None:
            preview = judge.preview_rebalance(prices_wide, as_of=d)
            dirs = signal_directions(preview["signal"].signals, tickers)
            if last is not None:
                if dirs == last_dirs:
                    continue
                if (date.fromisoformat(d) - date.fromisoformat(last[0])).days < signal_interval:
                    skipped.append(d)
                    continue
            signals = dict(judge.commit(preview).signals)
            last_dirs = dirs
        elif use_signals:
            # as_of 이후 절단과 워밍업 120행 절단은 bridge 가 한다
            judge.record_outcome(prices_wide, as_of=d)
            signals = dict(judge.judge(prices_wide, as_of=d).signals)
        else:
            signals = {t: 0.0 for t in tickers}
        if weigh is None:
            mapped, tgt, cash, apps = map_signals_to_weights(
                holdings, bounds, signals, profile["cash_min"], caps
            )
        else:
            mapped, tgt, cash, apps = weigh(d, signals)
        target.loc[d] = [tgt[t] for t in tickers]
        last = (d, {t: float(tgt[t]) for t in tickers}, now_px)
        decision = {
            "date": d,
            "signals": signals,
            "mapped": mapped,
            "target": tgt,
            "cash": cash,
            "capApplications": apps,
        }
        if use_signals:
            decision["views"] = judge.last_views()
        decisions.append(decision)

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
        # 실제로 리밸런싱한 날과, 조건은 됐지만 최소 간격에 걸린 날(drift · signal 일 때만 생긴다)
        "rebalance_dates": [x["date"] for x in decisions],
        "skipped_rebalance_dates": skipped,
    }


def run_buy_and_hold(bars: pd.Series, valuation_dates: list[str], initial_cash: float) -> list[dict]:
    """첫날 전액 매수 후 보유. 시장 참조용."""
    s = bars.reindex(bars.index.union(valuation_dates)).ffill().reindex(valuation_dates)
    first = float(s.iloc[0])
    qty = initial_cash * (1 - FEE) / (first * (1 + SLIPPAGE))
    return [{"date": d, "equity": float(qty * s.loc[d])} for d in valuation_dates]
