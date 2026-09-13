#!/usr/bin/env python3
"""국내 지수 일봉을 거시지표로 적재한다 (FN-406 추세 지수).

**price_daily(ETF 일봉)에는 손대지 않는다.** 이건 지수이고 거시지표라
macro_indicators 에 들어간다(source CHECK 의 'krx' 가 정확히 그 자리다).
ETF 일봉 적재 담당은 회의 안건으로 올라가 있어 여기서 건드리지 않는다.

## 왜 pykrx 가 아니라 FinanceDataReader 인가

2026-09-12 실측: KRX 의 JSON 엔드포인트가 세션 없이 HTTP 400 "LOGOUT" 을 준다.
pykrx 1.2.8 은 import 시 KRX_ID/KRX_PW 를 요구하고, get_index_ohlcv_by_date 는
응답 파싱에서 깨진다(KeyError '지수명'). 즉 **pykrx 는 KRX 계정이 있어야 한다.**
FinanceDataReader 는 인증 없이 같은 지수를 준다. 둘 다 확정 기술 스택 목록에 있다.

## 종가만 넣는다

trend_ma_window 는 Spec 필드(MarketTemperatureRule)다. Spec 마다 윈도우가 다를 수
있으므로 이동평균 이격도는 **판정 로직이 계산한다.** 이격도를 적재하면 윈도우가
바뀔 때마다 전 구간을 다시 채워야 한다.

(T2 의 "피처는 전지표를 미리 계산하고 Spec 은 마스크" 와 방향이 반대로 보이지만
이유가 다르다. 피처는 지표 목록이 유한해서 미리 정할 수 있지만, 이동평균 윈도우는
연속 파라미터라 가능한 값을 미리 다 계산해 둘 수 없다.)

## 구간을 쪼개 재개할 수 있게

상세설계서 1.6 이 국내 시세 수집에 "재시도와 캐시 필수"를 경고했다. 비공식 경로라
3년치를 한 번에 요청했다가 중간에 끊기면 처음부터다. 분기 단위로 끊어 받고,
--resume 이면 이미 채워진 분기는 건너뛴다.

사용:
    DATABASE_URL=... python scripts/ingest_index.py --start 2023-01-01 --end 2025-12-31
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.macro.codes import BY_CODE, estimate_released_at  # noqa: E402
from app.repositories.macro_indicators import get_macro, upsert_macro  # noqa: E402

DEFAULT_CODE = "KOSPI200"


def quarters(start: date, end: date):
    """[start, end] 를 분기 단위 (시작, 끝) 로 자른다."""
    current = start
    while current <= end:
        quarter_end_month = ((current.month - 1) // 3 + 1) * 3
        if quarter_end_month == 12:
            chunk_end = date(current.year, 12, 31)
        else:
            chunk_end = date(current.year, quarter_end_month + 1, 1) - timedelta(days=1)
        yield current, min(chunk_end, end)
        current = chunk_end + timedelta(days=1)


def fetch_chunk(external_id: str, start: date, end: date) -> list[tuple[date, float]]:
    import FinanceDataReader as fdr  # noqa: PLC0415

    frame = fdr.DataReader(external_id, start.isoformat(), end.isoformat())
    if frame is None or frame.empty:
        return []
    points: list[tuple[date, float]] = []
    for index_value, row in zip(frame.index, frame.to_dict("records"), strict=True):
        close = row.get("Close")
        if close is None or close != close:  # NaN
            continue
        points.append((index_value.date(), float(close)))
    return points


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", default=DEFAULT_CODE)
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument(
        "--resume",
        action="store_true",
        help="이미 값이 있는 분기는 건너뛴다 (중간에 끊긴 백필을 이어서)",
    )
    args = parser.parse_args()

    indicator = BY_CODE.get(args.code)
    if indicator is None:
        print(f"[ingest_index] 등록되지 않은 코드: {args.code}", file=sys.stderr)
        return 2

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    total = skipped_quarters = failed_quarters = 0
    for chunk_start, chunk_end in quarters(start, end):
        if args.resume and get_macro(args.code, as_of=chunk_end + timedelta(days=7)) is not None:
            # 분기 끝 시점에 이미 값이 보이면 그 분기는 채워져 있다고 본다.
            if get_macro(args.code, as_of=chunk_start + timedelta(days=7)) is not None:
                skipped_quarters += 1
                continue

        try:
            points = fetch_chunk(indicator.external_id, chunk_start, chunk_end)
        except Exception as exc:  # noqa: BLE001 — 비공식 경로라 어떤 예외든 분기만 건너뛴다
            print(
                f"[ingest_index] {chunk_start}~{chunk_end} 실패: {type(exc).__name__}: {exc} "
                f"(--resume 으로 이 분기만 다시 받을 수 있다)",
                file=sys.stderr,
            )
            failed_quarters += 1
            continue

        for as_of, value in points:
            upsert_macro(
                args.code,
                as_of=as_of,
                value=value,
                released_at=estimate_released_at(args.code, as_of),
                source=indicator.source,
            )
        print(f"[ingest_index] {chunk_start}~{chunk_end}: {len(points)}점")
        total += len(points)

    print(
        f"[ingest_index] {args.code} 적재 {total}점 · 건너뛴 분기 {skipped_quarters} "
        f"· 실패 분기 {failed_quarters}"
    )
    return 1 if failed_quarters else 0


if __name__ == "__main__":
    raise SystemExit(main())
