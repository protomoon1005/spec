// ---------------------------------------------------------------------------
// 화면이 읽는 백테스트 결과.
//
// 여기 있는 값은 합성이 아니다. 결과 하나를 받아 지표를 계산하는 buildReport 가
// 전부이고, 수치를 화면에 하드코딩하지 않는다.
//
// 결과는 두 곳에서 온다.
//   - 정적  data/backtest-result.json. scripts/run-backtest.ts 가 KRX 종가로 돌린
//           데모 결과다. 로그인 없이 /report 를 열면 이걸 그린다.
//   - 실행  GET /backtest/runs/{run_id}. 사용자가 방금 만든 전략서로 돌린 결과다.
//           /report?run_id= 로 열면 이걸 그린다(lib/report-from-run.ts 가 모양을 맞춘다).
//
// 둘이 같은 함수를 지나가므로 같은 결과면 같은 화면이 나온다. 예전에는 이 모듈이
// 정적 JSON 을 import 해서 모듈 상수로 계산했고, 그래서 /report 는 사용자가 무엇을
// 돌렸든 늘 같은 데모 결과를 보여 줬다.
//
// 정적 결과를 다시 만들려면:
//   node --experimental-strip-types scripts/run-backtest.ts
//
// 아직 목데이터인 것: 3관점 가중치(Hedge). M3 미구현이라 결정론적 모의값이다.
// ---------------------------------------------------------------------------

import staticResult from "@/data/backtest-result.json";
import {
  GROUP_LABEL,
  HARDCAP,
  profileFor,
  resolveBounds,
  type Bound,
  type Grade,
} from "@/lib/policy";

export { GROUP_LABEL, HARDCAP, type Bound };

// --- 결과와 무관한 상수 ------------------------------------------------------
export const RF = 0.025; // 무위험수익률 연 2.5%
export const FEATURESET = "v0.1";
export const FOLD_COUNT = 6;
export const SEED = 20260908;
export const VIEW_META = [
  { key: "market", label: "시장 분석", basis: "momentum_20d · rsi_14 · volume_ratio_20d", kind: "지도학습 (LightGBM + SHAP)", meanLoss: 0.185, prob: 0.68 },
  { key: "sentiment", label: "감성 분석", basis: "대형주 · 중소형주 뉴스 48시간", kind: "사전학습 언어모델 + 감성 렌즈", meanLoss: 0.243, prob: 0.55 },
  { key: "temperature", label: "시장 온도", basis: "KOSPI 200일 이동평균 · VKOSPI", kind: "규칙 및 통계 모형", meanLoss: 0.211, prob: 0.61 },
] as const;
// 리밸런싱 주기 표시. 러너가 지원하는 것은 backend/app/backtest/inputs.py _SCHEDULES 에 있다.
export const FREQ_LABEL: Record<string, string> = { monthly: "월간", weekly: "주간" };

export const ETA = 0.5;
export const W_FLOOR = 0.1;
export const EVAL_WINDOW = 60;

// --- 결과 모양 ---------------------------------------------------------------
export type Point = { date: string; strategy: number; control: number; market: number };

export type CapApplication = { stage: string; groupId: string; sumBefore: number; cap: number; factor: number };

export type Decision = {
  date: string;
  signals: Record<string, number>;
  mapped: Record<string, number>;
  target: Record<string, number>;
  cash: number;
  capApplications: CapApplication[];
  // TS 러너만 낸다. 파이썬 러너는 체결을 vectorbt 에 맡겨서 결정 기록에 남기지 않는다.
  turnover?: number;
  cost?: number;
};

export type UniverseItem = {
  ticker: string;
  name: string;
  grade: string;
  asset_group: string;
  sector_group: string;
  country_group: string;
  weight_min_raw: number;
  weight_max_raw: number;
};

export type BacktestResult = {
  as_of: string;
  period_start: string;
  initial: number;
  profile: { label: string; risk_level: number; cash_min: number };
  costs: { fee: number; tax: number; slippage: number };
  hardcap_max_per_asset: number;
  data_source: string;
  price_field: string;
  universe: UniverseItem[];
  caps: Record<string, number>;
  series: Point[];
  decisions: Decision[];
  control_decisions: Decision[];
};

// 세 관점 규칙은 계약(app/contracts/spec.py SignalRules)에서 전부 Optional 이다.
// 감성이 중립 고정된 뒤로 실제 전략서는 sentiment 가 null 일 수 있다.
export type SignalRulesView = {
  market_analysis?: { indicators: string[] } | null;
  sentiment?: { target_sectors: string[]; lookback_hours: number } | null;
  market_temperature?: { trend_index: string; trend_ma_window: number; volatility_index: string } | null;
};

export type ConstraintInput = {
  max_weight_per_asset?: number;
  min_weight_per_asset?: number;
  cash_min?: number;
  max_loss_per_trade?: number;
  max_drawdown?: number;
};

export type RebalanceRule = { trigger: { type: string; freq: string; day: number }; min_interval_days: number };

/** 결과 파일에 없는 전략서 정보. 정적 데모는 고정값, 실행은 GET /specs/{id} 에서 온다. */
export type SpecMeta = {
  spec_id: string;
  spec_version: string;
  user_id: number;
  name: string;
  created_at: string;
  rebalance: RebalanceRule;
  signal_rules: SignalRulesView;
  constraint: ConstraintInput | null;
  /** 표시용 리밸런싱 규칙. 없으면 rebalance.trigger 로 만든다 */
  triggerLabel?: string;
  /** 사용자가 쓴 요청 문장. 정적 데모에는 없다 */
  inputPrompt?: string | null;
  /** 실행 번호. 없으면 정적 데모 결과다 — 화면이 둘을 구분해 적는다 */
  runId?: number;
};

// 정적 데모 결과(data/backtest-result.json)의 전략서. 이 데모는 자연어로 만든 것이
// 아니라 scripts/run-backtest.ts 의 SPEC_UNIVERSE 로 손으로 고른 것이다.
const STATIC_SPEC_META: SpecMeta = {
  spec_id: "STR-001",
  spec_version: "0.1",
  user_id: 1,
  name: "코스피 코어 + 반도체 위성",
  created_at: "2026-09-08T09:00:00+09:00",
  rebalance: { trigger: { type: "calendar", freq: "monthly", day: 1 }, min_interval_days: 20 },
  signal_rules: {
    market_analysis: { indicators: ["momentum_20d", "rsi_14", "volume_ratio_20d"] },
    sentiment: { target_sectors: ["대형주", "중소형주"], lookback_hours: 48 },
    market_temperature: { trend_index: "KOSPI", trend_ma_window: 200, volatility_index: "VKOSPI" },
  },
  // cash_min 은 비워 둔다 — 성향의 현금 하한을 그대로 쓴다.
  constraint: { max_weight_per_asset: 0.35, min_weight_per_asset: 0.0, max_loss_per_trade: 0.05, max_drawdown: 0.2 },
  triggerLabel: "calendar · monthly · first_trading_day",
};

// --- 지표 계산 (결과와 무관한 함수) -----------------------------------------
export type Metrics = {
  total: number;
  cagr: number;
  vol: number;
  sharpe: number;
  sortino: number;
  downside: number;
  mdd: number;
  calmar: number;
  final: number;
};

function stdev(xs: number[]): number {
  if (xs.length < 2) return 0;
  const m = xs.reduce((a, b) => a + b, 0) / xs.length;
  return Math.sqrt(xs.reduce((a, b) => a + (b - m) ** 2, 0) / (xs.length - 1));
}

export function computeMetrics(values: number[], years?: number): Metrics {
  const first = values[0];
  const last = values[values.length - 1];
  const rets: number[] = [];
  for (let i = 1; i < values.length; i++) rets.push(values[i] / values[i - 1] - 1);

  const yrs = years ?? (values.length - 1) / 52;
  const total = last / first - 1;
  const cagr = Math.pow(last / first, 1 / yrs) - 1;
  const vol = stdev(rets) * Math.sqrt(52);
  const down = stdev(rets.map((r) => Math.min(r, 0))) * Math.sqrt(52);

  let peak = first;
  let mdd = 0;
  for (const v of values) {
    if (v > peak) peak = v;
    mdd = Math.min(mdd, v / peak - 1);
  }

  return {
    total,
    cagr,
    vol,
    sharpe: vol === 0 ? 0 : (cagr - RF) / vol,
    sortino: down === 0 ? 0 : (cagr - RF) / down,
    downside: down,
    mdd,
    calmar: mdd === 0 ? 0 : cagr / Math.abs(mdd),
    final: last,
  };
}

function drawdownOf(values: number[]): number[] {
  let peak = values[0];
  return values.map((v) => {
    if (v > peak) peak = v;
    return Number(((v / peak - 1) * 100).toFixed(2));
  });
}

function shiftMonths(iso: string, months: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCMonth(d.getUTCMonth() + months);
  return d.toISOString().slice(0, 10);
}

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gaussFrom(rnd: () => number): number {
  return Math.sqrt(-2 * Math.log(1 - rnd())) * Math.cos(2 * Math.PI * rnd());
}

function normalizeWithFloor(raw: number[], floor: number): number[] {
  const w = [...raw];
  for (let pass = 0; pass < w.length; pass++) {
    const sum = w.reduce((a, b) => a + b, 0);
    const scaled = w.map((x) => x / sum);
    const pinned = scaled.map((x) => x <= floor);
    if (!pinned.some(Boolean)) return scaled;
    const freeBudget = 1 - floor * pinned.filter(Boolean).length;
    const freeSum = scaled.reduce((a, x, i) => (pinned[i] ? a : a + x), 0);
    for (let i = 0; i < w.length; i++) w[i] = pinned[i] ? floor : (scaled[i] / freeSum) * freeBudget;
  }
  return w;
}

// --- 화면이 쓰는 행 모양 -----------------------------------------------------
export type MonthlyReturn = { month: string; ret: number; partial: boolean };

export type Fold = {
  id: string;
  trainFrom: string;
  trainTo: string;
  testFrom: string;
  testTo: string;
  ret: number;
  controlRet: number;
  excess: number;
  mdd: number;
  marketMdd: number;
  sharpe: number;
};

export type Asset = {
  ticker: string;
  name: string;
  grade: string;
  assetGroup: string;
  sectorGroup: string;
  countryGroup: string;
  minRaw: number;
  maxRaw: number;
  signal: number;
};

export type WeightRow = {
  asset: Asset;
  bound: Bound;
  normalized: number;
  controlWeight: number;
  final: number;
  capped: string | null;
};

export type CapLog = { stage: string; groupId: string; label: string; before: number; cap: number; factor: number };

export type RebalanceRow = { ticker: string; name: string; before: number; target: number; amount: number };

export type WeightHistory = { date: string; market: number; sentiment: number; temperature: number };

export type ClampRow = { field: string; requested: number; hardcap: number; resolved: number; clamped: boolean };

export type MetricGuide = {
  id: string;
  title: string;
  english: string;
  summary: string;
  formula: string;
  reading: string;
  caveat: string;
};

// buildReport 가 쓰므로 그보다 위에 둔다. 아래에 두면 정적 데모(defaultReport)를
// 만드는 순간 아직 초기화 전이라 ReferenceError 가 난다.
// --- 포맷터 ------------------------------------------------------------------
export const won = (n: number) =>
  new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 }).format(n);
// 부호는 반올림한 뒤에 붙인다. 표시되는 자릿수가 전부 0 인데 "+" 가 붙으면
// (+0.0%) 늘지도 줄지도 않은 값이 는 것처럼 읽힌다. 카운트업이 0 을 지나갈 때도
// 같은 문제가 생긴다.
const signed = (scaled: number, d: number, unit: string) => {
  const body = Math.abs(scaled).toFixed(d);
  if (Number(body) === 0) return `${body}${unit}`;
  return `${scaled >= 0 ? "+" : "−"}${body}${unit}`;
};
export const pct = (n: number, d = 2) => signed(n * 100, d, "%");
export const pctPlain = (n: number, d = 1) => `${(n * 100).toFixed(d)}%`;
export const pp = (n: number, d = 1) => signed(n * 100, d, "%p");
// 샤프가 0 에 붙으면 (-0.001).toFixed(2) 가 "-0.00" 이 된다. 부호 없는 값이라도
// 반올림한 자릿수가 전부 0 이면 음수 부호를 떼어 낸다 — pct·pp 와 같은 규칙이다.
// 정적 데모는 샤프가 2 점대라 안 보였고, 채권 위주 실행(CAGR ≈ 무위험)에서 드러났다.
export const num = (n: number, d = 2) => {
  const s = n.toFixed(d);
  return Number(s) === 0 ? Math.abs(Number(s)).toFixed(d) : s;
};
export const ym = (iso: string) => iso.slice(2, 7).replace("-", ".");

/**
 * 읽히는 Y축 눈금을 만든다.
 *
 * recharts 의 기본 눈금은 데이터 범위를 그대로 쪼개서 909만 · 1168만 같은
 * 값을 낸다. 사람이 축을 훑으면서 "지금 얼마쯤" 을 가늠하려면 눈금이 먼저
 * 반올림돼 있어야 한다. 그래서 간격을 1 · 2 · 2.5 · 5 × 10ⁿ 중에서 고르고
 * 양 끝을 그 간격에 맞춰 넓힌다.
 *
 * 반환값의 첫/마지막 원소를 그대로 domain 으로 주면 축이 눈금에서 끝난다.
 */
export function niceTicks(min: number, max: number, count = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) return [min, max];
  const raw = (max - min) / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const out: number[] = [];
  // 부동소수 누적 오차로 마지막 눈금이 빠지는 것을 막는다.
  for (let v = lo; v <= hi + step / 2; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

// --- 결과 하나 -> 화면 값 ---------------------------------------------------
export function buildReport(result: BacktestResult, meta: SpecMeta = STATIC_SPEC_META) {
  const AS_OF = result.as_of;
  const SNAPSHOT = AS_OF;
  const PERIOD_START = result.period_start;
  const PERIOD_END = AS_OF;
  const INITIAL = result.initial;

  const DATA_SOURCE = result.data_source;
  const PRICE_FIELD = result.price_field;
  const PROFILE_LABEL = result.profile.label;
  const RISK_LEVEL = result.profile.risk_level;

  const COST_MODEL = {
    fee: result.costs.fee,
    tax: result.costs.tax,
    slippage: result.costs.slippage,
  };

  const PROFILE = {
    ...profileFor(result.profile.risk_level),
    presetVersion: "v0.1",
    source: `허용범위 프리셋 v0.1 (asset_bound_presets, risk_level=${result.profile.risk_level})`,
  };

  const GROUP_CAPS: Record<string, number> = result.caps;

  // --- 자산곡선 --------------------------------------------------------------
  // strategy = 3관점 신호 사용, control = 같은 제약·신호 없음, market = KODEX 200 매수후보유
  const series: Point[] = result.series;

  // 자산곡선 Y축 눈금. 그 차트가 그리는 두 계열(전략·대조군)의 범위에서 낸다.
  // 시장은 별도 차트라 여기 넣지 않는다 — 스케일이 달라 두 계열이 납작해진다.
  const equityTicks: number[] = niceTicks(
    Math.min(...series.map((p) => Math.min(p.strategy, p.control))),
    Math.max(...series.map((p) => Math.max(p.strategy, p.control))),
  );

  // --- 성과지표 --------------------------------------------------------------
  const YEARS =
    (new Date(`${PERIOD_END}T00:00:00Z`).getTime() - new Date(`${PERIOD_START}T00:00:00Z`).getTime()) /
    (365.25 * 864e5);

  const strategy = computeMetrics(series.map((p) => p.strategy), YEARS);
  const control = computeMetrics(series.map((p) => p.control), YEARS);
  const market = computeMetrics(series.map((p) => p.market), YEARS);

  /** 신호가 더한 값. 전략 − 대조군. 이 시스템이 증명해야 하는 숫자다. */
  const signalAlpha = strategy.total - control.total;
  const signalAlphaAnnual = strategy.cagr - control.cagr;
  /** 시장 대비. 위험 수준이 달라 우열이 아니라 맥락으로 본다. */
  const vsMarket = strategy.total - market.total;

  // --- 낙폭 ------------------------------------------------------------------
  const ddStrategy = drawdownOf(series.map((p) => p.strategy));
  const ddMarket = drawdownOf(series.map((p) => p.market));
  const drawdownSeries = series.map((p, i) => ({
    date: p.date,
    drawdown: ddStrategy[i],
    marketDrawdown: ddMarket[i],
  }));

  // --- 월별 수익률 -----------------------------------------------------------
  const AS_OF_MONTH_END = (() => {
    const d = new Date(`${AS_OF}T00:00:00Z`);
    return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).toISOString().slice(0, 10);
  })();
  const LAST_MONTH_PARTIAL = AS_OF !== AS_OF_MONTH_END;

  const monthlyReturns: MonthlyReturn[] = (() => {
    const byMonth = new Map<string, number>();
    for (const p of series) byMonth.set(p.date.slice(0, 7), p.strategy);
    const months = [...byMonth.keys()].sort();
    const out: MonthlyReturn[] = [];
    for (let i = 1; i < months.length; i++) {
      out.push({
        month: months[i],
        ret: Number(((byMonth.get(months[i])! / byMonth.get(months[i - 1])! - 1) * 100).toFixed(2)),
        partial: LAST_MONTH_PARTIAL && i === months.length - 1,
      });
    }
    return out.slice(-18);
  })();

  const completeMonths = monthlyReturns.filter((m) => !m.partial);
  const bestMonth = completeMonths.reduce((a, b) => (b.ret > a.ret ? b : a));
  const worstMonth = completeMonths.reduce((a, b) => (b.ret < a.ret ? b : a));

  // --- 워크포워드 구간 -------------------------------------------------------
  const folds: Fold[] = (() => {
    const n = series.length - 1;
    const per = Math.floor(n / FOLD_COUNT);
    const out: Fold[] = [];
    for (let k = 0; k < FOLD_COUNT; k++) {
      const from = k * per;
      const to = k === FOLD_COUNT - 1 ? n : (k + 1) * per;
      const slice = series.slice(from, to + 1);
      if (slice.length < 3) continue;
      const yrs = (to - from) / 52;
      const s = computeMetrics(slice.map((p) => p.strategy), yrs);
      const c = computeMetrics(slice.map((p) => p.control), yrs);
      const m = computeMetrics(slice.map((p) => p.market), yrs);
      out.push({
        id: `WF-${k + 1}`,
        trainFrom: shiftMonths(slice[0].date, -12),
        trainTo: slice[0].date,
        testFrom: slice[0].date,
        testTo: slice[slice.length - 1].date,
        ret: s.total,
        controlRet: c.total,
        excess: s.total - c.total,
        mdd: s.mdd,
        marketMdd: m.mdd,
        sharpe: s.sharpe,
      });
    }
    return out;
  })();

  // --- 유니버스와 목표 비중 --------------------------------------------------
  const lastDecision = result.decisions[result.decisions.length - 1];
  const lastControl = result.control_decisions[result.control_decisions.length - 1];

  const universe: Asset[] = result.universe.map((u) => ({
    ticker: u.ticker,
    name: u.name,
    grade: u.grade,
    assetGroup: u.asset_group,
    sectorGroup: u.sector_group,
    countryGroup: u.country_group,
    minRaw: u.weight_min_raw,
    maxRaw: u.weight_max_raw,
    signal: lastDecision.signals[u.ticker] ?? 0,
  }));

  const bounds: Record<string, Bound> = resolveBounds(
    universe.map((a) => ({
      ticker: a.ticker,
      grade: a.grade as Grade,
      weightMinRaw: a.minRaw,
      weightMaxRaw: a.maxRaw,
    })),
    PROFILE,
    HARDCAP.maxWeightPerAsset,
  );

  const cappedBy = new Map<string, string[]>();
  for (const app of lastDecision.capApplications) {
    for (const a of universe) {
      const g = app.stage === "자산군" ? a.assetGroup : app.stage === "국가" ? a.countryGroup : a.sectorGroup;
      if (g === app.groupId) cappedBy.set(a.ticker, [...(cappedBy.get(a.ticker) ?? []), app.stage]);
    }
  }

  const weightRows: WeightRow[] = universe.map((a) => ({
    asset: a,
    bound: bounds[a.ticker],
    normalized: lastDecision.mapped[a.ticker] ?? 0,
    controlWeight: lastControl.target[a.ticker] ?? 0,
    final: lastDecision.target[a.ticker] ?? 0,
    capped: cappedBy.get(a.ticker)?.join("·") ?? null,
  }));

  const targetCash = lastDecision.cash;

  const capLogs: CapLog[] = lastDecision.capApplications.map((a) => ({
    stage: a.stage === "자산군" ? "상위 · 자산군" : `하위 · ${a.stage}`,
    groupId: a.groupId,
    label: GROUP_LABEL[a.groupId] ?? a.groupId,
    before: a.sumBefore,
    cap: a.cap,
    factor: a.factor,
  }));

  /** 전체 기간 동안 그룹캡이 몇 번 걸렸는지 */
  const capApplicationCount = result.decisions.reduce((n, d) => n + d.capApplications.length, 0);
  const rebalanceCount = result.decisions.length;
  const trig = meta.rebalance.trigger;
  const lastRebalance = {
    date: lastDecision.date,
    trigger: meta.triggerLabel ?? `${trig.type} · ${trig.freq} · day ${trig.day}`,
    minInterval: meta.rebalance.min_interval_days,
    turnover: lastDecision.turnover,
    cost: lastDecision.cost,
  };

  // --- 리밸런싱 이력 ---------------------------------------------------------
  const prevDecision = result.decisions[result.decisions.length - 2] ?? lastDecision;

  const rebalanceRows: RebalanceRow[] = universe.map((a) => {
    const before = prevDecision.target[a.ticker] ?? 0;
    const target = lastDecision.target[a.ticker] ?? 0;
    return {
      ticker: a.ticker,
      name: a.name,
      before,
      target,
      amount: Math.round(((target - before) * strategy.final) / 10) * 10,
    };
  });

  // --- 3관점 (아직 목데이터 — M3 미구현) -------------------------------------
  const viewWeightHistory: WeightHistory[] = (() => {
    const rnd = mulberry32(SEED + 7);
    const lossLog: number[][] = [];
    const out: WeightHistory[] = [];
    for (const p of series) {
      const window = lossLog.slice(-EVAL_WINDOW);
      const cumulative = VIEW_META.map((_, k) => window.reduce((a, row) => a + row[k], 0));
      const minLoss = Math.min(...cumulative);
      const w = normalizeWithFloor(cumulative.map((L) => Math.exp(-ETA * (L - minLoss))), W_FLOOR);
      out.push({ date: p.date, market: w[0], sentiment: w[1], temperature: w[2] });
      lossLog.push(VIEW_META.map((v) => Math.min(1, Math.max(0, v.meanLoss + 0.09 * gaussFrom(rnd)))));
    }
    return out;
  })();

  const viewWeights = viewWeightHistory[viewWeightHistory.length - 1];
  const viewWeightOf: Record<string, number> = {
    market: viewWeights.market,
    sentiment: viewWeights.sentiment,
    temperature: viewWeights.temperature,
  };
  const integratedProb =
    viewWeights.market * VIEW_META[0].prob +
    viewWeights.sentiment * VIEW_META[1].prob +
    viewWeights.temperature * VIEW_META[2].prob;
  const integratedSignal = 2 * integratedProb - 1;

  // --- 전략 Spec -------------------------------------------------------------
  // 전략서가 제약을 비워 두면 시스템 하드캡이 그대로 실효 상한이다. 현금 하한은 성향 값.
  const c = meta.constraint ?? {};
  const spec = {
    spec_id: meta.spec_id,
    spec_version: meta.spec_version,
    user_id: meta.user_id,
    name: meta.name,
    created_at: meta.created_at,
    universe: universe.map((a) => ({
      ticker: a.ticker,
      name: a.name,
      weight_min: bounds[a.ticker].min,
      weight_max: bounds[a.ticker].max,
    })),
    rebalance: meta.rebalance,
    signal_rules: meta.signal_rules,
    constraint: {
      max_weight_per_asset: c.max_weight_per_asset ?? HARDCAP.maxWeightPerAsset,
      min_weight_per_asset: c.min_weight_per_asset ?? 0,
      cash_min: c.cash_min ?? PROFILE.cashMin,
      max_loss_per_trade: c.max_loss_per_trade ?? HARDCAP.maxLossPerTrade,
      max_drawdown: c.max_drawdown ?? HARDCAP.maxDrawdown,
    },
  };
  const inputPrompt = meta.inputPrompt ?? null;
  const runId = meta.runId ?? null;
  const rebalanceLabel = `${FREQ_LABEL[trig.freq] ?? trig.freq} 리밸런싱`;

  // 낙폭 차트 Y축 눈금. 위는 항상 0(고점)이고 아래만 데이터에서 정한다.
  // 제약 상한선(max_drawdown)도 이 안에 들어와야 기준선이 잘리지 않는다.
  const drawdownTicks: number[] = niceTicks(
    Math.min(...ddStrategy, ...ddMarket, -spec.constraint.max_drawdown * 100),
    0,
    4,
  );

  const constraintResolved: ClampRow[] = [
    { field: "max_weight_per_asset", requested: spec.constraint.max_weight_per_asset, hardcap: HARDCAP.maxWeightPerAsset, resolved: Math.min(spec.constraint.max_weight_per_asset, HARDCAP.maxWeightPerAsset), clamped: spec.constraint.max_weight_per_asset > HARDCAP.maxWeightPerAsset },
    { field: "cash_min", requested: spec.constraint.cash_min, hardcap: HARDCAP.cashMin, resolved: Math.max(spec.constraint.cash_min, HARDCAP.cashMin), clamped: spec.constraint.cash_min < HARDCAP.cashMin },
    { field: "max_loss_per_trade", requested: spec.constraint.max_loss_per_trade, hardcap: HARDCAP.maxLossPerTrade, resolved: Math.min(spec.constraint.max_loss_per_trade, HARDCAP.maxLossPerTrade), clamped: spec.constraint.max_loss_per_trade > HARDCAP.maxLossPerTrade },
    { field: "max_drawdown", requested: spec.constraint.max_drawdown, hardcap: HARDCAP.maxDrawdown, resolved: Math.min(spec.constraint.max_drawdown, HARDCAP.maxDrawdown), clamped: spec.constraint.max_drawdown > HARDCAP.maxDrawdown },
    { field: "min_interval_days", requested: spec.rebalance.min_interval_days, hardcap: HARDCAP.minIntervalDays, resolved: Math.max(spec.rebalance.min_interval_days, HARDCAP.minIntervalDays), clamped: spec.rebalance.min_interval_days < HARDCAP.minIntervalDays },
  ];

  // --- 지표 해설 -------------------------------------------------------------
  const p1 = (n: number) => `${(n * 100).toFixed(2)}%`;
  const n2 = (n: number) => num(n, 2);

  const sharpeBand =
    strategy.sharpe >= 1
      ? "통상 1 이상을 양호하다고 보는데, 이 전략은 그 기준을 넘습니다."
      : strategy.sharpe >= 0.5
        ? "통상 1 이상을 양호하다고 보므로, 이 전략은 보통 수준입니다."
        : "통상 1 이상을 양호하다고 보므로, 이 전략은 변동성에 비해 초과수익이 크지 않은 편입니다.";

  const sortinoRatio = strategy.sharpe === 0 ? 0 : strategy.sortino / strategy.sharpe;
  const sortinoBand =
    sortinoRatio >= 1.3
      ? `샤프지수의 ${sortinoRatio.toFixed(1)}배입니다. 흔들림이 주로 오르는 쪽에서 나왔고, 실제로 손실이 난 구간의 변동은 그보다 작았다는 뜻입니다.`
      : `샤프지수와 큰 차이가 없습니다(${sortinoRatio.toFixed(1)}배). 흔들림이 위아래로 고르게 나왔다는 뜻입니다.`;

  const calmarBand =
    strategy.calmar >= 1
      ? "1 이상이므로, 최악의 낙폭만큼을 1년 수익으로 메울 수 있었다는 뜻입니다."
      : `1 미만이므로, 최대낙폭 ${p1(Math.abs(strategy.mdd))} 를 연수익으로 메우는 데 1년보다 오래 걸린다는 뜻입니다.`;

  const METRIC_GUIDES: MetricGuide[] = [
    {
      id: "sharpe",
      title: "샤프지수",
      english: "Sharpe Ratio",
      summary: "위험을 1만큼 감수해서 은행 이자보다 얼마나 더 벌었는지를 나타냅니다.",
      formula: `(연환산 수익률 ${p1(strategy.cagr)} − 무위험 ${p1(RF)}) ÷ 연변동성 ${p1(strategy.vol)} = ${n2(strategy.sharpe)}`,
      reading: `높을수록 좋습니다. ${sharpeBand}`,
      caveat: "오르는 쪽 흔들림도 위험으로 함께 벌점을 매깁니다. 크게 오른 달이 많아도 지수는 내려갈 수 있어서, 소르티노지수와 같이 봐야 합니다.",
    },
    {
      id: "sortino",
      title: "소르티노지수",
      english: "Sortino Ratio",
      summary: "샤프지수에서 위험을 내려간 쪽 흔들림만으로 다시 계산한 값입니다.",
      formula: `(연환산 수익률 ${p1(strategy.cagr)} − 무위험 ${p1(RF)}) ÷ 하방편차 ${p1(strategy.downside)} = ${n2(strategy.sortino)}`,
      reading: `높을수록 좋습니다. ${sortinoBand}`,
      caveat: "손실 구간이 적을수록 분모가 작아져 값이 급격히 커집니다. 관측 기간이 짧으면 과장되기 쉬우니 절대값보다 샤프지수와의 차이를 보는 편이 낫습니다.",
    },
    {
      id: "calmar",
      title: "칼마지수",
      english: "Calmar Ratio",
      summary: "가장 크게 물렸던 낙폭 1만큼당 1년에 얼마를 벌었는지를 나타냅니다.",
      formula: `연환산 수익률 ${p1(strategy.cagr)} ÷ 최대낙폭 ${p1(Math.abs(strategy.mdd))} = ${n2(strategy.calmar)}`,
      reading: `높을수록 좋습니다. ${calmarBand}`,
      caveat: "최대낙폭 한 지점에만 의존합니다. 그 한 번이 우연이었는지 반복되는 성질인지는 이 지수만으로 알 수 없어서, 워크포워드 구간별 낙폭을 같이 봐야 합니다.",
    },
  ];

  return {
    AS_OF,
    SNAPSHOT,
    PERIOD_START,
    PERIOD_END,
    INITIAL,
    DATA_SOURCE,
    PRICE_FIELD,
    PROFILE_LABEL,
    RISK_LEVEL,
    COST_MODEL,
    PROFILE,
    GROUP_CAPS,
    series,
    equityTicks,
    strategy,
    control,
    market,
    signalAlpha,
    signalAlphaAnnual,
    vsMarket,
    drawdownSeries,
    LAST_MONTH_PARTIAL,
    monthlyReturns,
    bestMonth,
    worstMonth,
    folds,
    universe,
    bounds,
    weightRows,
    targetCash,
    capLogs,
    capApplicationCount,
    rebalanceCount,
    lastRebalance,
    rebalanceRows,
    viewWeightHistory,
    viewWeights,
    viewWeightOf,
    integratedProb,
    integratedSignal,
    spec,
    inputPrompt,
    runId,
    rebalanceLabel,
    drawdownTicks,
    constraintResolved,
    METRIC_GUIDES,
  };
}

export type Report = ReturnType<typeof buildReport>;

// --- 정적 데모 결과 ----------------------------------------------------------
// 로그인 없이 /report 를 열 때, 그리고 /backtest 의 폴백이 쓰는 값이다.
// 아래 이름들을 그대로 내보내 기존 import 가 바뀌지 않게 한다.
export const defaultReport = buildReport(staticResult as BacktestResult);

export const {
  AS_OF,
  SNAPSHOT,
  PERIOD_START,
  PERIOD_END,
  INITIAL,
  DATA_SOURCE,
  PRICE_FIELD,
  PROFILE_LABEL,
  RISK_LEVEL,
  COST_MODEL,
  PROFILE,
  GROUP_CAPS,
  series,
  equityTicks,
  strategy,
  control,
  market,
  signalAlpha,
  signalAlphaAnnual,
  vsMarket,
  drawdownSeries,
  LAST_MONTH_PARTIAL,
  monthlyReturns,
  bestMonth,
  worstMonth,
  folds,
  universe,
  bounds,
  weightRows,
  targetCash,
  capLogs,
  capApplicationCount,
  rebalanceCount,
  lastRebalance,
  rebalanceRows,
  viewWeightHistory,
  viewWeights,
  viewWeightOf,
  integratedProb,
  integratedSignal,
  spec,
  drawdownTicks,
  constraintResolved,
  METRIC_GUIDES,
} = defaultReport;
