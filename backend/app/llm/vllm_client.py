"""LLMClient의 vLLM 구현체 — GPU 있는 팀원 전용 (docs/infra-spec.md 2·8단계).

OpenAI 호환 /v1/chat/completions 에 vLLM 확장 필드 `guided_json`(outlines
백엔드, docker-compose.yml의 vllm 서비스 커맨드 `--guided-decoding-backend
outlines`)을 실어 구조화 출력을 강제한다. 이번 단계는 GPU가 없어 이 클라이언트를
직접 기동해 검증하지 못했다 — 서비스 정의(profile: gpu)와 이 클라이언트
코드만 준비해 두고, 실측은 GPU 팀원 몫으로 남긴다.
"""
from __future__ import annotations

import json

import httpx

from app.core.config import get_settings


class VLLMClient:
    def __init__(self, *, base_url: str | None = None, model: str | None = None):
        settings = get_settings()
        self._base_url = (base_url or settings.vllm_base_url).rstrip("/")
        self._model = model or settings.vllm_model
        self._timeout = settings.llm_request_timeout_seconds

    def generate_json(self, prompt: str, json_schema: dict, *, system: str | None = None) -> dict:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = httpx.post(
            f"{self._base_url}/v1/chat/completions",
            json={
                "model": self._model,
                "messages": messages,
                "guided_json": json_schema,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)

    def ping(self) -> bool:
        try:
            response = httpx.get(f"{self._base_url}/v1/models", timeout=3.0)
            return response.status_code == 200
        except httpx.HTTPError:
            return False
