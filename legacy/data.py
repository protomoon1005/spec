"""FinanceDataReader 일봉 + parquet 캐시 (docs/demo-spec.md §B7).

- 캐시가 없을 때만 FDR 로 전체 이력을 한 번 받아 data/cache/{ticker}.parquet 에 저장한다.
- 캐시가 있으면 네트워크를 쓰지 않는다 (스냅샷 고정). 갱신하려면 refresh=True.
- 메타(받은 날짜·행 수·내용 해시)는 data/cache/{ticker}.meta.json 에 기록한다.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache"
FETCH_START = "2000-01-01"
COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def frame_hash(df: pd.DataFrame) -> str:
    """OHLCV 내용 해시 (플랫폼 무관하도록 고정 포맷 CSV 로 직렬화)."""
    csv = df[COLUMNS].to_csv(float_format="%.6f", date_format="%Y-%m-%d", lineterminator="\n")
    return hashlib.sha256(csv.encode("utf-8")).hexdigest()


def normalize(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """열 정리 + 거래정지 행(시가 0) 제거. (정리된 df, 제거 행 수) 반환."""
    df = raw[COLUMNS].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "Date"
    df = df[~df.index.duplicated(keep="first")].sort_index()
    df = df.astype({"Open": float, "High": float, "Low": float, "Close": float, "Volume": float})
    bad = (df["Open"] <= 0) | df[["Open", "High", "Low", "Close"]].isna().any(axis=1)
    return df[~bad], int(bad.sum())


def _fdr_fetch(ticker: str) -> pd.DataFrame:
    import FinanceDataReader as fdr  # 지연 import: 테스트는 네트워크·FDR 없이 동작

    return fdr.DataReader(ticker, FETCH_START)


def load_ohlcv(
    ticker: str,
    cache_dir: Path | str = CACHE_DIR,
    refresh: bool = False,
    fetcher: Optional[Callable[[str], pd.DataFrame]] = None,
) -> tuple[pd.DataFrame, dict]:
    """전체 일봉 이력과 스냅샷 메타를 반환. 기간 자르기는 백테스트가 한다(지표 워밍업 보존)."""
    cache_dir = Path(cache_dir)
    pq = cache_dir / f"{ticker}.parquet"
    meta_path = cache_dir / f"{ticker}.meta.json"
    if pq.exists() and meta_path.exists() and not refresh:
        df = pd.read_parquet(pq)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if frame_hash(df) != meta.get("sha256"):
            raise RuntimeError(f"{pq} 내용이 메타 해시와 다릅니다. 캐시가 수정됐습니다.")
        return df, meta

    raw = (fetcher or _fdr_fetch)(ticker)
    if raw is None or len(raw) == 0:
        raise RuntimeError(f"{ticker}: 데이터를 받지 못했습니다.")
    df, dropped = normalize(raw)
    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(pq)
    meta = {
        "ticker": ticker,
        "source": "FinanceDataReader" if fetcher is None else "custom",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "first": df.index[0].strftime("%Y-%m-%d"),
        "last": df.index[-1].strftime("%Y-%m-%d"),
        "rows": len(df),
        "dropped_zero_open_rows": dropped,
        "sha256": frame_hash(df),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return df, meta
