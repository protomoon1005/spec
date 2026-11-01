"""계약 ② 관점별 스코어 출력 형식 (docs/infra-spec.md 6단계 표 ②).

세 관점(시장분석 · 감성 · 시장온도) 모두 보정 확률(calibrated_prob)을 낸다 —
관점 경연(Hedge)이 Brier score로 채점하므로 원점수만으로는 부족하다는 것이
정본의 명시적 요구사항이다.

이 파일의 목업은 M3(판단 계층, 이번 범위 밖) 실구현을 기다리지 않고 M4가
백테스트 파이프라인을 먼저 조립할 수 있도록 하는 고정 시드 목업이다.
random 모듈은 쓰지 않는다 — 해시 기반으로 [-1,1]/[0,1] 구간에 결정론적으로
펼친다 (같은 (view_type, ticker, as_of) 입력엔 항상 같은 출력).
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ViewType = Literal["market", "sentiment", "regime"]


class ViewScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view_type: ViewType
    ticker: str = Field(min_length=1)
    raw_score: float = Field(ge=-1, le=1)
    calibrated_prob: float = Field(ge=0, le=1)
    evidence: dict = Field(default_factory=dict)


def _deterministic_unit(*parts: str) -> float:
    """parts를 이어붙인 문자열을 해시해 [0, 1) 구간의 결정론적 실수로 편다.

    random 모듈 대신 sha256 다이제스트의 앞 8바이트를 정수로 읽어 정규화한다.
    같은 parts는 프로세스·플랫폼에 관계없이 항상 같은 값을 낸다.
    """
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    as_int = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return as_int / float(2**64)


def mock_view_score(view_type: ViewType, ticker: str, *, as_of: date) -> ViewScore:
    """단일 (관점, 종목, 시점)에 대한 결정론적 목업 ViewScore를 만든다."""
    key = (view_type, ticker, as_of.isoformat())
    unit = _deterministic_unit(*key)
    raw_score = round(unit * 2 - 1, 4)  # [0,1) -> [-1,1)
    calibrated_prob = round((unit + _deterministic_unit(*key, "calibration")) / 2, 4)
    return ViewScore(
        view_type=view_type,
        ticker=ticker,
        raw_score=raw_score,
        calibrated_prob=calibrated_prob,
        evidence={"source": "mock", "as_of": as_of.isoformat(), "seed_key": "|".join(key)},
    )


def mock_view_scores(view_type: ViewType, tickers: list[str], *, as_of: date) -> list[ViewScore]:
    """유니버스 전체에 대한 결정론적 목업 ViewScore 목록."""
    return [mock_view_score(view_type, ticker, as_of=as_of) for ticker in tickers]
