"""GET /health — DB·Redis·MinIO·LLM 백엔드 연결 상태를 각각 보고한다
(docs/infra-spec.md 7단계, 8단계 수용 기준)."""
from __future__ import annotations

import httpx
import redis
from fastapi import APIRouter

from app.core.config import get_settings
from app.llm.client import get_llm_client
from app.repositories.health import ping_database

router = APIRouter(tags=["health"])


def _check_database() -> str:
    try:
        ping_database()
        return "ok"
    except Exception:  # noqa: BLE001 — 헬스체크는 원인 불문 down으로만 보고한다
        return "down"


def _check_redis() -> str:
    settings = get_settings()
    try:
        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        client.ping()
        return "ok"
    except Exception:  # noqa: BLE001
        return "down"


def _check_minio() -> str:
    settings = get_settings()
    try:
        response = httpx.get(f"http://{settings.minio_endpoint}/minio/health/live", timeout=3.0)
        return "ok" if response.status_code == 200 else "down"
    except httpx.HTTPError:
        return "down"


def _check_llm_backend() -> str:
    try:
        return "ok" if get_llm_client().ping() else "down"
    except Exception:  # noqa: BLE001 — 잘못된 LLM_BACKEND 값 등도 down으로 보고한다
        return "down"


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "database": _check_database(),
        "redis": _check_redis(),
        "minio": _check_minio(),
        "llm_backend": _check_llm_backend(),
    }
