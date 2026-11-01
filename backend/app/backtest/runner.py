"""시점 주입 백테스트 러너 (vectorbt 어댑터).

비중 계산은 우리 도메인 로직이라 직접 구현하고, 주문 · 체결 · 현금 · 수수료 관리만 vectorbt 에
맡긴다.

비중은 전략서에 적힌 종목별 범위의 정중앙이다. 3개 신호원과 성향 · 그룹 상한은 주제 변경으로
지웠다(docs/pivot-plan.md 결정 3, 6). 비중 정의를 다시 정하는 일은 기반 이후에 한다.

미래 참조 차단: 리밸런싱일 d 의 판정은 d 까지의 종가만 본다.
이 규칙이 깨지면 백테스트 결과 전체가 무의미해진다.
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

FEE = 0.00015
SLIPPAGE = 0.0005
TAX = 0.0


# --- 비중 ---------------------------------------------------------------------
def target_weights(
    tickers: list[str], bounds: dict[str, tuple[float, float]], cash_min: float
) -> tuple[dict[str, float], float]:
    """범위 정중앙에서 출발해 투자 가능 금액(1 − cash_min)에 맞춘다. 반환은 (목표 비중, 현금).

    정규화 뒤 범위를 벗어나면 다시 범위로 자르고, 이를 최대 3회 되풀이한다.
    """
    investable = 1 - cash_min
    w = np.array([(bounds[t][0] + bounds[t][1]) / 2 for t in tickers])
    for _ in range(3):
        total = w.sum()
        if total <= 0:
            break
        w = w * investable / total
        w = np.array([min(max(v, bounds[t][0]), bounds[t][1]) for v, t in zip(w, tickers)])
    target = {t: float(v) for t, v in zip(tickers, w)}
    return target, float(1 - w.sum())


def weekly_dates(dates: list[str]) -> list[str]:
    """각 주의 마지막 거래일.

    +4 는 1970-01-01(목)을 주 시작 요일에 맞추는 보정이고, 연도를 키에 넣어
    해를 넘는 주가 두 구간으로 갈리게 한다.
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

    day=1 이면 monthly_first 와 같다.
    """
    out: list[str] = []
    done = ""
    for d in dates:
        if d[:7] != done and int(d[8:10]) >= day:
            out.append(d)
            done = d[:7]
    return out


def quarterly_first(dates: list[str]) -> list[str]:
    """각 분기(1·4·7·10월 시작)의 첫 날짜."""
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
# 값은 블록 정의에서 온다.
DRIFT_THRESHOLD = 0.05


def drifted_weights(
    target: dict[str, float], prev_px: dict[str, float], now_px: dict[str, float], tickers: list[str]
) -> dict[str, float]:
    """직전 리밸런싱 목표 비중이 그 뒤 가격 변화로 흘러간 추정 비중.

    w_i = t_i·r_i / (Σ_j t_j·r_j + (1 − Σ_j t_j)),  r_i = P_i(지금) / P_i(직전 리밸런싱)
    현금은 움직이지 않는다고 보고, 비용·체결 오차는 무시한다 — 체결 엔진과 무관하게 같은 값을
    내도록 실제 보유 수량이 아니라 이 추정식을 쓴다. 가격이 없거나 0 이하이면 r=1.
    합은 tickers 순서로 하나씩 더한다(순서가 바뀌면 끝자리가 갈릴 수 있다).
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


# --- 러너 -------------------------------------------------------------------
def run(
    prices_wide: pd.DataFrame,
    tickers: list[str],
    bounds: dict[str, tuple[float, float]],
    cash_min: float,
    valuation_dates: list[str],
    rebalance_dates: list[str],
    initial_cash: float,
    *,
    drift: tuple[float, int] | None = None,
) -> dict:
    """vectorbt 로 한 계열을 돌린다.

    drift=(임계값, 최소 간격 일수) 를 주면 rebalance_dates 는 "판정 후보일" 이 된다. 첫 후보일은
    무조건 리밸런싱하고, 그 뒤로는 직전 목표 비중의 추정 이탈(max_drift)이 임계값 이상이고 직전
    리밸런싱과 최소 간격 이상 떨어진 후보일에만 리밸런싱한다. 이탈했는데 간격이 모자란 날은
    skipped_rebalance_dates 에 남는다. None 이면 rebalance_dates 가 곧 리밸런싱일이다(달력형 —
    최소 간격은 inputs 가 이미 적용).
    """
    import vectorbt as vbt  # import 에 10초 넘게 걸려서 호출 시점으로 미룬다

    rebal = set(rebalance_dates)
    tgt, cash = target_weights(tickers, bounds, cash_min)

    # 평가 시점 그리드로 맞춘다. 없는 날은 직전 종가를 쓴다.
    px = (
        prices_wide.reindex(prices_wide.index.union(valuation_dates))
        .ffill()
        .reindex(valuation_dates)[tickers]
    )

    target = pd.DataFrame(np.nan, index=px.index, columns=tickers)
    decisions: list[dict] = []
    skipped: list[str] = []
    last: tuple[str, dict[str, float]] | None = None  # (날짜, 그날 가격)

    for d in valuation_dates:
        if d not in rebal:
            continue
        now_px = {t: float(px.at[d, t]) for t in tickers}
        if drift is not None and last is not None:
            threshold, min_interval = drift
            if max_drift(tgt, last[1], now_px, tickers) < threshold:
                continue
            if (date.fromisoformat(d) - date.fromisoformat(last[0])).days < min_interval:
                skipped.append(d)
                continue
        target.loc[d] = [tgt[t] for t in tickers]
        last = (d, now_px)
        decisions.append({"date": d, "target": dict(tgt), "cash": cash})

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
        # 실제로 리밸런싱한 날과, 조건은 됐지만 최소 간격에 걸린 날(drift 일 때만 생긴다)
        "rebalance_dates": [x["date"] for x in decisions],
        "skipped_rebalance_dates": skipped,
    }


def run_buy_and_hold(bars: pd.Series, valuation_dates: list[str], initial_cash: float) -> list[dict]:
    """첫날 전액 매수 후 보유. 시장 참조용."""
    s = bars.reindex(bars.index.union(valuation_dates)).ffill().reindex(valuation_dates)
    first = float(s.iloc[0])
    qty = initial_cash * (1 - FEE) / (first * (1 + SLIPPAGE))
    return [{"date": d, "equity": float(qty * s.loc[d])} for d in valuation_dates]
