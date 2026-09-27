// lib/api.ts 의 SSE 끊어 읽기 확인. 서버 없이 돈다.
//   node --experimental-strip-types scripts/check-api.ts

import assert from "node:assert/strict";

import { parseSse } from "../lib/api.ts";

// 서버가 실제로 보내는 모양 (backend/app/routers/jobs.py)
const status = 'event: status\ndata: {"job_id": "j1", "state": "PENDING"}\n\n';
const complete = 'event: complete\ndata: {"job_id": "j1", "state": "SUCCESS", "result": {"status": "completed"}}\n\n';

// 한 번에 다 온 경우
let r = parseSse(status + complete);
assert.deepEqual(r.events.map((e) => e.event), ["status", "complete"]);
assert.deepEqual(r.events[1].data, { job_id: "j1", state: "SUCCESS", result: { status: "completed" } });
assert.equal(r.rest, "");

// 이벤트 중간에서 끊겨 온 경우 — 끝 조각은 남겼다가 다음에 붙인다
const cut = 20;
r = parseSse(complete.slice(0, cut));
assert.equal(r.events.length, 0);
r = parseSse(r.rest + complete.slice(cut));
assert.deepEqual(r.events.map((e) => e.event), ["complete"]);

// CRLF 줄바꿈도 같게 읽는다
r = parseSse(status.replace(/\n/g, "\r\n"));
assert.deepEqual(r.events[0].data, { job_id: "j1", state: "PENDING" });

console.log("ok");
