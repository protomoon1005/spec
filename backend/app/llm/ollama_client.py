"""LLMClient의 Ollama 구현체.

/api/chat의 `format` 필드에 JSON Schema를 그대로 넘겨 구조화 출력을 받는다.
Ollama의 제약 디코딩은 강도가 약해 스키마를 어긴 출력이 실제로 나온다. 그래서
M1 후처리(app/m1/postprocess.py)가 다시 확인한다.
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
        self._options = {"temperature": settings.llm_temperature, "seed": settings.llm_seed}

    def build_payload(self, prompt: str, json_schema: dict, *, system: str | None = None) -> dict:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "format": json_schema,
            # qwen3 는 기본으로 생각(thinking)부터 길게 써서 호출 한 번이 1분 가까이 걸린다.
            # 결과는 format 으로 JSON 이 강제되므로 생각은 끈다.
            "think": False,
            # 재현성 — 샘플링 온도 0, 시드 고정(app/core/config.py llm_temperature · llm_seed).
            "options": dict(self._options),
        }

    def generate_json(self, prompt: str, json_schema: dict, *, system: str | None = None) -> dict:
        response = httpx.post(
            f"{self._base_url}/api/chat",
            json=self.build_payload(prompt, json_schema, system=system),
            timeout=self._timeout,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        return json.loads(content)

    def generate_text(self, prompt: str, *, system: str | None = None, options: dict | None = None) -> str:
        """구조화 출력 없이 글로 답을 받는다. 비교 대상(LLM 이 코드를 직접 짜는 방식)이 쓴다.

        모델 · 온도 · 시드 · thinking 끔은 generate_json 과 같다. options 로 num_ctx 같은 값을 더한다.
        """
        payload = self.build_payload(prompt, {}, system=system)
        payload.pop("format")
        payload["options"] = {**payload["options"], **(options or {})}
        response = httpx.post(f"{self._base_url}/api/chat", json=payload, timeout=self._timeout * 3)
        response.raise_for_status()
        return response.json()["message"]["content"]

    def ping(self) -> bool:
        try:
            response = httpx.get(f"{self._base_url}/api/tags", timeout=3.0)
            return response.status_code == 200
        except httpx.HTTPError:
            return False
