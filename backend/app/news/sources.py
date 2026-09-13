# 뉴스 RSS 소스 목록. .env 가 아니라 저장소에 커밋되는 상수다 —
# "어떤 소스를 봤는가"는 재현 조건의 일부라 환경마다 달라지면 안 된다.
#
# 수집 범위 원칙 (T10 조사와 같은 기준):
#   RSS 피드 자체는 언론사가 배포(syndication)를 목적으로 공개한 것이라 읽는다.
#   기사 페이지 본문을 긁는 스크래핑은 하지 않는다 — 이용약관·robots.txt 확인이
#   필요한 영역이고, 판단이 애매하면 멈춰서 묻기로 했다.
#   따라서 본문은 RSS 가 주는 description/content 까지만 쓴다.
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NewsSource:
    source_id: str
    label: str
    url: str


# 국내 경제·시장 섹션 피드. 데모 범위라 소수만 둔다.
RSS_SOURCES: tuple[NewsSource, ...] = (
    NewsSource("yna_economy", "연합뉴스 경제", "https://www.yna.co.kr/rss/economy.xml"),
    NewsSource("hankyung_economy", "한국경제 경제", "https://www.hankyung.com/feed/economy"),
    NewsSource("hankyung_finance", "한국경제 증권", "https://www.hankyung.com/feed/finance"),
    NewsSource("mk_economy", "매일경제 경제", "https://www.mk.co.kr/rss/30100041/"),
    NewsSource("mk_stock", "매일경제 증권", "https://www.mk.co.kr/rss/50200011/"),
)

# 2026-09-12 실측에서 빠진 소스. 되살리려면 접근 방법부터 다시 확인해야 한다.
#   yna_market  https://www.yna.co.kr/rss/stock.xml   -> HTTP 404 (경로가 없다)
#   edaily      https://rss.edaily.co.kr/edaily_news.xml -> Connection reset by peer
# 자세한 실측은 docs/news-archive-feasibility.md 참조.

SOURCE_BY_ID: dict[str, NewsSource] = {source.source_id: source for source in RSS_SOURCES}
