"""LLMClient 통합 테스트 (docs/infra-spec.md 8단계).

호스트에 뜬 Ollama(qwen3:8b)로 실제 구조화 JSON 응답을 받아 확인한다. GPU가
없어 vLLM 쪽은 이번 단계에서 검증하지 못한다 — VLLMClient는 코드만 존재하고
이 파일에서 실행하지 않는다.

`requires_ollama` 마커가 붙어 있다: CI(ubuntu-latest, GPU도 Ollama도 없다)는
`pytest -m "not requires_ollama"`로 이 파일을 건너뛴다. 로컬에서 호스트
Ollama가 떠 있으면 그대로 돈다.
"""
from __future__ import annotations

import pytest

from app.llm.client import get_llm_client
from app.llm.ollama_client import OllamaClient

pytestmark = pytest.mark.requires_ollama


def test_get_llm_client_default_backend_is_ollama():
    """LLM_BACKEND를 아무도 안 정하면(.env 미로딩 테스트 환경) 기본값이 ollama다."""
    client = get_llm_client()
    assert isinstance(client, OllamaClient)


def test_ollama_client_returns_schema_conformant_json():
    client = OllamaClient()
    schema = {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "name": {"type": "string"},
        },
        "required": ["ticker", "name"],
    }

    result = client.generate_json(
        "국내 ETF 상품 하나를 골라 종목코드(ticker)와 이름(name)을 답해라.",
        schema,
    )

    assert isinstance(result, dict)
    assert isinstance(result["ticker"], str) and result["ticker"]
    assert isinstance(result["name"], str) and result["name"]
