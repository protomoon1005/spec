"""근거 기사 조회 — 감성 관점 evidence.article_ids 의 제목 · 언론사 · 링크.

본문은 내보내지 않는다(저장도 안 한다 — docs/known-issues.md "뉴스 표" 항목).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.security import AuthUser, require_any_role
from app.repositories import news

router = APIRouter(prefix="/news", tags=["news"])
MAX_IDS = 50


class ArticleOut(BaseModel):
    article_id: int
    title: str | None
    source: str | None
    url: str | None
    published_at: datetime


@router.get("/articles", response_model=list[ArticleOut], summary="근거 기사 제목 조회")
def get_articles(
    ids: str = Query(..., description="쉼표로 구분한 article_id (최대 50개)", examples=["1,2,3"]),
    user: AuthUser = Depends(require_any_role),
) -> list[ArticleOut]:
    try:
        parsed = [int(part) for part in ids.split(",") if part.strip()]
    except ValueError:
        raise HTTPException(422, "ids 는 쉼표로 구분한 정수여야 한다") from None
    if not parsed or len(parsed) > MAX_IDS:
        raise HTTPException(422, f"ids 는 1~{MAX_IDS}개여야 한다")
    return [ArticleOut(**vars(row)) for row in news.get_articles(parsed)]
