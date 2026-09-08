"""202 Accepted + job_id -> GET /jobs/{job_id}/stream SSE 패턴 테스트
(docs/infra-spec.md 7단계). compile_spec 더미 태스크로 패턴 자체가 실제로
동작하는지만 확인한다 — 자연어 -> Spec 컴파일 로직은 검증 대상이 아니다.

CELERY_TASK_ALWAYS_EAGER=true(conftest 기본값)이라 .delay()가 그 자리에서
동기 실행된다. 데모 3초 대기까지 그대로 기다리면 테스트가 느려지므로
DEMO_SLEEP_SECONDS를 0으로 monkeypatch 한다.
"""
from __future__ import annotations

import json


def _login(client, email: str, password: str) -> str:
    resp = client.post("/auth/login", json={"email": email, "password": password})
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


def test_compile_spec_returns_202_with_job_id(client, make_user, test_password, monkeypatch):
    monkeypatch.setattr("app.workers.tasks.DEMO_SLEEP_SECONDS", 0.0)
    email, _ = make_user("retail")
    token = _login(client, email, test_password)

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


def test_job_stream_reports_completion(client, make_user, test_password, monkeypatch):
    monkeypatch.setattr("app.workers.tasks.DEMO_SLEEP_SECONDS", 0.0)
    email, _ = make_user("retail")
    token = _login(client, email, test_password)
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
    assert events[-1]["result"]["status"] == "completed"


def test_job_stream_requires_auth(client):
    resp = client.get("/jobs/some-job-id/stream")

    assert resp.status_code == 401
