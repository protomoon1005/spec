// lib/views-briefing.ts 확인. 서버 없이 돈다.
//   node --experimental-strip-types scripts/check-views.ts

import assert from "node:assert/strict";

import { briefingFromRun, probTint, sentimentEvidenceOf, type RunForBriefing } from "../lib/views-briefing.ts";

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

// 뉴스 감성 근거: 기록이 없으면 빈 배열, 있으면 아는 칸만 읽는다
assert.deepEqual(b.sentimentEvidence, []);
assert.deepEqual(sentimentEvidenceOf(undefined), []);
assert.deepEqual(sentimentEvidenceOf({ sentiment: "x" }), []);
assert.deepEqual(sentimentEvidenceOf({ sentiment: { foo: 1 } }), []);
assert.deepEqual(
  sentimentEvidenceOf({
    sentiment: {
      lens_id: "L1",
      sector: "EQUITY",
      n_articles: 34,
      article_ids: ["A1", "A2", 3, null],
      model_version: "kr-finbert-sc",
      z: -0.42,
      extra: 1,
    },
  }),
  [{ sector: "EQUITY", lens_id: "L1", n_articles: 34, article_ids: ["A1", "A2", "3"], model_version: "kr-finbert-sc", z: -0.42, state: null, as_of: null }],
);
// 종목별 근거면 자산군마다 하나 (코드 순 첫 종목)
const perTicker = sentimentEvidenceOf({
  sentiment: {
    "148070": { sector: "BOND", n_articles: 5 },
    "069500": { sector: "EQUITY", n_articles: 30 },
    "229200": { sector: "EQUITY", n_articles: 99 },
  },
});
assert.deepEqual(
  perTicker.map((e) => [e.sector, e.n_articles]),
  [
    ["BOND", 5],
    ["EQUITY", 30],
  ],
);
// 숫자 칸이 숫자가 아니면 버린다
assert.deepEqual(sentimentEvidenceOf({ sentiment: { z: "높음" } }), []);
const withEvidence = briefingFromRun({
  ...base,
  decisions: [
    ...(base.decisions ?? []),
    {
      date: "2023-04-28",
      views: {
        weights: { market: 0.5, sentiment: 0.1, regime: 0.4 },
        probs: {},
        evidence: { sentiment: { sector: "EQUITY", z: 0.8, state: "positive" } },
      },
    },
  ],
});
assert.ok(withEvidence.kind === "ok" && withEvidence.sentimentEvidence[0]?.state === "positive");

// 0.5 중심 색: 중립은 무색, 멀어질수록 짙고 0.25 이상이면 상한
assert.equal(probTint(0.5, "#00ff00", "#ff0000"), undefined);
assert.equal(probTint(null, "#00ff00", "#ff0000"), undefined);
assert.equal(probTint(0.75, "#00ff00", "#ff0000"), "#00ff0040");
assert.equal(probTint(0.1, "#00ff00", "#ff0000"), "#ff000040");
assert.equal(probTint(0.6, "#00ff00", "#ff0000"), "#00ff001a");

// 백엔드 bridge.last_views() 모양: views.sentiment = {묶음: {sector, z, state, n_articles, article_ids, data_asof, tickers}}.
const fromBridge = briefingFromRun({
  ...base,
  decisions: [
    {
      date: "2023-01-31",
      views: {
        weights: { market: eq, sentiment: eq, regime: eq },
        probs: {},
        sentiment: {
          EQUITY: { sector: "EQUITY", z: 1.2, state: "high", n_articles: 412, article_ids: [3, 1], data_asof: "2023-01-31", tickers: ["069500"] },
        },
      },
    },
  ],
} as RunForBriefing);
assert.equal(fromBridge.kind, "ok");
if (fromBridge.kind === "ok") {
  assert.equal(fromBridge.sentimentEvidence.length, 1);
  assert.equal(fromBridge.sentimentEvidence[0].as_of, "2023-01-31");
  assert.deepEqual(fromBridge.sentimentEvidence[0].article_ids, ["3", "1"]);
}

console.log("ok");
