#!/usr/bin/env python3
"""거시지표를 받아 적재한다 (FN-406 입력).

지표 코드와 소스 매핑은 backend/app/macro/codes.py 가 정본이다. 적재는
저장소 계층의 쓰기 함수를 거친다(그 테이블은 as_of 가드 대상이다).

## released_at 은 추정값이다

FRED 는 관측값에 공표일을 함께 주지 않는다(ALFRED vintage 질의를 쓰면 받을 수
있으나 별도 경로다). 그래서 codes.py 의 release_lag_days 로 추정해 채운다.
일간 지표는 +1일 — 거래일 d 의 값은 장 마감 뒤 확정되므로 d 에는 아직 못 본다고
본다. **늦게 잡으면 판단이 보수적으로 틀리고, 빠르게 잡으면 미래를 미리 보는
것이 된다. 후자가 훨씬 나쁘다.**

정확한 공표일을 받게 되면 codes.py 에서 released_at_is_estimated=True 인 코드를
다시 채워야 한다. source 컬럼은 ecos|fred|krx CHECK 가 걸려 있어 "추정"을 거기
적을 수 없어서, 이 주석과 README 가 그 기록이다.

## 인증키

FRED_API_KEY / ECOS_API_KEY 를 .env 로 받는다(.env.example 참조).

키가 없을 때를 위해 --public-csv 를 뒀다. FRED 가 그래프용으로 공개하는 CSV
엔드포인트를 읽는 개발용 경로이고, **정식 경로가 아니다** — 키가 발급되면
기본 경로(API)로 돌려야 한다.

사용:
    DATABASE_URL=... FRED_API_KEY=... python scripts/ingest_macro.py
    DATABASE_URL=... python scripts/ingest_macro.py --public-csv --start 2023-01-01
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.macro.codes import INDICATORS, MacroIndicator, estimate_released_at  # noqa: E402

USER_AGENT = "Mozilla/5.0 (spec-graduation-project; macro ingest)"
TIMEOUT_SECONDS = 30


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        return response.read()


def fetch_fred_api(indicator: MacroIndicator, *, api_key: str, start: str, end: str):
    params = {
        "series_id": indicator.external_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start,
        "observation_end": end,
    }
    url = "https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode(params)
    payload = json.loads(_get(url))
    for row in payload.get("observations", []):
        if row.get("value") in (".", "", None):
            continue
        yield date.fromisoformat(row["date"]), float(row["value"])


def fetch_fred_public_csv(indicator: MacroIndicator, *, start: str, end: str):
    url = (
        "https://fred.stlouisfed.org/graph/fredgraph.csv?"
        + urllib.parse.urlencode({"id": indicator.external_id, "cosd": start, "coed": end})
    )
    text = _get(url).decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return
    columns = list(rows[0].keys())
    for row in rows:
        raw = row[columns[1]]
        if raw in (".", "", None):
            continue
        yield datetime.strptime(row[columns[0]], "%Y-%m-%d").date(), float(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--public-csv",
        action="store_true",
        help="FRED 공개 CSV 엔드포인트를 쓴다(개발용). 키가 발급되면 쓰지 마라.",
    )
    args = parser.parse_args()

    fred_key = os.environ.get("FRED_API_KEY")
    if not args.public_csv and not fred_key:
        print(
            "[ingest_macro] FRED_API_KEY 가 없다. 키를 발급받아 .env 에 넣거나 "
            "개발용으로 --public-csv 를 쓴다.",
            file=sys.stderr,
        )
        return 2

    total = 0
    for indicator in INDICATORS:
        if not indicator.available:
            print(f"[ingest_macro] {indicator.code}: 건너뜀 — {indicator.note}")
            continue
        if indicator.source != "fred":
            print(f"[ingest_macro] {indicator.code}: {indicator.source} 경로는 아직 미구현이다")
            continue

        if args.public_csv:
            points = list(fetch_fred_public_csv(indicator, start=args.start, end=args.end))
        else:
            points = list(fetch_fred_api(indicator, api_key=fred_key, start=args.start, end=args.end))

        print(f"[ingest_macro] {indicator.code}: {len(points)}점 ({args.start} ~ {args.end})")
        if args.dry_run:
            continue

        from app.repositories.macro_indicators import upsert_macro  # noqa: PLC0415

        for as_of, value in points:
            upsert_macro(
                indicator.code,
                as_of=as_of,
                value=value,
                released_at=estimate_released_at(indicator.code, as_of),
                source=indicator.source,
            )
        total += len(points)

    print(f"[ingest_macro] 적재 {total}점 (released_at 은 추정값이다 — 위 주석 참조)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
