"""GET /jobs/{job_id}/stream — 202 Accepted + job_id -> SSE 구독 패턴 (docs/
infra-spec.md 7단계). 라우터 6종 중 유일하게 "이 패턴만 실제로 동작"해야 하는
부분이라 501로 두지 않는다. Celery AsyncResult를 폴링해 상태 변화와 완료를
text/event-stream으로 흘린다.
"""
from __future__ import annotations

import asyncio
import json

from celery.result import AsyncResult
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.security import AuthUser, require_any_role
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/jobs", tags=["jobs"])

_POLL_INTERVAL_SECONDS = 0.3
_MAX_POLLS = 200  # 데모 태스크(3초)보다 넉넉한 상한. 넘기면 timeout 이벤트로 스트림을 닫는다.


async def _event_stream(job_id: str):
    result = AsyncResult(job_id, app=celery_app)
    last_state: str | None = None
    for _ in range(_MAX_POLLS):
        state = result.state
        if state != last_state:
            yield f"event: status\ndata: {json.dumps({'job_id': job_id, 'state': state})}\n\n"
            last_state = state
        if state in ("SUCCESS", "FAILURE"):
            payload: dict[str, object] = {"job_id": job_id, "state": state}
            if state == "SUCCESS":
                payload["result"] = result.result
            else:
                payload["error"] = str(result.result)
            yield f"event: complete\ndata: {json.dumps(payload, default=str)}\n\n"
            return
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    yield f"event: timeout\ndata: {json.dumps({'job_id': job_id})}\n\n"


@router.get("/{job_id}/stream")
def stream_job(job_id: str, user: AuthUser = Depends(require_any_role)) -> StreamingResponse:
    return StreamingResponse(_event_stream(job_id), media_type="text/event-stream")
