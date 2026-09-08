"""501 스텁 라우터 공통 헬퍼. 본체 구현 없이 경로·응답모델·인증만 열어둔
엔드포인트가 전부 같은 방식으로 거부하게 한다 (docs/infra-spec.md 7단계)."""
from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException, status


def raise_not_implemented(
    detail: str = "이번 단계 범위 밖이다 (docs/infra-spec.md 9장 '하지 말 것')",
) -> NoReturn:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=detail)
