"""202 Accepted + job_id -> GET /jobs/{job_id}/stream SSE 패턴 테스트.
**패턴 자체가 동작하는지만** 본다 — 컴파일 결과는 tests/test_pipeline.py 와
tests/test_compile.py 가 본다.

CELERY_TASK_ALWAYS_EAGER=true(conftest 기본값)이라 .delay()가 그 자리에서
동기 실행된다. LLM 은 아무 의도도 뽑지 않는 가짜로 바꿔 끼운다 — 그러면 컴파일이
추가 질문에서 멈추므로 모델을 기다리지 않고 전략서도 저장하지 않는다.
"""
from __future__ import annotations

import json

import pytest


class _NoIntentLLM:
    def generate_json(self, prompt: str, schema: dict, *, system: str | None = None) -> dict:
        return {"keywords": [], "sectors": [], "asset_groups": [], "mentioned_names": []}


@pytest.fixture(autouse=True)
def _fake_llm(monkeypatch):
    monkeypatch.setattr("app.llm.client.get_llm_client", lambda: _NoIntentLLM())


def _login(client, email: str) -> str:
    resp = client.post("/auth/login", json={"username": email})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _parse_sse_events(body: str) -> list[dict]:
    events: list[dict] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        lines = block.splitlines()
        data_line = next(line for line in lines if line.startswith("data: "))
        events.append(json.loads(data_line[len("data: ") :]))
    return events


def test_compile_spec_returns_202_with_job_id(client, make_user):
    email, _ = make_user()
    token = _login(client, email)

    resp = client.post(
        "/specs/compile",
        json={"input_prompt": "안정적으로 배당 위주로 굴려줘"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 202, resp.text
    assert resp.json()["job_id"]


def test_compile_spec_requires_auth(client):
    resp = client.post("/specs/compile", json={"input_prompt": "x"})

    assert resp.status_code == 401


def test_job_stream_reports_completion(client, make_user):
    email, _ = make_user()
    token = _login(client, email)
    headers = {"Authorization": f"Bearer {token}"}

    compile_resp = client.post(
        "/specs/compile", json={"input_prompt": "성장주 위주로"}, headers=headers
    )
    job_id = compile_resp.json()["job_id"]

    stream_resp = client.get(f"/jobs/{job_id}/stream", headers=headers)

    assert stream_resp.status_code == 200
    assert stream_resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse_events(stream_resp.text)
    assert events[-1]["state"] == "SUCCESS"
    # 의도가 비어 추가 질문으로 끝난다. 여기서 보려는 것은 태스크가 예외로 죽지 않고
    # 사용자에게 보여 줄 수 있는 결과를 스트림에 흘린다는 것이다.
    assert events[-1]["result"]["status"] == "need_answer"
    assert events[-1]["result"]["question"]


def test_job_stream_requires_auth(client):
    resp = client.get("/jobs/some-job-id/stream")

    assert resp.status_code == 401
