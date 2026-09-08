"""LLMClient의 Ollama 구현체 — GPU 없는 팀원 기본값 (docs/infra-spec.md 2·8단계).

/api/chat의 `format` 필드에 JSON Schema를 그대로 넘겨 구조화 출력을 받는다.
Ollama의 제약 디코딩은 vLLM(guided_json, outlines 백엔드)보다 강도가 약하다
— 정본이 명시한 대로, M1의 10월 정식 실측은 반드시 vLLM 쪽에서 한다. 이
클라이언트는 GPU 없는 팀원의 로컬 개발용이다.
"""
from __future__ import annotations

import json

import httpx

from app.core.config import get_settings


class OllamaClient:
    def __init__(self, *, base_url: str | None = None, model: str | None = None):
        settings = get_settings()
        self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._model = model or settings.ollama_model
        self._timeout = settings.llm_request_timeout_seconds

    def generate_json(self, prompt: str, json_schema: dict, *, system: str | None = None) -> dict:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = httpx.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": messages,
                "stream": False,
                "format": json_schema,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        return json.loads(content)

    def ping(self) -> bool:
        try:
            response = httpx.get(f"{self._base_url}/api/tags", timeout=3.0)
            return response.status_code == 200
        except httpx.HTTPError:
            return False
