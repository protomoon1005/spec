// lib/report-narrative.ts 확인. 서버 없이 돈다.
//   node --experimental-strip-types scripts/check-narrative.ts
// (frontend 컨테이너는 node 20 이라 CLAUDE.md 의 node:22-slim docker 명령으로 돌린다.)

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  NARRATIVE_FIELDS,
  narrate,
  narrativeValues,
  SENTENCES,
  type NarrativeSource,
  type NarrativeValues,
} from "../lib/report-narrative.ts";

const report: NarrativeSource["report"] = {
  PERIOD_START: "2019-01-02",
  PERIOD_END: "2026-09-30",
  AS_OF: "2026-09-30",
  strategy: { cagr: 0.0812, mdd: -0.1234, sharpe: 0.8567 },
  rebalanceCount: 93,
  cashTarget: 0.12,
  feasibility: { adjusted: { ranges: 2, cashFrom: 0.05 } },
};

const run: NonNullable<NarrativeSource["run"]> = {
  decisions: [
    { date: "2026-08-03", views: { weights: { market: 0.4, sentiment: 0.2, regime: 0.4 } } },
    { date: "2026-09-01", views: { weights: { market: 0.5, sentiment: 0.1, regime: 0.4 } } },
    // 관점 기록이 빠진 결정은 건너뛴다
    { date: "2026-09-30", views: null },
  ],
  validation: { status: "passed", source: "stored" },
};

test("모든 값이 있으면 문장 틀 전부가 나온다", () => {
  const lines = narrate(narrativeValues({ report, run }));
  assert.deepEqual(
    lines.map((l) => l.id),
    SENTENCES.map((s) => s.id),
  );
  const text = lines.map((l) => l.text).join(" ");
  assert.match(text, /2019-01-02부터 2026-09-30까지/);
  assert.match(text, /리밸런싱을 93회/);
  assert.match(text, /\+8\.1%입니다\(2026-09-30 종가 기준\)/);
  assert.match(text, /최대낙폭\(MDD\)은 −12\.3%, 샤프지수는 0\.86/);
  assert.match(text, /마지막 리밸런싱\(2026-09-01\)에 쓴 관점 가중치는 시장분석 50\.0%, 뉴스 감성 10\.0%, 시장온도 40\.0%/);
  assert.match(text, /2종목의 비중 범위를 보정했고, 현금 목표를 현금 하한 5%에서 12%로 올렸습니다/);
  assert.match(text, /승인 시점에 저장한 검증 확정값/);
});

test("값이 없으면 그 문장만 빠진다", () => {
  const lines = narrate(narrativeValues({ report: { ...report, feasibility: { adjusted: null } }, run: null }));
  const ids = lines.map((l) => l.id);
  assert.deepEqual(ids, ["period", "cagr", "risk"]);
  // 정적 데모(실행 기록 없음)에서 관점 가중치 문장이 나오면 안 된다 — 리포트 그래프는 모의값이다
  assert.ok(!lines.some((l) => l.text.includes("관점 가중치")));
});

test("숫자가 NaN 이어도 문장을 빼고, 다른 문장은 남는다", () => {
  const lines = narrate(
    narrativeValues({ report: { ...report, strategy: { cagr: Number.NaN, mdd: -0.1, sharpe: 0.5 } }, run }),
  );
  assert.ok(!lines.some((l) => l.id === "cagr"));
  assert.ok(lines.some((l) => l.id === "risk"));
});

test("검증 출처는 passed 이고 아는 값일 때만", () => {
  const v1 = narrativeValues({ report, run: { ...run, validation: { status: "failed", source: "stored" } } });
  assert.equal(v1.validation_source, null);
  const v2 = narrativeValues({ report, run: { ...run, validation: { status: "passed", source: "모름" } } });
  assert.equal(v2.validation_source, null);
});

test("현금 목표가 하한과 같으면 '그대로' 문장", () => {
  const lines = narrate(
    narrativeValues({ report: { ...report, cashTarget: 0.05, feasibility: { adjusted: { ranges: 0, cashFrom: 0.05 } } }, run }),
  );
  const b = lines.find((l) => l.id === "bounds");
  assert.ok(b);
  assert.match(b.text, /범위를 보정하지 않았고, 현금 목표는 현금 하한 그대로 5%/);
});

test("확정 현금 목표가 없으면 성향 하한으로 채우지 않고 문장을 뺀다", () => {
  const lines = narrate(narrativeValues({ report: { ...report, cashTarget: null }, run }));
  assert.ok(!lines.some((l) => l.id === "bounds"));
});

test("문장 틀은 화이트리스트 칸만 쓴다", () => {
  const allowed = new Set(Object.keys(NARRATIVE_FIELDS));
  for (const s of SENTENCES) for (const f of s.uses) assert.ok(allowed.has(f), `${s.id} 가 ${f} 를 쓴다`);
  // narrativeValues 가 내는 칸도 정확히 화이트리스트와 같다
  const keys = Object.keys(narrativeValues({ report, run })).sort();
  assert.deepEqual(keys, [...allowed].sort());
});

test("문장 틀이 쓰지 않는 칸은 문장에 영향을 주지 않는다", () => {
  // 타입으로도 막혀 있지만(render 는 uses 칸만 받는다) 실행에서도 확인한다
  const base = narrativeValues({ report, run });
  const before = narrate(base).find((l) => l.id === "cagr")?.text;
  const changed: NarrativeValues = { ...base, sharpe: 99 };
  const after = narrate(changed).find((l) => l.id === "cagr")?.text;
  assert.equal(before, after);
});
