// 데모 샘플 결과(public/data/demo-samples.json)의 모양과, 그걸 화면 값으로 바꾸는 순수 함수.
//
// 파일은 샘플 문장을 실제 스택(컴파일 → 검증 → 백테스트)에 차례로 넣어 본 기록이다. 다른 스크립트가
// 만들어 public/data 에 둔다. 화면(/samples, /repro)은 이 파일을 브라우저에서 fetch 로 읽고,
// 없으면 빈 상태를 보여 준다 — 파일이 없을 때 지어낸 수치를 그리지 않는다.
//
// 칸은 대부분 null 일 수 있다(실패한 단계 뒤의 칸, 측정하지 않은 칸). 화면은 null 을 "–" 로 그린다.
// node 의 타입 제거 실행(scripts/check-demo.ts)이 읽을 수 있게 런타임 import 를 두지 않는다.

export const DEMO_SAMPLES_URL = "/data/demo-samples.json";

// 성공 기준(시연 4번). 정상 문장은 끝까지 돌고, 일부러 막히게 만든 문장은 막혀야 한다.
export const SUCCESS = { pass: 8, blocked: 2 } as const;

export type CaseResult = "pass" | "blocked_expected" | "fail";

export type CompileRound = { question: string | null; choices: string[] | null; answer: string | null };

export type UniverseEntry = { ticker: string; name: string | null; weight_min: number | null; weight_max: number | null };

export type DemoCase = {
  no: string;
  profile_level: number | null;
  profile_label: string | null;
  text: string;
  axis: string | null;
  expected: string | null;
  compile: {
    status: "completed" | "need_answer" | "failed" | null;
    rounds: CompileRound[] | null;
    seconds_total: number | null;
    seconds_processing: number | null;
    spec_id: string | null;
    n_universe: number | null;
    universe: UniverseEntry[] | null;
    rebalance: unknown;
    failure_reason: string | null;
    // 요청했지만 담지 못한 종목(lib/excluded.ts 모양). 나중에 추가될 수 있는 칸
    excluded?: unknown;
  } | null;
  validation: {
    passed: boolean | null;
    stage_failed: string | number | null;
    reasons: unknown[] | null;
    adjustments: unknown[] | null;
  } | null;
  backtest: {
    run_id: number | null;
    status: string | null;
    cagr: number | null;
    mdd: number | null;
    sharpe: number | null;
    view_sources: Record<string, string> | null;
    failure_reason: string | null;
  } | null;
  result: CaseResult | null;
  note: string | null;
};

export type DemoSamples = {
  generated_at: string | null;
  git_commit: string | null;
  llm_model: string | null;
  period: { start: string | null; end: string | null } | null;
  cases: DemoCase[] | null;
  latency: {
    compile_seconds_median: number | null;
    processing_seconds_median: number | null;
    n: number | null;
    baseline_seconds: number | null;
    baseline_date: string | null;
  } | null;
  reproducibility: {
    backtest: {
      spec_id: string | null;
      run_ids: (number | null)[] | null;
      same_weights: boolean | null;
      same_orders: boolean | null;
      same_curve: boolean | null;
      n_decisions: number | null;
      sample_orders: unknown[] | null;
    } | null;
    compile: {
      text: string | null;
      spec_ids: (string | null)[] | null;
      same_spec: boolean | null;
      diff: unknown[] | null;
    } | null;
  } | null;
};

export type LoadResult =
  | { kind: "ok"; data: DemoSamples }
  // 파일이 아직 없다(404). 빈 상태 안내를 그린다.
  | { kind: "missing" }
  | { kind: "error"; message: string };

// 파일을 받는다. fetch 를 인자로 받아 서버 없이 확인할 수 있게 한다.
export async function loadDemoSamples(fetcher: typeof fetch = fetch): Promise<LoadResult> {
  let res: Response;
  try {
    res = await fetcher(DEMO_SAMPLES_URL, { cache: "no-store" });
  } catch (err) {
    return { kind: "error", message: `파일을 받지 못했습니다 — ${err instanceof Error ? err.message : String(err)}` };
  }
  if (res.status === 404) return { kind: "missing" };
  if (!res.ok) return { kind: "error", message: `파일을 받지 못했습니다 — HTTP ${res.status}` };
  try {
    const data = (await res.json()) as unknown;
    if (!data || typeof data !== "object") return { kind: "error", message: "파일 내용이 객체가 아닙니다." };
    return { kind: "ok", data: data as DemoSamples };
  } catch {
    return { kind: "error", message: "파일을 JSON 으로 읽지 못했습니다." };
  }
}

// --- 요약 ------------------------------------------------------------------

export type Summary = {
  n: number;
  pass: number;
  blocked: number;
  fail: number;
  // result 칸이 비었거나 모르는 값인 건수. 0 이 아니면 판정을 내리지 않는다.
  unknown: number;
  // 성공 기준 충족 여부. 결과를 모르는 건이 있으면 null.
  met: boolean | null;
};

export function summarize(cases: DemoCase[]): Summary {
  let pass = 0;
  let blocked = 0;
  let fail = 0;
  let unknown = 0;
  for (const c of cases) {
    if (c.result === "pass") pass++;
    else if (c.result === "blocked_expected") blocked++;
    else if (c.result === "fail") fail++;
    else unknown++;
  }
  const met = unknown > 0 ? null : fail === 0 && pass >= SUCCESS.pass && blocked >= SUCCESS.blocked;
  return { n: cases.length, pass, blocked, fail, unknown, met };
}

export type LatencyVerdict = {
  median: number;
  baseline: number;
  // median / baseline. 1 보다 작으면 기준보다 빠르다.
  ratio: number;
  faster: boolean;
};

export function latencyVerdict(latency: DemoSamples["latency"]): LatencyVerdict | null {
  const median = latency?.compile_seconds_median;
  const baseline = latency?.baseline_seconds;
  if (!isNum(median) || !isNum(baseline) || baseline <= 0) return null;
  return { median, baseline, ratio: median / baseline, faster: median <= baseline };
}

// --- 칸 그리기 도우미 --------------------------------------------------------

export function isNum(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

// 비율 → "+8.1%". null 이면 "–".
export function fmtPct(v: number | null | undefined, d = 1, signed = true): string {
  if (!isNum(v)) return "–";
  const s = (v * 100).toFixed(d);
  if (!signed) return `${s}%`;
  return v > 0 ? `+${s}%` : v < 0 ? `−${s.slice(1)}%` : `${s}%`;
}

export function fmtNum(v: number | null | undefined, d = 2): string {
  if (!isNum(v)) return "–";
  return v < 0 ? `−${Math.abs(v).toFixed(d)}` : v.toFixed(d);
}

export function fmtSec(v: number | null | undefined): string {
  return isNum(v) ? `${v.toFixed(1)}초` : "–";
}

// 사유 · 보정 · diff 처럼 모양이 정해지지 않은 칸. 문자열은 그대로, 나머지는 JSON 한 줄.
export function fmtAny(v: unknown): string {
  if (v === null || v === undefined) return "–";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

// 리밸런싱 이름표 — lib/data.ts FREQ_LABEL · TRIGGER_TYPE_LABEL 사본(이 파일은 import 를 두지 않는다).
const REBALANCE_LABEL: Record<string, string> = {
  monthly: "월간",
  weekly: "주간",
  quarterly: "분기",
  daily: "매일",
  threshold: "이탈형",
  signal: "신호형",
};

function rebalanceKindLabel(t: { type?: string; freq?: string }): string {
  return REBALANCE_LABEL[t.type ?? ""] ?? REBALANCE_LABEL[t.freq ?? ""] ?? t.freq ?? "?";
}

// 리밸런싱 규칙. 전략서 모양({trigger:{type,freq,day}, min_interval_days})이면 사람 말로, 아니면 그대로.
export function fmtRebalance(v: unknown): string {
  if (v && typeof v === "object" && "trigger" in v) {
    const r = v as { trigger?: { type?: string; freq?: string; day?: number }; min_interval_days?: number };
    const kind = r.trigger ? rebalanceKindLabel(r.trigger) : null;
    const parts = [kind, r.trigger?.day != null ? `${r.trigger.day}일` : null].filter(Boolean);
    if (r.min_interval_days != null) parts.push(`최소 간격 ${r.min_interval_days}일`);
    return parts.join(" · ") || fmtAny(v);
  }
  return fmtAny(v);
}

// 유니버스 한 줄 요약: "3종목 — KODEX 200, TIGER 국채3년 외 1"
export function universeSummary(c: DemoCase["compile"]): string {
  if (!c) return "–";
  const list = c.universe ?? [];
  const n = c.n_universe ?? (list.length || null);
  if (n === null) return "–";
  const names = list.slice(0, 2).map((u) => u.name ?? u.ticker);
  const rest = list.length > 2 ? ` 외 ${list.length - 2}` : "";
  return names.length ? `${n}종목 — ${names.join(", ")}${rest}` : `${n}종목`;
}

export const RESULT_BADGE: Record<CaseResult, { label: string; tone: "ok" | "warn" | "err" }> = {
  pass: { label: "통과", tone: "ok" },
  blocked_expected: { label: "의도된 차단", tone: "warn" },
  fail: { label: "실패", tone: "err" },
};

export const COMPILE_STATUS: Record<string, string> = {
  completed: "완료",
  need_answer: "되묻기에서 멈춤",
  failed: "실패",
};

// "2026-10-27T09:12:00+09:00" → "2026-10-27 09:12". 모양이 다르면 그대로.
export function fmtStamp(v: string | null | undefined): string {
  if (!v) return "–";
  const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(v);
  return m ? `${m[1]} ${m[2]}` : v;
}

export function periodLabel(p: DemoSamples["period"]): string {
  if (!p?.start && !p?.end) return "기간 기록 없음";
  return `${p?.start ?? "?"} ~ ${p?.end ?? "?"}`;
}

// 보정 한 건. {kind, stage, field, requested, applied, limit} 모양이면 "4단 하드캡 · 칸 0.4 → 0.3 (한도)" 로,
// 아니면 fmtAny 로 그대로.
export function fmtAdjustment(v: unknown): string {
  if (v && typeof v === "object" && !Array.isArray(v)) {
    const a = v as Record<string, unknown>;
    if ("field" in a && ("requested" in a || "applied" in a)) {
      const head = [a.stage != null ? `${a.stage}단` : null, typeof a.kind === "string" ? a.kind : null]
        .filter(Boolean)
        .join(" ");
      const val = (x: unknown) => (isNum(x) ? String(Number(x.toFixed(6))) : fmtAny(x));
      const limit = typeof a.limit === "string" ? ` (${a.limit})` : "";
      return `${head ? `${head} · ` : ""}${fmtAny(a.field)} ${val(a.requested)} → ${val(a.applied)}${limit}`;
    }
    // 3단 범위 보정(run_demo_samples.validate 의 {kind: "bounds", stage, case, cash_min, items}).
    if (a.kind === "bounds") {
      const n = Array.isArray(a.items) ? a.items.length : null;
      const parts = [
        `${a.stage != null ? `${a.stage}단 ` : ""}범위 보정${typeof a.case === "string" ? ` (Case ${a.case})` : ""}`,
        n !== null ? `${n}종목` : null,
        isNum(a.cash_min) ? `현금 하한 ${Number((a.cash_min * 100).toFixed(2))}%` : null,
      ];
      return parts.filter(Boolean).join(" · ");
    }
  }
  return fmtAny(v);
}
