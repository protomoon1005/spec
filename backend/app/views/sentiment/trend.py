# 감성 관점(FN-408) — 묶음별 뉴스 감성 집계를 "심리 상태" 로 바꾸는 순수 함수. stdlib 만 쓴다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 다른 app/views 모듈과 같다:
# scripts/check_asof_guard.py 가 문자열 리터럴에서 가드 대상 테이블명을 찾는다.
#
# ── 입력 ─────────────────────────────────────────────────────────────
# 묶음(EQUITY/BOND/COMMODITY, app/views/sentiment/lens.py)별 · 장 세션별 기사 집계
# (n, n_pos, n_neg). 기사 한 건의 긍정/부정은 lens.py 의 POLARITY_EDGE 로 가른다.
#
# ── 장 세션 (as_of 규약) ──────────────────────────────────────────────
# 거래일 T 의 세션 = (직전 거래일 15:30 KST, T 15:30 KST] 에 발행된 기사.
# 15:30 뒤 기사는 다음 거래일 세션이다 — 장 마감 뒤 기사로 그날 판단하지 않는다.
# 주말 · 휴장일 기사는 다음 거래일 세션으로 넘어간다(bucket_sessions).
# 거래일 달력은 가격 저장소의 거래일이다. 달력의 마지막 거래일 뒤 기사는 세션이 아직 없다.
#
# ── 규칙 ─────────────────────────────────────────────────────────────
# 1) 세션 지수 B_T = ln((1 + n_pos) / (1 + n_neg)). 유효 기사 MIN_ARTICLES(30) 미만이면 결측.
# 2) z_T = (B_T − 평균) / 표준편차. 평균 · 표준편차는 T 를 **뺀** 직전 WINDOW(120) 거래일 칸에서.
#    표준편차는 모표준편차(pstdev). ±Z_CLIP(3) 으로 자른다.
#    직전 120칸 중 값이 있는 칸이 MIN_COVERAGE(70%) 미만이거나 표준편차가 0 이면 None.
# 3) 상태 = z 를 경계 edges 로 나눈 구간. 경계값 위의 z 는 바깥(극단) 쪽 — −1.0 이면 공포,
#    +1.0 이면 탐욕. 0 을 기준으로 대칭이 되게 하려는 것이다.
# 4) 판단일 t 의 상태 = t 이하 가장 늦은 세션의 상태. 그 세션이 t 보다 MAX_STALE_DAYS 넘게
#    앞서면 None(적재가 끊긴 채 옛 상태로 판단하지 않는다). 결측 세션도 None 이다 —
#    직전 세션 상태를 끌어 쓰지 않는다.
# None 은 호출자(app/scoring/sentiment.py)가 neutral_score(REASON_NO_DATA) 로 바꾼다.
#
# ── 상태 → 점수 ──────────────────────────────────────────────────────
# raw = 2·P(사건 | 종목 묶음, 상태) − 1, P 는 라플라스 평활 조건부 빈도(시장온도와 같은 틀,
# app/views/regime/scorer.py). **부호를 손으로 정하지 않는다** — 부정 기사가 몰린 뒤 오르는지
# (역발상) 내리는지(모멘텀)는 빈도표가 정한다. 종목은 자기 자산군 묶음의 상태를 쓰고,
# 인버스는 group_key 가 따로라 빈도가 부호를 알아서 뒤집는다.
#
# ── 상태 경계는 설계 수치다 (측정 대기) ──────────────────────────────
# 후보: 3구간 ±1.0 (EDGES_3) / 5구간 ±0.5 · ±1.5 (EDGES_5).
# 워크포워드 20거래일 격자 · 보정 후 Brier · 기저율 상수와 묶음 기저율 P(사건 | 묶음) 둘과 비교
# (scripts/eval_sentiment_states.py). 묶음을 나누는 것만으로 상수는 이긴다 — 무작위 잡음 상태로
# 돌려 보면(2026-10-27) 후보가 상수는 이기고 묶음 기저율에는 진다. 감성 상태가 값을 더한다고
# 말하려면 묶음 기저율을 이겨야 한다. 3구간 ±1.0 은 2026-10-28 사용자 결정으로 유지한다.
# 2026-10-28 실측(빅카인즈 754,112건 적재 뒤, 활성 종목, 상태 있는 날만, Brier — 낮을수록 좋다):
#   후보              구간        n      상수     묶음기저율  후보     적중(상수/후보)
#   3구간 ±1.0        t>=2021-07  71970  0.24395  0.23958   0.24781  0.5919/0.5541
#   3구간 ±1.0        t>=2023-01  49614  0.23074  0.22566   0.23475  0.6554/0.5862
#   5구간 ±0.5·±1.5   t>=2021-07  71970  0.24395  0.23958   0.24871  0.5919/0.5469
#   5구간 ±0.5·±1.5   t>=2023-01  49614  0.23074  0.22566   0.23508  0.6554/0.5725
# **두 후보 모두 상수 · 묶음 기저율 둘 다에 진다.** 상태별 빈도가 다음 구간에 이어지지 않는다는 뜻이다.
# 2026-10-28 사용자 결정: 두 후보 모두 지지만 3구간 ±1.0 을 유지하고 한계로 기록한다(docs/known-issues.md).
from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

WINDOW = 120
MIN_COVERAGE = 0.70
MIN_ARTICLES = 30
Z_CLIP = 3.0
MAX_STALE_DAYS = 7

STATE_EXTREME_FEAR = "extreme_fear"
STATE_FEAR = "fear"
STATE_NEUTRAL = "neutral"
STATE_GREED = "greed"
STATE_EXTREME_GREED = "extreme_greed"

EDGES_3: tuple[float, ...] = (-1.0, 1.0)
EDGES_5: tuple[float, ...] = (-1.5, -0.5, 0.5, 1.5)
STATE_LABELS: dict[int, tuple[str, ...]] = {
    3: (STATE_FEAR, STATE_NEUTRAL, STATE_GREED),
    5: (STATE_EXTREME_FEAR, STATE_FEAR, STATE_NEUTRAL, STATE_GREED, STATE_EXTREME_GREED),
}
# 2026-10-28 유지 결정 — 위 머리 주석.
ACTIVE_EDGES = EDGES_3
STATE_RULE_VERSION = "z120-e3-v0.1"


@dataclass(frozen=True)
class SessionCount:
    n: int
    n_pos: int
    n_neg: int


@dataclass(frozen=True)
class TrendPoint:
    session: date  # 거래일 T (그 세션은 T 15:30 KST 에 닫힌다)
    n: int
    index: float | None  # B_T, 결측이면 None
    z: float | None
    state: str | None


def balance(n_pos: int, n_neg: int) -> float:
    if n_pos < 0 or n_neg < 0:
        raise ValueError(f"기사 수가 음수다: n_pos={n_pos} n_neg={n_neg}")
    return math.log((1.0 + n_pos) / (1.0 + n_neg))


def bucket_sessions(
    rows: Iterable[tuple[date, SessionCount]], calendar: Sequence[date]
) -> dict[date, SessionCount]:
    # rows 는 (달력 세션일 d, 집계). d 는 "그날 15:30 이하, 아니면 다음 날" 로 이미 잘린 날짜다.
    # d 이상 첫 거래일로 넘긴다(주말 · 휴장일 -> 다음 거래일). 달력 뒤의 d 는 버린다.
    out: dict[date, list[int]] = {}
    for day, count in rows:
        i = bisect.bisect_left(calendar, day)
        if i == len(calendar):
            continue
        cell = out.setdefault(calendar[i], [0, 0, 0])
        cell[0] += count.n
        cell[1] += count.n_pos
        cell[2] += count.n_neg
    return {day: SessionCount(*cell) for day, cell in out.items()}


def zscores(
    values: Sequence[float | None],
    *,
    window: int = WINDOW,
    min_coverage: float = MIN_COVERAGE,
    clip: float = Z_CLIP,
) -> list[float | None]:
    need = math.ceil(min_coverage * window)
    out: list[float | None] = []
    for i, today in enumerate(values):
        past = [v for v in values[max(0, i - window) : i] if v is not None]
        if today is None or len(past) < need:
            out.append(None)
            continue
        std = statistics.pstdev(past)
        if std == 0:
            out.append(None)
            continue
        out.append(max(-clip, min(clip, (today - statistics.fmean(past)) / std)))
    return out


def state_of(z: float | None, *, edges: Sequence[float] = ACTIVE_EDGES) -> str | None:
    if z is None:
        return None
    labels = STATE_LABELS.get(len(edges) + 1)
    if labels is None or list(edges) != sorted(edges):
        raise ValueError(f"지원하지 않는 상태 경계: {tuple(edges)!r}")
    bucket = sum(1 for e in edges if (z >= e if e >= 0 else z > e))
    return labels[bucket]


def timeline(
    sessions: Mapping[date, SessionCount],
    calendar: Sequence[date],
    *,
    edges: Sequence[float] = ACTIVE_EDGES,
    min_articles: int = MIN_ARTICLES,
) -> list[TrendPoint]:
    # 달력의 거래일마다 한 점. 기사가 없거나 모자란 거래일도 칸은 차지한다(커버리지 계산).
    counts = [sessions.get(day, SessionCount(0, 0, 0)) for day in calendar]
    index = [balance(c.n_pos, c.n_neg) if c.n >= min_articles else None for c in counts]
    zs = zscores(index)
    return [
        TrendPoint(day, c.n, b, z, state_of(z, edges=edges))
        for day, c, b, z in zip(calendar, counts, index, zs, strict=True)
    ]


class StateLookup:
    # 판단일 t -> t 이하 가장 늦은 세션의 점. 위 규칙 4).
    def __init__(self, points: Sequence[TrendPoint], *, max_stale_days: int = MAX_STALE_DAYS):
        self._points = sorted(points, key=lambda p: p.session)
        self._days = [p.session for p in self._points]
        self._max_stale = max_stale_days

    def point_at(self, day: date) -> TrendPoint | None:
        i = bisect.bisect_right(self._days, day)
        if i == 0:
            return None
        point = self._points[i - 1]
        if (day - point.session).days > self._max_stale or point.state is None:
            return None
        return point


def raw_score(
    *, state: str | None, key: str | None, rates: Mapping[tuple[str, str], float]
) -> float | None:
    if state is None or key is None:
        return None
    probability = rates.get((key, state), 0.5)
    return max(-1.0, min(1.0, 2.0 * probability - 1.0))
