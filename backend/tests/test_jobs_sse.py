"""202 Accepted + job_id -> GET /jobs/{job_id}/stream SSE 패턴 테스트
(docs/infra-spec.md 7단계). **패턴 자체가 동작하는지만** 본다 — 컴파일 결과는
tests/test_pipeline.py 와 tests/test_compile.py 가 본다.

CELERY_TASK_ALWAYS_EAGER=true(conftest 기본값)이라 .delay()가 그 자리에서
동기 실행된다. 여기 쓰는 사용자는 성향을 확정한 적이 없어서, 컴파일이
LLM 을 부르기 전에 "성향이 없다" 로 끝난다. 테스트가 모델을 기다리지 않는
것은 그 덕분이다 — 6단계에서 더미 태스크가 실물로 바뀌면서 생긴 성질이라
일부러 적어 둔다.
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


def test_compile_spec_returns_202_with_job_id(client, make_user, test_password):
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


def test_job_stream_reports_completion(client, make_user, test_password):
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
    # 성향이 없는 사용자라 컴파일은 실패로 끝난다. 여기서 보려는 것은 태스크가
    # 예외로 죽지 않고 **사용자에게 보여 줄 수 있는 결과**를 스트림에 흘린다는 것이다.
    assert events[-1]["result"]["status"] == "failed"
    assert "성향" in events[-1]["result"]["reason"]


def test_job_stream_requires_auth(client):
    resp = client.get("/jobs/some-job-id/stream")

    assert resp.status_code == 401
