#!/usr/bin/env python3
"""적재된 거시지표로 국면 스냅샷을 만들어 채운다 (FN-406 백필).

판정 자체는 app/views/regime/judge.py 의 순수 함수가 하고, 조회는 저장소 계층의
as_of 질의를 거친다. 이 스크립트는 start~end 의 모든 달력일(주말 포함)을 하루씩 훑으며 둘을 잇기만 한다.

**시점 규약**: 하루치 판정은 그날 as_of 로 조회한 값만 쓴다. 조회 함수가
released_at <= as_of 로도 거르므로 공표 전 값은 들어오지 않는다.

사용:
    DATABASE_URL=... python scripts/build_regime.py --start 2023-01-01 --end 2025-12-31
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.macro.codes import CREDIT_SPREAD_CODE, FX_CODE  # noqa: E402
from app.repositories.macro_indicators import get_macro, get_macro_window  # noqa: E402
from app.repositories.regime import upsert_regime_snapshot  # noqa: E402
from app.views.regime.judge import judge  # noqa: E402

FX_GAP_WINDOW = 60
VOLATILITY_CODE = "VIX_CLOSE"


def _gap_to_average(window: list[tuple[date, float]]) -> float | None:
    """마지막 값의 창 평균 대비 이격도. 창이 덜 찼거나 평균이 0 이하면 None."""
    if len(window) != FX_GAP_WINDOW:
        return None
    values = [value for _, value in window]
    average = sum(values) / len(values)
    if average > 0:
        return values[-1] / average - 1.0
    return None


def build_indicators(as_of: date, *, trend_index: str) -> dict[str, float | None]:
    """그 시점에 볼 수 있는 지표만 모은다. 없는 것은 None 으로 둔다."""
    fx_gap = _gap_to_average(get_macro_window(FX_CODE, as_of=as_of, lookback_days=FX_GAP_WINDOW))
    trend_gap = _gap_to_average(get_macro_window(trend_index, as_of=as_of, lookback_days=FX_GAP_WINDOW))

    return {
        VOLATILITY_CODE: get_macro(VOLATILITY_CODE, as_of=as_of),
        CREDIT_SPREAD_CODE: get_macro(CREDIT_SPREAD_CODE, as_of=as_of),
        "USDKRW_GAP60": fx_gap,
        "TREND_GAP": trend_gap,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument(
        "--trend-index",
        default="KOSPI200",
        help="스냅샷의 추세 지수 코드. 데이터가 없어도 설정을 기록하기 위해 넣는다.",
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    counts: dict[str, int] = {}
    current = start
    while current <= end:
        indicators = build_indicators(current, trend_index=args.trend_index)
        result = judge(indicators)
        upsert_regime_snapshot(
            as_of=current,
            trend_index=args.trend_index,
            regime_label=result.label,
            intensity=result.intensity,
            threshold_state=result.threshold_state,
        )
        counts[result.label] = counts.get(result.label, 0) + 1
        current += timedelta(days=1)

    total = sum(counts.values())
    print(f"[build_regime] {start} ~ {end} 스냅샷 {total}건")
    for label, count in sorted(counts.items()):
        print(f"[build_regime]   {label:9s} {count:5d}건 ({count / total:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
