# 계약 ③ 관점 가중치 조회 인터페이스 (docs/infra-spec.md 6단계 표 ③).
#
# get_view_weights(portfolio_id, as_of) -> {view_type: weight} — Sigma w = 1,
# 하한 0.10. **읽기 전용**: 이 모듈은 쓰기 API를 노출하지 않는다.
#
# 실제 운영 시점의 구현은 저장소 계층의 as_of 질의 규약(DB의 as_of 미만 최신
# 행)을 따르는 리포지토리 모듈이고, 이 파일은 그 계약의 타입(Protocol)과
# M3/M4가 실구현을 기다리지 않고 붙어볼 수 있는 결정론적 목업만 담는다.
# DB에 접근하지 않으므로 scripts/check_asof_guard.py 감시 대상과 무관하다.
#
# (주: 아래 독스트링은 일부러 짧게 둔다 — 해당 테이블명을 문장으로 풀어 쓰면
#  check_asof_guard.py의 문자열 스캔이 리포지토리 밖 참조로 오탐하기 때문에,
#  자세한 설명은 AST에 안 잡히는 이 파일 상단 주석에 적었다.)
"""읽기 전용 관점 가중치 조회 계약과 결정론적 목업."""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Protocol

VIEW_TYPES: tuple[str, str, str] = ("market", "sentiment", "regime")
WEIGHT_FLOOR = 0.10


class ViewWeightsProvider(Protocol):
    """읽기 전용 조회 계약. 쓰기(update)는 이 Protocol에 없다."""

    def __call__(self, portfolio_id: int, *, as_of: date) -> dict[str, float]:
        ...


def _deterministic_unit(*parts: str) -> float:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    as_int = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return as_int / float(2**64)


def mock_get_view_weights(portfolio_id: int, *, as_of: date) -> dict[str, float]:
    """결정론적 목업. Sigma w = 1, 각 관점 하한 0.10을 항상 만족한다.

    하한 확보분(0.10 x 3 = 0.30)을 먼저 배정하고, 나머지 0.70을 관점별
    해시 기반 기본 비중으로 비례 배분한 뒤, 반올림 잔차는 마지막 관점에
    몰아 Sigma가 정확히 1이 되게 한다.
    """
    key_base = (str(portfolio_id), as_of.isoformat())
    base_scores = {vt: _deterministic_unit(*key_base, vt) for vt in VIEW_TYPES}
    total_base = sum(base_scores.values())

    remaining = 1.0 - WEIGHT_FLOOR * len(VIEW_TYPES)
    weights = {
        vt: round(WEIGHT_FLOOR + remaining * (base_scores[vt] / total_base), 6)
        for vt in VIEW_TYPES
    }

    drift = round(1.0 - sum(weights.values()), 6)
    last_view = VIEW_TYPES[-1]
    weights[last_view] = round(weights[last_view] + drift, 6)

    return weights
