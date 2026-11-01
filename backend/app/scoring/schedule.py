"""워크포워드 재학습 일정 — 두 관점(시장분석·시장온도)이 같은 달력을 쓴다.

확정 규칙(2026-09-24, app/views/market/labels.py 머리 주석): expanding 창,
20거래일마다 재학습, embargo 20. 재학습 시점(cutoff)은 달력의 첫 거래일부터
RETRAIN_EVERY 거래일 간격 격자에 놓인다. 격자가 실행과 무관하게 고정되어 있어서
어느 백테스트가 어느 as_of 에서 물어도 같은 cutoff → 같은 모델이 나온다.
그래서 모델을 cutoff 로 캐시해도 재현성이 깨지지 않는다.
"""
from __future__ import annotations

from datetime import date

from app.repositories import price_daily

RETRAIN_EVERY = 20
EMBARGO = 20
# 거래일 달력의 시작. 가격 정본이 2019-01-02 부터다(data/prices.meta.json).
CALENDAR_START = date(2019, 1, 1)


def cutoff_for(as_of: date) -> date | None:
    """as_of 에 쓸 모델의 재학습 시점. as_of 이하 격자점 중 가장 늦은 것.

    as_of 이후 거래일은 조회하지 않는다 — 격자 위치는 앞에서부터 세므로 미래가
    필요 없다. 달력을 캐시하지 않는다: 그날 가격이 적재되기 전에 부른 결과가 프로세스
    수명 내내 남으면 같은 as_of 가 다른 cutoff 를 받는다. 조회는 거래일 2천 행이 안 된다.
    """
    calendar = price_daily.get_trade_dates(start=CALENDAR_START, as_of=as_of)
    if not calendar:
        return None
    return calendar[((len(calendar) - 1) // RETRAIN_EVERY) * RETRAIN_EVERY]
