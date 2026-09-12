// ---------------------------------------------------------------------------
// 화면이 읽는 백테스트 결과.
//
// 여기 있는 값은 합성이 아니다. data/backtest-result.json 은
// scripts/run-backtest.ts 가 KRX 실제 종가로 돌린 결과이고, 이 모듈은 그걸
// 읽어 지표를 계산해 내보낸다. 수치를 화면에 하드코딩하지 않는다.
//
// 다시 돌리려면:
//   node --experimental-strip-types scripts/run-backtest.ts
//   RISK_LEVEL=3 node --experimental-strip-types scripts/run-backtest.ts  (성향 변경)
//
// 아직 목데이터인 것: 3관점 가중치(Hedge). M3 미구현이라 결정론적 모의값이다.
// ---------------------------------------------------------------------------

import result from "@/data/backtest-result.json";

export const AS_OF = result.as_of;
export const SNAPSHOT = AS_OF;
export const PERIOD_START = result.period_start;
export const PERIOD_END = AS_OF;
export const INITIAL = result.initial;
export const RF = 0.025; // 무위험수익률 연 2.5%
export const FEATURESET = "v0.1";

export const DATA_SOURCE = result.data_source;
export const PRICE_FIELD = result.price_field;
export const PROFILE_LABEL = result.profile.label;
export const RISK_LEVEL = result.profile.risk_level;

export const COST_MODEL = {
  fee: result.costs.fee,
  tax: result.costs.tax,
  slippage: result.costs.slippage,
};

export const HARDCAP = {
  version: "v0.1",
  maxWeightPerAsset: result.hardcap_max_per_asset,
  cashMin: 0.05,
  maxLossPerTrade: 0.05,
  maxDrawdown: 0.25,
  minIntervalDays: 5,
  leverageAllowed: false,
};

// db/seeds/02_preset_v0_1.sql · asset_bound_presets
const PRESET_GRADE_CAP: Record<number, Record<string, number>> = {
  1: { G1: 0, G2: 0, G3: 0, G4: 0.1, G5: 0.3, G6: 1 },
  2: { G1: 0, G2: 0, G3: 0.1, G4: 0.25, G5: 0.4, G6: 1 },
  3: { G1: 0, G2: 0.1, G3: 0.25, G4: 0.35, G5: 0.5, G6: 1 },
  4: { G1: 0.1, G2: 0.25, G3: 0.35, G4: 0.4, G5: 0.6, G6: 1 },
  5: { G1: 0.25, G2: 0.35, G3: 0.4, G4: 0.5, G5: 0.7, G6: 1 },
};

export const PROFILE = {
  label: result.profile.label,
  gradeCap: PRESET_GRADE_CAP[result.profile.risk_level],
  riskLevel: result.profile.risk_level,
  presetVersion: "v0.1",
  cashMin: result.profile.cash_min,
  source: `허용범위 프리셋 v0.1 (asset_bound_presets, risk_level=${result.profile.risk_level})`,
};

export const GROUP_CAPS: Record<string, number> = result.caps;

export const GROUP_LABEL: Record<string, string> = {
  EQUITY: "주식계",
  BOND: "채권계",
  COMMODITY: "원자재계",
  SECTOR_SEMICONDUCTOR: "반도체",
  SECTOR_BATTERY: "2차전지",
  SECTOR_BIOHEALTH: "바이오·헬스케어",
  SECTOR_FINANCE: "금융",
  SECTOR_INTERNET_PLATFORM: "인터넷·플랫폼",
  SECTOR_CONSUMER: "소비재",
  SECTOR_OTHER: "기타",
  COUNTRY_KR: "한국",
  COUNTRY_US: "미국",
  COUNTRY_OTHER: "기타",
};

// --- 자산곡선 ----------------------------------------------------------------
// strategy = 3관점 신호 사용, control = 같은 제약·신호 없음, market = KODEX 200 매수후보유
export type Point = { date: string; strategy: number; control: number; market: number };
export const series: Point[] = result.series;

// --- 성과지표 ----------------------------------------------------------------
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

const YEARS =
  (new Date(`${PERIOD_END}T00:00:00Z`).getTime() - new Date(`${PERIOD_START}T00:00:00Z`).getTime()) /
  (365.25 * 864e5);

export const strategy = computeMetrics(series.map((p) => p.strategy), YEARS);
export const control = computeMetrics(series.map((p) => p.control), YEARS);
export const market = computeMetrics(series.map((p) => p.market), YEARS);

/** 신호가 더한 값. 전략 − 대조군. 이 시스템이 증명해야 하는 숫자다. */
export const signalAlpha = strategy.total - control.total;
export const signalAlphaAnnual = strategy.cagr - control.cagr;
/** 시장 대비. 위험 수준이 달라 우열이 아니라 맥락으로 본다. */
export const vsMarket = strategy.total - market.total;

// --- 낙폭 --------------------------------------------------------------------
function drawdownOf(values: number[]): number[] {
  let peak = values[0];
  return values.map((v) => {
    if (v > peak) peak = v;
    return Number(((v / peak - 1) * 100).toFixed(2));
  });
}
const ddStrategy = drawdownOf(series.map((p) => p.strategy));
const ddMarket = drawdownOf(series.map((p) => p.market));
export const drawdownSeries = series.map((p, i) => ({
  date: p.date,
  drawdown: ddStrategy[i],
  marketDrawdown: ddMarket[i],
}));

// --- 월별 수익률 -------------------------------------------------------------
const AS_OF_MONTH_END = (() => {
  const d = new Date(`${AS_OF}T00:00:00Z`);
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).toISOString().slice(0, 10);
})();
export const LAST_MONTH_PARTIAL = AS_OF !== AS_OF_MONTH_END;

export type MonthlyReturn = { month: string; ret: number; partial: boolean };

export const monthlyReturns: MonthlyReturn[] = (() => {
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
export const bestMonth = completeMonths.reduce((a, b) => (b.ret > a.ret ? b : a));
export const worstMonth = completeMonths.reduce((a, b) => (b.ret < a.ret ? b : a));

// --- 워크포워드 구간 ---------------------------------------------------------
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

function shiftMonths(iso: string, months: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCMonth(d.getUTCMonth() + months);
  return d.toISOString().slice(0, 10);
}

export const FOLD_COUNT = 6;

export const folds: Fold[] = (() => {
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

// --- 유니버스와 목표 비중 ----------------------------------------------------
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

const lastDecision = result.decisions[result.decisions.length - 1];
const lastControl = result.control_decisions[result.control_decisions.length - 1];

export const universe: Asset[] = result.universe.map((u) => ({
  ticker: u.ticker,
  name: u.name,
  grade: u.grade,
  assetGroup: u.asset_group,
  sectorGroup: u.sector_group,
  countryGroup: u.country_group,
  minRaw: u.weight_min_raw,
  maxRaw: u.weight_max_raw,
  signal: (lastDecision.signals as Record<string, number>)[u.ticker] ?? 0,
}));

export type Bound = { min: number; max: number; clampedBy: string | null };

export const bounds: Record<string, Bound> = Object.fromEntries(
  universe.map((a) => {
    const presetCap = PROFILE.gradeCap[a.grade] ?? 1;
    const cap = Math.min(a.maxRaw, presetCap, HARDCAP.maxWeightPerAsset);
    const by = cap < a.maxRaw - 1e-9 ? (presetCap <= HARDCAP.maxWeightPerAsset ? "프리셋" : "하드캡") : null;
    return [a.ticker, { min: Math.min(a.minRaw, cap), max: cap, clampedBy: by }];
  }),
);

export type WeightRow = {
  asset: Asset;
  bound: Bound;
  normalized: number;
  controlWeight: number;
  final: number;
  capped: string | null;
};

const cappedBy = new Map<string, string[]>();
for (const app of lastDecision.capApplications) {
  for (const a of universe) {
    const g = app.stage === "자산군" ? a.assetGroup : app.stage === "국가" ? a.countryGroup : a.sectorGroup;
    if (g === app.groupId) cappedBy.set(a.ticker, [...(cappedBy.get(a.ticker) ?? []), app.stage]);
  }
}

export const weightRows: WeightRow[] = universe.map((a) => ({
  asset: a,
  bound: bounds[a.ticker],
  normalized: (lastDecision.mapped as Record<string, number>)[a.ticker] ?? 0,
  controlWeight: (lastControl.target as Record<string, number>)[a.ticker] ?? 0,
  final: (lastDecision.target as Record<string, number>)[a.ticker] ?? 0,
  capped: cappedBy.get(a.ticker)?.join("·") ?? null,
}));

export const targetCash = lastDecision.cash;

export type CapLog = { stage: string; groupId: string; label: string; before: number; cap: number; factor: number };

export const capLogs: CapLog[] = lastDecision.capApplications.map((a) => ({
  stage: a.stage === "자산군" ? "상위 · 자산군" : `하위 · ${a.stage}`,
  groupId: a.groupId,
  label: GROUP_LABEL[a.groupId] ?? a.groupId,
  before: a.sumBefore,
  cap: a.cap,
  factor: a.factor,
}));

/** 전체 기간 동안 그룹캡이 몇 번 걸렸는지 */
export const capApplicationCount = result.decisions.reduce((n, d) => n + d.capApplications.length, 0);
export const rebalanceCount = result.decisions.length;
export const lastRebalance = {
  date: lastDecision.date,
  trigger: "calendar · monthly · first_trading_day",
  minInterval: 20,
  turnover: lastDecision.turnover,
  cost: lastDecision.cost,
};

// --- 리밸런싱 이력 -----------------------------------------------------------
export type RebalanceRow = { ticker: string; name: string; before: number; target: number; amount: number };

const prevDecision = result.decisions[result.decisions.length - 2] ?? lastDecision;

export const rebalanceRows: RebalanceRow[] = universe.map((a) => {
  const before = (prevDecision.target as Record<string, number>)[a.ticker] ?? 0;
  const target = (lastDecision.target as Record<string, number>)[a.ticker] ?? 0;
  return {
    ticker: a.ticker,
    name: a.name,
    before,
    target,
    amount: Math.round(((target - before) * strategy.final) / 10) * 10,
  };
});

// --- 3관점 (아직 목데이터 — M3 미구현) ---------------------------------------
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

export const SEED = 20260908;
export const VIEW_META = [
  { key: "market", label: "시장 분석", basis: "momentum_20d · rsi_14 · volume_ratio_20d", kind: "지도학습 (LightGBM + SHAP)", meanLoss: 0.185, prob: 0.68 },
  { key: "sentiment", label: "감성 분석", basis: "대형주 · 중소형주 뉴스 48시간", kind: "사전학습 언어모델 + 감성 렌즈", meanLoss: 0.243, prob: 0.55 },
  { key: "temperature", label: "시장 온도", basis: "KOSPI 200일 이동평균 · VKOSPI", kind: "규칙 및 통계 모형", meanLoss: 0.211, prob: 0.61 },
] as const;

export const ETA = 0.5;
export const W_FLOOR = 0.1;
export const EVAL_WINDOW = 60;

export type WeightHistory = { date: string; market: number; sentiment: number; temperature: number };

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

export const viewWeightHistory: WeightHistory[] = (() => {
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

export const viewWeights = viewWeightHistory[viewWeightHistory.length - 1];
export const viewWeightOf: Record<string, number> = {
  market: viewWeights.market,
  sentiment: viewWeights.sentiment,
  temperature: viewWeights.temperature,
};
export const integratedProb =
  viewWeights.market * VIEW_META[0].prob +
  viewWeights.sentiment * VIEW_META[1].prob +
  viewWeights.temperature * VIEW_META[2].prob;
export const integratedSignal = 2 * integratedProb - 1;

// --- 전략 Spec ---------------------------------------------------------------
export const spec = {
  spec_id: "STR-001",
  spec_version: "0.1",
  user_id: 1,
  name: "코스피 코어 + 반도체 위성",
  created_at: "2026-09-08T09:00:00+09:00",
  universe: universe.map((a) => ({
    ticker: a.ticker,
    name: a.name,
    weight_min: bounds[a.ticker].min,
    weight_max: bounds[a.ticker].max,
  })),
  rebalance: { trigger: { type: "calendar", freq: "monthly", day: 1 }, min_interval_days: 20 },
  signal_rules: {
    market_analysis: { indicators: ["momentum_20d", "rsi_14", "volume_ratio_20d"] },
    sentiment: { target_sectors: ["대형주", "중소형주"], lookback_hours: 48 },
    market_temperature: { trend_index: "KOSPI", trend_ma_window: 200, volatility_index: "VKOSPI" },
  },
  constraint: {
    max_weight_per_asset: 0.35,
    min_weight_per_asset: 0.0,
    cash_min: PROFILE.cashMin,
    max_loss_per_trade: 0.05,
    max_drawdown: 0.2,
  },
};

export type ClampRow = { field: string; requested: number; hardcap: number; resolved: number; clamped: boolean };

export const constraintResolved: ClampRow[] = [
  { field: "max_weight_per_asset", requested: spec.constraint.max_weight_per_asset, hardcap: HARDCAP.maxWeightPerAsset, resolved: Math.min(spec.constraint.max_weight_per_asset, HARDCAP.maxWeightPerAsset), clamped: spec.constraint.max_weight_per_asset > HARDCAP.maxWeightPerAsset },
  { field: "cash_min", requested: spec.constraint.cash_min, hardcap: HARDCAP.cashMin, resolved: Math.max(spec.constraint.cash_min, HARDCAP.cashMin), clamped: spec.constraint.cash_min < HARDCAP.cashMin },
  { field: "max_loss_per_trade", requested: spec.constraint.max_loss_per_trade, hardcap: HARDCAP.maxLossPerTrade, resolved: Math.min(spec.constraint.max_loss_per_trade, HARDCAP.maxLossPerTrade), clamped: spec.constraint.max_loss_per_trade > HARDCAP.maxLossPerTrade },
  { field: "max_drawdown", requested: spec.constraint.max_drawdown, hardcap: HARDCAP.maxDrawdown, resolved: Math.min(spec.constraint.max_drawdown, HARDCAP.maxDrawdown), clamped: spec.constraint.max_drawdown > HARDCAP.maxDrawdown },
  { field: "min_interval_days", requested: spec.rebalance.min_interval_days, hardcap: HARDCAP.minIntervalDays, resolved: Math.max(spec.rebalance.min_interval_days, HARDCAP.minIntervalDays), clamped: spec.rebalance.min_interval_days < HARDCAP.minIntervalDays },
];

// --- 지표 해설 ---------------------------------------------------------------
export type MetricGuide = {
  id: string;
  title: string;
  english: string;
  summary: string;
  formula: string;
  reading: string;
  caveat: string;
};

const p1 = (n: number) => `${(n * 100).toFixed(2)}%`;
const n2 = (n: number) => n.toFixed(2);

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

export const METRIC_GUIDES: MetricGuide[] = [
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

// --- 포맷터 ------------------------------------------------------------------
export const won = (n: number) =>
  new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 }).format(n);
export const pct = (n: number, d = 2) => `${n >= 0 ? "+" : "−"}${Math.abs(n * 100).toFixed(d)}%`;
export const pctPlain = (n: number, d = 1) => `${(n * 100).toFixed(d)}%`;
export const pp = (n: number, d = 1) => `${n >= 0 ? "+" : "−"}${Math.abs(n * 100).toFixed(d)}%p`;
export const num = (n: number, d = 2) => n.toFixed(d);
export const ym = (iso: string) => iso.slice(2, 7).replace("-", ".");
