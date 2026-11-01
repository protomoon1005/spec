#!/usr/bin/env python3
"""가격 스냅샷을 price_daily 에 적재한다 (기반 정리 3단계, docs/pivot-plan.md 3.2).

## 하는 일

1. data/prices_snapshot.json(메타)을 읽어 스냅샷 파일 이름 · 해시를 안다
2. data/snapshot/<파일>이 없으면 메타의 url(GitHub Release)에서 내려받는다
3. 파일 해시(file_sha256)를 맞춰 본다 — 깨졌거나 다른 버전이면 멈춘다
4. 한 트랜잭션으로 적재한다(price_daily.copy_price_bars). 같은 (종목, 거래일)이면 덮어쓴다
5. 적재한 DB 의 내용 해시를 메타의 content_sha256 과 맞춰 본다. 같으면 누가 빌드해도 같은 가격이다

## 왜 API 를 부르지 않는가

가격을 API 에서 받는 것은 스냅샷을 만드는 fetch_prices.py 한 번뿐이다. 각자 API 를 부르면
받은 날 · 수정주가 재계산에 따라 가격이 갈라진다. 이 스크립트는 파일만 읽는다.

## atr_14 · nav 는 채우지 않는다

atr_14 는 app/backtest/features.py 가 계산하는 값이라 DB 에 두면 정의가 두 곳이 된다.
nav 는 원천이 주지 않는다(시장가격만 준다).

사용 (api 컨테이너 안):
    python /repo/scripts/ingest_prices.py                     # 메타가 가리키는 스냅샷
    python /repo/scripts/ingest_prices.py --file 다른.parquet  # 파일을 직접 지정(해시 확인은 그대로)
    DATABASE_URL=... python /repo/scripts/ingest_prices.py     # 다른 DB(테스트 DB 등)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.repositories.price_daily import (  # noqa: E402
    copy_price_bars,
    count_price_rows,
    stored_price_content_sha256,
)

META = ROOT / "data" / "prices_snapshot.json"
SNAPSHOT_DIR = ROOT / "data" / "snapshot"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate(meta: dict, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    path = SNAPSHOT_DIR / meta["file"]
    if path.exists():
        return path
    url = meta.get("url")
    if not url:
        raise SystemExit(
            f"[ingest_prices] {path} 가 없고 메타에 url 도 없다. "
            "스냅샷 파일을 data/snapshot/ 에 두거나 "
            "GitHub Release 주소를 data/prices_snapshot.json 의 url 에 적어라"
        )
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    print(f"[ingest_prices] 내려받는 중: {url}")
    urllib.request.urlretrieve(url, partial)
    partial.replace(path)
    return path


def bars(path: Path):
    import pandas as pd

    frame = pd.read_parquet(path)
    frame = frame.sort_values(["ticker", "trade_date"])
    for row in frame.itertuples(index=False):
        yield {
            "ticker": row.ticker,
            "trade_date": row.trade_date,
            "open": None if pd.isna(row.open) else float(row.open),
            "high": None if pd.isna(row.high) else float(row.high),
            "low": None if pd.isna(row.low) else float(row.low),
            "close": float(row.close),
            "volume": int(row.volume),
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--meta", type=Path, default=META)
    parser.add_argument("--file", type=Path, default=None, help="스냅샷 파일을 직접 지정")
    parser.add_argument(
        "--skip-content-check", action="store_true", help="적재 뒤 DB 내용 해시 확인을 건너뛴다"
    )
    args = parser.parse_args()

    meta = json.loads(args.meta.read_text(encoding="utf-8"))
    path = locate(meta, args.file)

    got = file_sha256(path)
    if got != meta["file_sha256"]:
        raise SystemExit(
            f"[ingest_prices] {path.name} 해시가 메타와 다르다 — 깨졌거나 다른 버전이다.\n"
            f"  메타 {meta['file_sha256']}\n  파일 {got}"
        )

    t0 = time.perf_counter()
    loaded = copy_price_bars(bars(path))
    print(f"[ingest_prices] {meta['version']} · {loaded:,}행 적재 · {time.perf_counter() - t0:.0f}초")

    if args.skip_content_check:
        return 0
    total = count_price_rows()
    if total != meta["rows"]:
        raise SystemExit(
            f"[ingest_prices] price_daily 가 {total:,}행인데 스냅샷은 {meta['rows']:,}행이다. "
            "스냅샷 밖의 가격이 섞여 있으면 내용 해시가 맞지 않는다 — 빈 DB 에 적재해라(scripts/build_db.sh)"
        )
    stored = stored_price_content_sha256()
    if stored != meta["content_sha256"]:
        raise SystemExit(
            f"[ingest_prices] 적재한 가격의 내용 해시가 스냅샷과 다르다.\n"
            f"  메타 {meta['content_sha256']}\n  DB   {stored}"
        )
    print(f"[ingest_prices] 내용 해시 일치 {stored[:12]}… — 같은 스냅샷이면 누가 빌드해도 같은 가격이다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
