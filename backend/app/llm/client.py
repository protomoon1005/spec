"""LLMClient 추상화 (docs/infra-spec.md 2·8단계).

api/worker는 LLM_BACKEND 값만 보고 구현체를 고른다 — 자연어 -> Spec 컴파일
본체(M1, 이번 범위 밖)를 포함해 어느 쪽이든 이 Protocol 하나만 본다. vLLM은
guided_json(outlines 백엔드)으로, Ollama는 format 필드에 JSON Schema를 그대로
줘서 구조화 출력을 강제한다 — 제약 디코딩 강도가 서로 다르다는 점은 각 구현체
모듈 docstring에 적어 뒀다.
"""
from __future__ import annotations

from typing import Protocol

from app.core.config import get_settings


class LLMClient(Protocol):
    def generate_json(self, prompt: str, json_schema: dict, *, system: str | None = None) -> dict:
        """prompt에 대해 json_schema를 만족하는 JSON 객체 하나를 반환한다."""
        ...

    def ping(self) -> bool:
        """실제 생성 없이 백엔드 연결 여부만 확인한다 (헬스체크용, 가볍다)."""
        ...


def get_llm_client() -> LLMClient:
    settings = get_settings()
    if settings.llm_backend == "ollama":
        from app.llm.ollama_client import OllamaClient

        return OllamaClient()
    if settings.llm_backend == "vllm":
        from app.llm.vllm_client import VLLMClient

        return VLLMClient()
    raise ValueError(f"알 수 없는 LLM_BACKEND: {settings.llm_backend!r} (vllm|ollama만 허용)")
