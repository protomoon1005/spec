"""애플리케이션 설정. 전부 환경변수에서만 읽는다 (.env.example이 정본).

DATABASE_URL은 이 모듈이 아니라 app/core/db.py가 직접 읽는다 (기존 규약을
그대로 둔다) — 여기서는 FastAPI/Celery 스켈레톤(7단계)에 새로 필요해진
설정만 모은다.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # JWT
    jwt_secret: str = "dev-insecure-secret-change-me-before-deploy"
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_minutes: int = 30
    jwt_refresh_ttl_minutes: int = 60 * 24 * 7

    # Celery / Redis
    redis_url: str = "redis://localhost:6379/0"
    celery_task_always_eager: bool = False

    # LLM 백엔드 분기 (docs/infra-spec.md 2·8단계). api/worker는 llm_backend 값만
    # 보고 구현체를 고른다 — 어느 쪽이든 app.llm.client.LLMClient 인터페이스만 본다.
    llm_backend: str = "ollama"  # "vllm" | "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    vllm_base_url: str = "http://localhost:8001"
    vllm_model: str = "LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct"
    llm_request_timeout_seconds: float = 120.0

    # 브라우저에서 프론트(NEXT_PUBLIC_API_BASE_URL, 기본 3000)가 이 API를 직접
    # 호출하므로 CORS를 열어야 한다 (docs/infra-spec.md 9단계, /health 페이지).
    cors_allow_origins: list[str] = ["http://localhost:3000"]

    minio_endpoint: str = "localhost:9000"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
