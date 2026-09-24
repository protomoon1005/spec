"""BBL(Building Block Library) 검색 — 전략서에 넣을 부품 찾기 (M1 3단계).

시점 규약 대상이 아니다. 블록은 시점 자산이 아니라 카탈로그라서, 조회에 시점을
받지 않는다(users.py 와 같은 이유).

## 태그로 거르고 맞은 개수로 정렬한다. 그게 전부다

태그 점수와 벡터 점수를 가중합하지 않는다. 가중치를 섞으면 튜닝할 값이 늘고
"왜 이 블록이 뽑혔는가" 를 설명하기 어려워진다. 순서는 이렇다.

  1. 태그가 하나라도 맞는 블록만 남긴다
  2. 맞은 태그가 많은 순
  3. 같으면 블록 식별자 순

**3번이 없으면 같은 질의에 순서가 흔들리고, 그러면 같은 입력에 다른 전략서가
나온다.** 재현성은 이 프로젝트가 보장해야 하는 성질이다.

## 벡터 검색은 아직 켜지 않았다

칸(768차원)과 색인이 이미 만들어져 있지만 블록이 서른 개 남짓이라 태그만으로
거의 다 잡힌다. 켜는 기준은 "의도 추출이 뽑아낸 키워드조차 사전에 없는" 사례가
실제로 나올 때다. 그때 붙이는 비용은 스크립트 하나다 —
repositories/news.py 의 유사 기사 조회가 그대로 본보기가 된다.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from app.core.db import get_engine

BLOCK_TYPES = ("indicator", "filter", "rebalance", "lens", "etf_trait")

# 태그 접두어. 늘리지 않는다 — docs/m1_milestone.md 3단계 참고.
TAG_PREFIXES = ("kw:", "asset:", "sector:", "country:", "risk:")


@dataclass(frozen=True)
class Block:
    block_id: str
    block_type: str
    title: str | None
    description: str | None
    params_schema: dict | None
    tags: tuple[str, ...]
    matched_tags: int = 0


# 맞은 태그 수(matched)를 질의에서 같이 센다. 파이썬에서 또 세면 세는 규칙이 두 곳이
# 되고, 한쪽만 고치면 정렬과 표시가 어긋난다. :tags 를 안 넘기는 호출에서는 0 이다.
_SELECT = """
    SELECT b.block_id, b.block_type, b.title, b.description, b.params_schema,
           COALESCE(ARRAY_AGG(t.tag ORDER BY t.tag) FILTER (WHERE t.tag IS NOT NULL), '{}') AS tags,
           COUNT(*) FILTER (WHERE t.tag = ANY(COALESCE(:tags, ARRAY[]::varchar[]))) AS matched
      FROM bbl_blocks b
      LEFT JOIN bbl_tags t ON t.block_id = b.block_id
     WHERE b.active = true
"""


def get_block(block_id: str) -> Block | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text(_SELECT + " AND b.block_id = :block_id GROUP BY b.block_id"),
            {"block_id": block_id, "tags": None},
        ).one_or_none()
    return _as_block(row) if row is not None else None


def list_blocks(block_type: str | None = None) -> list[Block]:
    """종류별 전체 목록. 식별자 순으로 나온다."""
    clause = " AND b.block_type = :block_type" if block_type else ""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(_SELECT + clause + " GROUP BY b.block_id ORDER BY b.block_id"),
            {"block_type": block_type, "tags": None} if block_type else {"tags": None},
        ).all()
    return [_as_block(row) for row in rows]


def list_tags(prefix: str) -> list[str]:
    """접두어가 같은 태그의 값만 모아 준다. 중복 없이, 항상 같은 순서로.

    의도 추출이 LLM 에게 "이 낱말 중에서 고르라" 고 줄 목록이다. 자유 문자열을
    받으면 사전에 없는 말이 들어와 아무 블록도 안 잡힌다.
    """
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT t.tag FROM bbl_tags t
                  JOIN bbl_blocks b ON b.block_id = t.block_id
                 WHERE b.active = true AND t.tag LIKE :prefix
                 ORDER BY t.tag
                """
            ),
            {"prefix": f"{prefix}%"},
        ).all()
    return [row.tag.split(":", 1)[1] for row in rows]


def search_blocks(
    tags: list[str], *, block_type: str | None = None, limit: int = 20
) -> list[Block]:
    """태그가 하나라도 맞는 블록을, 많이 맞은 순으로 돌려준다.

    태그가 비면 빈 목록이다 — 아무 태그도 없는데 전부 돌려주면 "찾았다"와
    "못 찾았다"가 구분되지 않는다. 전체가 필요하면 list_blocks 를 쓴다.
    """
    if not tags:
        return []

    clause = " AND b.block_type = :block_type" if block_type else ""
    params: dict = {"tags": list(tags), "limit": limit}
    if block_type:
        params["block_type"] = block_type

    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                _SELECT
                + clause
                + """
                 GROUP BY b.block_id
                HAVING COUNT(*) FILTER (WHERE t.tag = ANY(:tags)) > 0
                 ORDER BY COUNT(*) FILTER (WHERE t.tag = ANY(:tags)) DESC, b.block_id
                 LIMIT :limit
                """
            ),
            params,
        ).all()
    return [_as_block(row, matched_tags=row.matched) for row in rows]


def _as_block(row, *, matched_tags: int = 0) -> Block:
    return Block(
        block_id=row.block_id,
        block_type=row.block_type,
        title=row.title,
        description=row.description,
        params_schema=row.params_schema,
        tags=tuple(row.tags),
        matched_tags=matched_tags,
    )
