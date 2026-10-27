// lib/views-briefing.ts 확인. 서버 없이 돈다.
//   node --experimental-strip-types scripts/check-views.ts

import assert from "node:assert/strict";

import { briefingFromRun, probTint, type RunForBriefing } from "../lib/views-briefing.ts";

const eq = 1 / 3;
const base: RunForBriefing = {
  run_id: 7,
  status: "done",
  reason: null,
  data_snapshot_asof: "2025-12-30",
  scorer_sources: { market: "real", sentiment: "neutral", regime: "real" },
  universe: [
    { ticker: "069500", name: "KODEX 200" },
    { ticker: "148070", name: "KOSEF 국고채10년" },
  ],
  decisions: [
    {
      date: "2023-01-31",
      views: {
        weights: { market: eq, sentiment: eq, regime: eq },
        probs: { market: { "069500": 0.6, "148070": 0.4 }, sentiment: { "069500": 0.5, "148070": 0.5 } },
      },
    },
    {
      date: "2023-02-28",
      views: {
        weights: { market: eq, sentiment: eq, regime: eq },
        probs: { market: { "069500": 0.55 } },
      },
    },
    {
      date: "2023-03-31",
      views: {
        weights: { market: 0.5, sentiment: 0.1, regime: 0.4 },
        probs: {
          market: { "148070": 0.42, "069500": 0.71, "999999": 0.5 },
          sentiment: { "069500": 0.5, "148070": 0.5 },
          regime: { "069500": 0.3, "148070": 0.62 },
        },
      },
    },
  ],
};

// 정상 실행: 가중치 추이 · 최신값 · 처음 움직인 날 · 변화 횟수
const b = briefingFromRun(base);
assert.equal(b.kind, "ok");
if (b.kind !== "ok") throw new Error("unreachable");
assert.equal(b.history.length, 3);
assert.deepEqual(b.latest, { date: "2023-03-31", market: 0.5, sentiment: 0.1, regime: 0.4 });
assert.equal(b.firstMove, "2023-03-31");
assert.equal(b.changes, 1);
assert.ok(Math.abs(b.drift.market - (0.5 - eq)) < 1e-12);
assert.equal(b.sources.sentiment, "neutral");

// 확률 표: 종목 목록 순서, 목록 밖 종목은 뒤에, 빠진 관점은 null
assert.deepEqual(b.rows.map((r) => r.ticker), ["069500", "148070", "999999"]);
assert.equal(b.rows[0].name, "KODEX 200");
assert.deepEqual(b.rows[0].probs, { market: 0.71, sentiment: 0.5, regime: 0.3 });
assert.equal(b.rows[2].name, "");
assert.deepEqual(b.rows[2].probs, { market: 0.5, sentiment: null, regime: null });

// 관점 기록이 섞여 있으면 기록이 있는 리밸런싱만 쓴다
const mixed = briefingFromRun({ ...base, decisions: [{ date: "2022-12-30" }, ...(base.decisions ?? [])] });
assert.equal(mixed.kind === "ok" && mixed.history[0].date, "2023-01-31");

// 끝까지 균등이면 firstMove 는 null
const flat = briefingFromRun({ ...base, decisions: base.decisions!.slice(0, 2) });
assert.ok(flat.kind === "ok" && flat.firstMove === null && flat.changes === 0);

// 옛 실행(관점 기록 없음) · 진행 중 · 실패
assert.equal(briefingFromRun({ ...base, decisions: [{ date: "2023-01-31" }] }).kind, "no-views");
assert.equal(briefingFromRun({ ...base, decisions: null }).kind, "no-views");
assert.equal(briefingFromRun({ ...base, status: "running", decisions: null }).kind, "pending");
const failed = briefingFromRun({ ...base, status: "failed", reason: "가격 없음" });
assert.ok(failed.kind === "failed" && failed.message.includes("가격 없음"));

// 0.5 중심 색: 중립은 무색, 멀어질수록 짙고 0.25 이상이면 상한
assert.equal(probTint(0.5, "#00ff00", "#ff0000"), undefined);
assert.equal(probTint(null, "#00ff00", "#ff0000"), undefined);
assert.equal(probTint(0.75, "#00ff00", "#ff0000"), "#00ff0040");
assert.equal(probTint(0.1, "#00ff00", "#ff0000"), "#ff000040");
assert.equal(probTint(0.6, "#00ff00", "#ff0000"), "#00ff001a");

console.log("ok");
