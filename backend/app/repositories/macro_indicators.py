"""macro_indicators as_of 질의와 적재. docs/db-erd.md 4.5가 정본이다."""
from __future__ import annotations

from datetime import date

from sqlalchemy import text

from app.core.db import get_engine


def get_macro(indicator_code: str, *, as_of: date) -> float | None:
    """거시지표는 공표 시차가 있으므로 released_at 기준으로도 걸러야 한다."""
    with get_engine().connect() as conn:
        value = conn.execute(
            text(
                """
                SELECT value
                  FROM macro_indicators
                 WHERE indicator_code = :indicator_code
                   AND as_of       <= :as_of
                   AND released_at <= :as_of
                 ORDER BY as_of DESC
                 LIMIT 1
                """
            ),
            {"indicator_code": indicator_code, "as_of": as_of},
        ).scalar_one_or_none()
    return float(value) if value is not None else None


def upsert_macro(
    indicator_code: str,
    *,
    as_of: date,
    value: float,
    released_at: date,
    source: str,
) -> None:
    """거시지표 한 점을 적재한다. 같은 (코드, 시점)이면 덮어쓴다.

    **view_weights 와 달리 append-only 가 아니다.** 거시지표는 잠정치가 확정치로
    개정되는 일이 정상이라 같은 as_of 를 다시 채우는 것이 맞다.

    **다만 개정이 일어나면 과거 백테스트 결과가 바뀐다.** 어제 돌린 백테스트와
    오늘 돌린 백테스트가 달라질 수 있다는 뜻이다. 개정 이력을 보존하려면 스키마에
    vintage 축이 필요한데(그건 스키마 변경이라 지금 하지 않는다), 지금은 이 한계를
    문서에 기록만 해 둔다 — README "거시지표 개정" 절.

    released_at 은 공표 시점이고 as_of 와 다르다. 조회가 released_at <= as_of 로도
    거르므로, 이 값이 비거나 너무 이르면 공표 전 데이터가 과거 판단에 섞인다.
    FRED 가 공표일을 주지 않아 app/macro/codes.py 의 규칙으로 **추정**해 채운다.
    """
    if released_at < as_of:
        # 공표가 지표 시점보다 앞설 수는 없다. 이걸 허용하면 미래를 미리 보게 된다.
        raise ValueError(f"released_at({released_at}) 이 as_of({as_of}) 보다 빠르다")

    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO macro_indicators (indicator_code, as_of, value, released_at, source)
                VALUES (:indicator_code, :as_of, :value, :released_at, :source)
                ON CONFLICT (indicator_code, as_of)
                DO UPDATE SET value = EXCLUDED.value,
                              released_at = EXCLUDED.released_at,
                              source = EXCLUDED.source
                """
            ),
            {
                "indicator_code": indicator_code,
                "as_of": as_of,
                "value": value,
                "released_at": released_at,
                "source": source,
            },
        )


def get_macro_window(
    indicator_code: str, *, as_of: date, lookback_days: int
) -> list[tuple[date, float]]:
    """as_of 기준 최근 lookback_days 일의 (시점, 값) 목록. 오래된 것부터.

    이동평균 이격도처럼 창이 필요한 파생값을 만들기 위한 조회다. 단건 조회를
    반복하면 as_of 마다 수십 번씩 질의하게 되고, 그렇다고 응용 코드가 테이블을
    직접 읽으면 시점 규약이 깨진다 — 그래서 창 조회도 저장소 계층에 둔다.

    get_macro 와 같은 두 조건을 그대로 건다: as_of <= 이고 released_at <= as_of.
    공표 전 값이 과거 판단에 섞이면 미래를 미리 보는 것이다.
    """
    if lookback_days <= 0:
        raise ValueError(f"lookback_days 는 1 이상이어야 한다: {lookback_days!r}")

    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT as_of, value
                  FROM (
                        SELECT as_of, value
                          FROM macro_indicators
                         WHERE indicator_code = :indicator_code
                           AND as_of       <= :as_of
                           AND released_at <= :as_of
                         ORDER BY as_of DESC
                         LIMIT :lookback_days
                       ) recent
                 ORDER BY as_of ASC
                """
            ),
            {"indicator_code": indicator_code, "as_of": as_of, "lookback_days": lookback_days},
        ).all()
    return [(row.as_of, float(row.value)) for row in rows]
