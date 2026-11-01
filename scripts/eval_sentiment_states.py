#!/usr/bin/env python3
"""감성 상태 경계 후보 비교 — 워크포워드 보정 후 Brier (app/views/sentiment/trend.py 머리 주석).

시장온도 규칙을 고를 때(app/views/regime/scorer.py 주석)와 같은 방식이다.
  - 재학습: schedule 의 20거래일 격자. cutoff c 의 빈도표 · 보정기로 [c, 다음 격자점) 의 판단일을 맞힌다.
  - 표본: 활성 종목 중 묶음 키가 있는 것. 판단일 t 의 사건 = t+20행 수익률 > 0 (labels.py).
  - 상태: t 15:30 까지 발행된 기사로 만든 자기 자산군 세션 상태. 상태가 없는 날(기사 30건 미만 ·
    커버리지 게이트 · 표 없음)은 뺀다 — 그날 운영 경로는 중립이라 후보 간 차이가 없다. 뺀 수를 찍는다.
  - 기준선 둘: cutoff 까지 실현된 표본의 (a) 기저율 상수, (b) 묶음 기저율 P(사건 | 묶음 키).
    **(b) 가 감성 신호를 재는 진짜 기준선이다.** 묶음을 나누기만 해도 (a) 는 이긴다 — 2026-10-27
    무작위 잡음 상태로 돌려 보니 후보가 (a) 는 이기고(2020-01~: 0.23985 -> 0.23678) (b)(0.23571)에는 졌다.
  - 평가 구간: t >= 2023-01-01 중심, t >= 2021-07-01 도 함께
    (기사가 2021-01 부터라 첫 120거래일 뒤부터 상태가 난다).

**결과를 보고 렌즈(분류 매핑 · 모델 · 임계값)를 바꾸지 않는다.** 이 스크립트는 상태 경계만 비교한다.

사용 (api 컨테이너 안, 기사 적재 뒤):
    python /repo/scripts/eval_sentiment_states.py
"""
from __future__ import annotations

import bisect
import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.repositories import etf_master, price_daily  # noqa: E402
from app.scoring import schedule  # noqa: E402
from app.scoring import sentiment as scorer  # noqa: E402
from app.views.market.labels import HORIZON, realized_outcome  # noqa: E402
from app.views.regime.scorer import event_rates, group_key  # noqa: E402
from app.views.sentiment import trend  # noqa: E402

CANDIDATES = {"3구간 ±1.0": trend.EDGES_3, "5구간 ±0.5·±1.5": trend.EDGES_5}
WINDOWS = (date(2021, 7, 1), date(2023, 1, 1))
END = date(2025, 12, 31)


def main() -> int:
    calendar = price_daily.get_trade_dates(start=schedule.CALENDAR_START, as_of=END)
    grid = calendar[:: schedule.RETRAIN_EVERY]
    records = [r for r in etf_master.list_all(include_leveraged=True) if r.active]
    meta = {r.ticker: (r.asset_group_id, group_key(r.asset_group_id, name=r.name)) for r in records}
    meta = {t: m for t, m in meta.items() if m[1]}
    closes = price_daily.get_close_history(sorted(meta), as_of=END)

    # (t, 실현일, 자산군, 묶음 키, 사건) — 실현일은 그 종목 t+20행의 날짜
    events = []
    for ticker, series in closes.items():
        group, key = meta[ticker]
        for (day, before), (after_day, after) in zip(series, series[HORIZON:]):
            if before > 0:
                events.append((day, after_day, group, key, realized_outcome(before, after)))
    events.sort()

    print(f"{'후보':<18}{'구간':<10}{'n':>8}{'제외':>8}{'상수':>10}{'묶음기저율':>10}{'후보':>10}"
          f"{'상수 적중':>10}{'후보 적중':>10}   (Brier, 낮을수록 좋다)")
    for name, edges in CANDIDATES.items():
        _, points = scorer.load_points(END, edges=edges)
        lookups = {g: trend.StateLookup(p) for g, p in points.items()}
        rows = []  # (t, 사건, 후보 p | None, 상수 p, 묶음 기저율 p)
        for i, cutoff in enumerate(grid):
            nxt = grid[i + 1] if i + 1 < len(grid) else date.max
            table = scorer.build(cutoff, edges=edges)
            realized = [(e[3], e[4]) for e in events if e[1] <= cutoff]
            base = (sum(y for _, y in realized) + 1) / (len(realized) + 2)
            by_key = event_rates((key, "all", y) for key, y in realized)
            lo = bisect.bisect_left(events, (cutoff,))
            hi = bisect.bisect_left(events, (nxt,))
            for day, _after_day, group, key, outcome in events[lo:hi]:
                lookup = lookups.get(group)
                point = lookup.point_at(day) if lookup else None
                p = None
                if table is not None and point is not None:
                    rates, calibrator = table
                    raw = trend.raw_score(state=point.state, key=key, rates=rates)
                    p = calibrator.predict(raw) if raw is not None else None
                rows.append((day, outcome, p, base, by_key.get((key, "all"), base)))
        for start in WINDOWS:
            graded = [r for r in rows if r[0] >= start and r[2] is not None]
            dropped = sum(1 for r in rows if r[0] >= start and r[2] is None)
            if not graded:
                print(f"{name:<18}{start.isoformat()[:7]:<10}{0:>8}{dropped:>8}")
                continue
            n = len(graded)
            brier_c = math.fsum((r[3] - r[1]) ** 2 for r in graded) / n
            brier_g = math.fsum((r[4] - r[1]) ** 2 for r in graded) / n
            brier_m = math.fsum((r[2] - r[1]) ** 2 for r in graded) / n
            hit_c = sum(1 for r in graded if (r[3] >= 0.5) == bool(r[1])) / n
            hit_m = sum(1 for r in graded if (r[2] >= 0.5) == bool(r[1])) / n
            print(f"{name:<18}{start.isoformat()[:7]:<10}{n:>8}{dropped:>8}"
                  f"{brier_c:>10.5f}{brier_g:>10.5f}{brier_m:>10.5f}{hit_c:>10.4f}{hit_m:>10.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
