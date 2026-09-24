# 시장분석 관점(T3)의 학습 라벨과 실현 채점. DB 도 ML 도 모르는 순수 함수다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 features.py 와 같다 — 이 파일은
# as_of 가드 사정권이다.
#
# ── 사건 정의는 하나다 (2026-09-24 확정) ─────────────────────────────
# 사건 = t 종가 대비 HORIZON 거래일 뒤 종가의 수익률 > 0.
# 학습 라벨(make_label)·실현 채점(realized_outcome)·isotonic 보정이 모두 이 정의를
# 쓴다. 두 함수가 _is_event 한 곳을 거치게 해서 정의가 갈라질 자리를 없앴다 —
# 모델이 배운 사건과 채점받는 사건이 다르면 Brier 도 보정도 뜻을 잃는다.
#
# ── THETA 는 사건 정의가 아니라 학습 표본 필터다 ─────────────────────
# |수익률| < THETA 인 행을 학습 표본에서만 뺀다. 남은 행의 라벨 값은 바꾸지 않고,
# 채점·보정은 THETA 를 보지 않는다. THETA = 0 이면 |r| < 0 이 거짓이라 아무 행도
# 빠지지 않는다.
#
# ── "t+20" 은 그 종목 시계열의 행 기준이다 ───────────────────────────
# 달력 날짜가 아니라 해당 종목 가격 행으로 20행 뒤다.
#
# ── 워크포워드와의 경계 ──────────────────────────────────────────────
# 분할은 expanding · 20거래일마다 재학습 · embargo 20 이다(2026-09-24 확정). 여기서
# 지키는 것은 하나 — t+HORIZON 거래일이 train_end 를 넘는 행은 학습 시점에 라벨이
# 확정되지 않았으므로 표본에 넣지 않는다. 재학습 주기·embargo 는 학습 코드 몫이다.
from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from app.views.market.features import PriceBar

HORIZON = 20
THETA = 0.0


def make_label(bars: Sequence[PriceBar], *, train_end: date) -> list[tuple[date, int]]:
    # 학습 표본 (t 거래일, 라벨) 목록. 세 가지 행이 빠진다.
    #   1) t+HORIZON 행이 아직 없는 행 — 라벨이 정의되지 않는다
    #   2) t+HORIZON 거래일이 train_end 뒤인 행 — 학습 시점에 확정되지 않은 라벨
    #   3) |수익률| < THETA 인 행
    ordered = sorted(bars, key=lambda bar: bar.trade_date)
    labels: list[tuple[date, int]] = []
    for start, end in zip(ordered, ordered[HORIZON:]):
        if end.trade_date > train_end:
            break
        ret = _forward_return(start.close, end.close)
        if abs(ret) < THETA:
            continue
        labels.append((start.trade_date, int(_is_event(ret))))
    return labels


def realized_outcome(close_t: float, close_t_plus_horizon: float) -> int:
    # t 판단의 실현. 채점과 보정이 쓴다. THETA 를 보지 않는다.
    if close_t <= 0:
        raise ValueError(f"기준 종가는 양수여야 한다: {close_t!r}")
    return int(_is_event(_forward_return(close_t, close_t_plus_horizon)))


def _forward_return(close_t: float, close_t_plus_horizon: float) -> float:
    return close_t_plus_horizon / close_t - 1.0


def _is_event(ret: float) -> bool:
    return ret > 0.0
