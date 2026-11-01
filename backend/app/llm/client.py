"""LLMClient 인터페이스와 구현체 선택.

구현체는 Ollama 하나다(docs/pivot-plan.md 결정 5). 테스트는 이 Protocol 모양의 가짜 클라이언트를
끼운다. Ollama 는 format 필드에 JSON Schema 를 그대로 줘서 구조화 출력을 강제한다.
"""
from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    def generate_json(self, prompt: str, json_schema: dict, *, system: str | None = None) -> dict:
        """prompt에 대해 json_schema를 만족하는 JSON 객체 하나를 반환한다."""
        ...

    def ping(self) -> bool:
        """실제 생성 없이 백엔드 연결 여부만 확인한다 (헬스체크용, 가볍다)."""
        ...


def get_llm_client() -> LLMClient:
    from app.llm.ollama_client import OllamaClient

    return OllamaClient()
