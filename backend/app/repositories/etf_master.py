"""종목 마스터 조회 (M1 4단계).

시점 규약 대상이 아니다 — 종목 원장은 시점 자산이 아니라 카탈로그다.

## 순서를 항상 고정한다

조회 결과의 순서가 흔들리면 후보 목록이 흔들리고, 그러면 같은 요청에 다른 전략서가
나온다. 모든 질의가 `ticker` 로 정렬해 끝난다.

**아쉬운 점**: "대표적인 종목부터" 고르고 싶은데 순자산 칸이 표에 없다.
원본 파일(frontend/data/universe.csv)에는 있지만 표에 칸이 없어 적재하지 못했다.
지금은 종목코드 순이라 사실상 임의 순서다 — README 의 알려진 문제에 적어 뒀다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text

from app.core.db import get_engine

_COLUMNS = """
    ticker, name, sector, group_id, risk_tag, is_leveraged, active, delisted_date
"""


@dataclass(frozen=True)
class EtfRecord:
    ticker: str
    name: str
    sector: str | None
    group_id: str
    risk_tag: str | None
    is_leveraged: bool
    active: bool
    delisted_date: date | None


def get_by_tickers(tickers: list[str]) -> dict[str, EtfRecord]:
    """종목코드로 찾는다. 없는 코드는 결과에 안 들어간다 — 호출부가 구분해야 한다.

    **활성 여부로 거르지 않는다.** 상장폐지 종목도 돌려줘야 "그런 종목 없다" 와
    "상장폐지라 못 담는다" 를 구분해 답할 수 있다.
    """
    if not tickers:
        return {}
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(f"SELECT {_COLUMNS} FROM etf_master WHERE ticker = ANY(:tickers) ORDER BY ticker"),
            {"tickers": list(tickers)},
        ).all()
    return {row.ticker: _as_record(row) for row in rows}


def find_by_name(fragment: str, *, limit: int = 10) -> list[EtfRecord]:
    """종목명 일부로 찾는다. 사용자가 코드 대신 이름을 말할 때 쓴다.

    **대소문자와 띄어쓰기를 무시한다.** 양쪽에서 공백을 뺀 뒤 비교하므로
    "kodex200" 으로도 "KODEX 200" 이 걸린다. 사람이 종목명을 정확히 띄어 쓰지 않는다.

    여기서도 활성 여부로 거르지 않는다 — 상장폐지 종목을 지목했을 때
    "없다" 가 아니라 "폐지됐다" 고 답해야 한다.
    """
    squished = "".join(fragment.split())
    if not squished:
        return []
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT {_COLUMNS} FROM etf_master
                 WHERE REPLACE(name, ' ', '') ILIKE :pattern
                 ORDER BY ticker
                 LIMIT :limit
                """
            ),
            {"pattern": f"%{squished}%", "limit": limit},
        ).all()
    return [_as_record(row) for row in rows]


def search(
    *,
    group_ids: list[str] | None = None,
    sectors: list[str] | None = None,
    risk_tags: list[str] | None = None,
    name_pattern: str | None = None,
    include_leveraged: bool = False,
    limit: int = 200,
) -> list[EtfRecord]:
    """조건으로 추린다. 조건을 안 주면 활성 종목 전부.

    **여기서는 활성 종목만 돌려준다.** 후보를 고르는 경로라 상장폐지 종목이
    섞이면 안 된다. 지목한 종목을 확인하는 경로는 get_by_tickers 를 쓴다.

    name_pattern 은 BBL 의 ETF 특성 블록이 들고 있는 정규식을 그대로 받는다.
    """
    clauses = ["active = true"]
    params: dict = {"limit": limit}

    if group_ids:
        clauses.append("group_id = ANY(:group_ids)")
        params["group_ids"] = list(group_ids)
    if sectors:
        clauses.append("sector = ANY(:sectors)")
        params["sectors"] = list(sectors)
    if risk_tags:
        clauses.append("risk_tag = ANY(:risk_tags)")
        params["risk_tags"] = list(risk_tags)
    if name_pattern:
        clauses.append("name ~ :name_pattern")
        params["name_pattern"] = name_pattern
    if not include_leveraged:
        clauses.append("is_leveraged = false")

    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM etf_master"
                f" WHERE {' AND '.join(clauses)}"
                " ORDER BY ticker LIMIT :limit"
            ),
            params,
        ).all()
    return [_as_record(row) for row in rows]


def _as_record(row) -> EtfRecord:
    return EtfRecord(**row._mapping)
