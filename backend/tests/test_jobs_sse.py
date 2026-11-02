"""202 Accepted + job_id -> GET /jobs/{job_id}/stream SSE 패턴 테스트.
**패턴 자체가 동작하는지만** 본다. 명세화 결과는 tests/test_compile_v0_2.py 와
tests/test_strategy_v0_2_api.py 가 본다.

CELERY_TASK_ALWAYS_EAGER=true(conftest 기본값)이라 .delay()가 그 자리에서
동기 실행된다. AI 를 부르기 전에 거절되는 문장을 써서 모델을 기다리지 않고 전략서도 저장하지 않는다.
LLM 은 혹시 불리면 실패하는 가짜로 바꿔 끼운다.
"""
from __future__ import annotations

import json

import pytest


class _NoCallLLM:
    def generate_json(self, prompt: str, schema: dict, *, system: str | None = None) -> dict:
        raise AssertionError("이 테스트는 AI 를 부르지 않아야 한다")


@pytest.fixture(autouse=True)
def _fake_llm(monkeypatch):
    monkeypatch.setattr("app.llm.client.get_llm_client", lambda: _NoCallLLM())


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
        "/v2/strategies/compile",
        json={"text": "비트코인 20일 이평 돌파하면 사고 깨지면 팔아"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 202, resp.text
    assert resp.json()["job_id"]


def test_compile_spec_requires_auth(client):
    resp = client.post("/v2/strategies/compile", json={"text": "x"})

    assert resp.status_code == 401


def test_job_stream_reports_completion(client, make_user):
    email, _ = make_user()
    token = _login(client, email)
    headers = {"Authorization": f"Bearer {token}"}

    compile_resp = client.post(
        "/v2/strategies/compile",
        json={"text": "비트코인 20일 이평 돌파하면 사고 깨지면 팔아"},
        headers=headers,
    )
    job_id = compile_resp.json()["job_id"]

    stream_resp = client.get(f"/jobs/{job_id}/stream", headers=headers)

    assert stream_resp.status_code == 200
    assert stream_resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse_events(stream_resp.text)
    assert events[-1]["state"] == "SUCCESS"
    # AI 를 부르기 전에 거절되는 문장이다. 여기서 보려는 것은 태스크가 예외로 죽지 않고
    # 사용자에게 보여 줄 수 있는 결과를 스트림에 흘린다는 것이다.
    assert events[-1]["result"]["status"] == "reject"
    assert events[-1]["result"]["code"] == "UNSUPPORTED_ASSET"


def test_job_stream_requires_auth(client):
    resp = client.get("/jobs/some-job-id/stream")

    assert resp.status_code == 401
