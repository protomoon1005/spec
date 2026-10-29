// lib/demo-samples.ts · lib/repro.ts 확인. 서버 없이 돈다.
//   node --experimental-strip-types scripts/check-demo.ts

import assert from "node:assert/strict";
import { test } from "node:test";

import { latencyVerdict, loadDemoSamples, summarize, universeSummary, type DemoCase } from "../lib/demo-samples.ts";
import { compareRuns, deltaPair, ordersOf, splitSampleOrder, type RunForRepro } from "../lib/repro.ts";

const mk = (result: DemoCase["result"]): DemoCase => ({
  no: "1",
  profile_level: 3,
  profile_label: "위험중립형",
  text: "",
  axis: null,
  expected: null,
  compile: null,
  validation: null,
  backtest: null,
  result,
  note: null,
});

test("성공 기준: 통과 8 + 의도된 차단 2 + 실패 0", () => {
  const ok = summarize([...Array(8)].map(() => mk("pass")).concat([mk("blocked_expected"), mk("blocked_expected")]));
  assert.equal(ok.met, true);
  assert.equal(ok.n, 10);
  const failOne = summarize([...Array(8)].map(() => mk("pass")).concat([mk("blocked_expected"), mk("fail")]));
  assert.equal(failOne.met, false);
  const unknown = summarize([mk("pass"), mk(null)]);
  assert.equal(unknown.met, null);
  assert.equal(unknown.unknown, 1);
});

test("응답시간 판정", () => {
  assert.deepEqual(latencyVerdict({ compile_seconds_median: 15, processing_seconds_median: 10, n: 10, baseline_seconds: 30, baseline_date: "2026-08-22" }), {
    median: 15,
    baseline: 30,
    ratio: 0.5,
    faster: true,
  });
  assert.equal(latencyVerdict(null), null);
  assert.equal(latencyVerdict({ compile_seconds_median: null, processing_seconds_median: null, n: 0, baseline_seconds: 30, baseline_date: null }), null);
});

test("유니버스 요약", () => {
  assert.equal(universeSummary(null), "–");
  const c = {
    status: "completed" as const,
    rounds: [],
    seconds_total: 1,
    seconds_processing: 1,
    spec_id: "S",
    n_universe: 3,
    universe: [
      { ticker: "1", name: "A", weight_min: 0, weight_max: 0.3 },
      { ticker: "2", name: null, weight_min: 0, weight_max: 0.3 },
      { ticker: "3", name: "C", weight_min: 0, weight_max: 0.3 },
    ],
    rebalance: null,
    failure_reason: null,
  };
  assert.equal(universeSummary(c), "3종목 — A, 2 외 1");
});

test("파일이 없으면 missing", async () => {
  const notFound = (async () => new Response("no", { status: 404 })) as typeof fetch;
  assert.deepEqual(await loadDemoSamples(notFound), { kind: "missing" });
  const ok = (async () => new Response(JSON.stringify({ cases: [] }), { status: 200 })) as typeof fetch;
  assert.equal((await loadDemoSamples(ok)).kind, "ok");
  const bad = (async () => new Response("{", { status: 200 })) as typeof fetch;
  assert.equal((await loadDemoSamples(bad)).kind, "error");
});

const run = (id: number, targets: Record<string, number>[], curve: number[]): RunForRepro => ({
  run_id: id,
  spec_id: "S",
  status: "done",
  data_snapshot_asof: "2026-09-30",
  decisions: targets.map((t, i) => ({ date: `2026-0${i + 1}-01`, target: t, cash: 1 - Object.values(t).reduce((a, b) => a + b, 0) })),
  series: curve.map((v, i) => ({ date: `d${i}`, strategy: v })),
});

test("주문은 직전 목표비중 대비 변화", () => {
  const o = ordersOf([{ target: { a: 0.5 } }, { target: { a: 0.3, b: 0.2 } }]);
  assert.deepEqual(o[0], { a: 0.5 });
  assert.ok(Math.abs(o[1].a + 0.2) < 1e-12 && Math.abs(o[1].b - 0.2) < 1e-12);
});

test("같은 두 실행은 전부 같음", () => {
  const t = [{ a: 0.5, b: 0.4 }, { a: 0.45, b: 0.45 }];
  const c = compareRuns(run(1, t, [1, 2, 3]), run(2, t, [1, 2, 3]));
  assert.equal(c.sameWeights, true);
  assert.equal(c.sameOrders, true);
  assert.equal(c.curve.same, true);
  assert.equal(c.diffRows, 0);
});

test("한 결정이 다르면 그 행과 뒤 주문이 다름", () => {
  const c = compareRuns(
    run(1, [{ a: 0.5 }, { a: 0.4 }, { a: 0.4 }], [1, 2, 3]),
    run(2, [{ a: 0.5 }, { a: 0.41 }, { a: 0.4 }], [1, 2, 3.1]),
  );
  assert.equal(c.sameWeights, false);
  assert.deepEqual(
    c.rows.map((r) => r.same),
    [true, false, false],
  );
  assert.ok(c.rows[1].diffKeys.includes("a") && c.rows[1].diffKeys.includes("현금"));
  assert.equal(c.curve.diffPoints, 1);
});

test("결정 수가 다르면 다름", () => {
  const c = compareRuns(run(1, [{ a: 0.5 }], [1]), run(2, [{ a: 0.5 }, { a: 0.5 }], [1]));
  assert.equal(c.sameWeights, false);
  assert.deepEqual(c.rows[1].diffKeys, ["행 없음"]);
});

test("주문 표본 짝 나누기", () => {
  assert.deepEqual(splitSampleOrder({ date: "d", a: 1, b: 2 }, null), { common: { date: "d" }, a: 1, b: 2 });
  assert.deepEqual(splitSampleOrder({ date: "d", "11": 1, "12": 1 }, [11, 12]), { common: { date: "d" }, a: 1, b: 1 });
  assert.equal(splitSampleOrder({ date: "d", ticker: "x", qty: 1 }, [11, 12]), null);
  assert.equal(splitSampleOrder("x", null), null);
});

test("주문 표본 확정 모양 {date, a: {티커: Δ}, b: {티커: Δ}}", () => {
  const x = splitSampleOrder({ date: "2023-02-01", a: { "069500": 0.01, "229200": -0.02 }, b: { "069500": 0.01, "229200": -0.03 } }, [38, 39]);
  assert.ok(x);
  assert.deepEqual(x.common, { date: "2023-02-01" });
  const d = deltaPair(x.a, x.b);
  assert.ok(d);
  assert.deepEqual(d.diffKeys, ["229200"]);
  assert.equal(deltaPair({ side: "buy" }, { side: "buy" }), null);
  assert.equal(deltaPair({}, {}), null);
});
