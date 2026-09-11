// ---------------------------------------------------------------------------
// 백테스트 목데이터 · 전부 고정 시드에서 결정론적으로 생성된다.
// 화면에 뜨는 모든 수치는 이 파일의 series / pipeline 에서 파생되므로
// 새로고침해도 값이 바뀌지 않고, 카드·표·차트가 서로 어긋나지 않는다.
// ---------------------------------------------------------------------------

export const SEED = 20260908;

// 데이터 스냅샷 기준 시점. 화면의 모든 날짜(리포트 구간, 워크포워드 구간,
// 리밸런싱 일자)가 이 값 하나에서 파생되므로, 최신화할 때 여기만 고치면 된다.
//
// 실제 운용에서는 이 값이 BACKTEST_RUNS.data_snapshot_asof 에서 온다.
// 브라우저의 현재 시각에서 계산하지 않는 이유가 둘 있다. (1) 스냅샷 시점은
// 백테스트 실행 기록에 박히는 값이라 조회 시점에 따라 달라지면 안 된다.
// (2) 정적 프리렌더된 HTML 과 클라이언트가 서로 다른 날짜를 만들면
// 하이드레이션이 어긋난다.
export const AS_OF = "2026-09-11";
export const SNAPSHOT = AS_OF;
export const FEATURESET = "v0.1";
export const INITIAL = 10_000_000; // 시드머니 1,000만원
export const RF = 0.025; // 무위험수익률 연 2.5%

export const COST_MODEL = {
  fee: 0.00015, // 매매 수수료 0.015%
  tax: 0, // ETF 매도세 없음
  slippage: 0.0005, // 슬리피지 5bp
};

// --- 결정론적 난수 -----------------------------------------------------------
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
  const u = 1 - rnd();
  const v = rnd();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

// --- 자산곡선 (주간, 3년) ----------------------------------------------------
export type Point = { date: string; equity: number; buyHold: number };

const WEEKS = 157;
const YEARS = (WEEKS - 1) / 52;

// 목표 연환산 수치. 1배수 ETF 중장기 운용 기준으로 잡았다.
const TARGET = {
  strategy: { cagr: 0.066, vol: 0.091 },
  buyHold: { cagr: 0.031, vol: 0.152 },
};

// 원시 충격을 표준화한 뒤 목표 모수를 입히면, 시드가 무엇이든
// 실현 CAGR·변동성이 목표에 정확히 맞으면서도 경로는 결정론적으로 남는다.
function shockPath(rnd: () => number, n: number, cagr: number, vol: number): number[] {
  const raw = Array.from({ length: n }, () => gaussFrom(rnd));
  const mean = raw.reduce((a, b) => a + b, 0) / n;
  const sd = Math.sqrt(raw.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1));
  const muLog = (YEARS * Math.log(1 + cagr)) / n;
  const sdLog = vol / Math.sqrt(52);
  return raw.map((x) => muLog + sdLog * ((x - mean) / sd));
}

export const series: Point[] = (() => {
  const rnd = mulberry32(SEED);
  const eqLog = shockPath(rnd, WEEKS - 1, TARGET.strategy.cagr, TARGET.strategy.vol);
  const bhLog = shockPath(rnd, WEEKS - 1, TARGET.buyHold.cagr, TARGET.buyHold.vol);

  let eq = INITIAL;
  let bh = INITIAL;
  const out: Point[] = [];
  for (let i = 0; i < WEEKS; i++) {
    // AS_OF 에서 주 단위로 거슬러 올라간다 — 마지막 점이 곧 스냅샷 시점이다.
    const d = new Date(`${AS_OF}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() - (WEEKS - 1 - i) * 7);
    if (i > 0) {
      eq *= Math.exp(eqLog[i - 1]);
      bh *= Math.exp(bhLog[i - 1]);
    }
    out.push({
      date: d.toISOString().slice(0, 10),
      equity: Math.round(eq),
      buyHold: Math.round(bh),
    });
  }
  return out;
})();

export const PERIOD_START = series[0].date;
export const PERIOD_END = series[series.length - 1].date;

// --- 성과지표 ----------------------------------------------------------------
export type Metrics = {
  total: number;
  cagr: number;
  vol: number;
  sharpe: number;
  sortino: number;
  downside: number; // 하방편차(연환산) — 소르티노의 분모
  mdd: number;
  calmar: number;
  final: number;
};

function stdev(xs: number[]): number {
  if (xs.length < 2) return 0;
  const m = xs.reduce((a, b) => a + b, 0) / xs.length;
  const v = xs.reduce((a, b) => a + (b - m) ** 2, 0) / (xs.length - 1);
  return Math.sqrt(v);
}

export function computeMetrics(values: number[]): Metrics {
  const first = values[0];
  const last = values[values.length - 1];
  const rets: number[] = [];
  for (let i = 1; i < values.length; i++) rets.push(values[i] / values[i - 1] - 1);

  const years = (values.length - 1) / 52;
  const total = last / first - 1;
  const cagr = Math.pow(last / first, 1 / years) - 1;
  const vol = stdev(rets) * Math.sqrt(52);
  const down = stdev(rets.map((r) => Math.min(r, 0))) * Math.sqrt(52);

  let peak = first;
  let mdd = 0;
  for (const v of values) {
    if (v > peak) peak = v;
    const dd = v / peak - 1;
    if (dd < mdd) mdd = dd;
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

export const strategy = computeMetrics(series.map((p) => p.equity));
export const buyHold = computeMetrics(series.map((p) => p.buyHold));
export const excess = strategy.total - buyHold.total;

// --- 드로다운 ----------------------------------------------------------------
export const drawdownSeries = (() => {
  let peak = series[0].equity;
  return series.map((p) => {
    if (p.equity > peak) peak = p.equity;
    return { date: p.date, drawdown: Number(((p.equity / peak - 1) * 100).toFixed(2)) };
  });
})();

// --- 월별 수익률 -------------------------------------------------------------
// 기준 시점이 그 달의 마지막 날이 아니면 마지막 달은 아직 진행 중이다.
// 완결된 달과 같은 막대로 그리면 짧은 기간이 한 달처럼 보여 오해를 부르므로
// partial 로 표시하고, 최고월/최저월 집계에서도 제외한다.
const AS_OF_MONTH_END = (() => {
  const d = new Date(`${AS_OF}T00:00:00Z`);
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).toISOString().slice(0, 10);
})();

export const LAST_MONTH_PARTIAL = AS_OF !== AS_OF_MONTH_END;

export type MonthlyReturn = { month: string; ret: number; partial: boolean };

export const monthlyReturns: MonthlyReturn[] = (() => {
  const byMonth = new Map<string, number>();
  for (const p of series) byMonth.set(p.date.slice(0, 7), p.equity);
  const months = [...byMonth.keys()].sort();
  const out: MonthlyReturn[] = [];
  for (let i = 1; i < months.length; i++) {
    const prev = byMonth.get(months[i - 1])!;
    const cur = byMonth.get(months[i])!;
    out.push({
      month: months[i],
      ret: Number(((cur / prev - 1) * 100).toFixed(2)),
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
  buyHoldRet: number;
  excess: number;
  mdd: number;
  buyHoldMdd: number;
  sharpe: number;
};

function shiftMonths(iso: string, months: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCMonth(d.getUTCMonth() + months);
  return d.toISOString().slice(0, 10);
}

// 구간을 날짜가 아니라 시리즈 인덱스로 자른다. 156개 구간을 6등분하면 정확히
// 26주씩 떨어지므로, 구간들이 끊김 없이 이어지고 구간 수익률의 기하곱이 전체
// 수익률과 정확히 일치한다. 날짜로 자르면 경계에서 한 주가 빠져 어긋난다.
export const FOLD_COUNT = 6;
const FOLD_WEEKS = (WEEKS - 1) / FOLD_COUNT;
const TRAIN_MONTHS = 12;

export const folds: Fold[] = (() => {
  const out: Fold[] = [];
  for (let k = 0; k < FOLD_COUNT; k++) {
    const from = k * FOLD_WEEKS;
    const to = (k + 1) * FOLD_WEEKS;
    const slice = series.slice(from, to + 1);
    if (slice.length < 3) continue;
    const s = computeMetrics(slice.map((p) => p.equity));
    const b = computeMetrics(slice.map((p) => p.buyHold));
    const testFrom = series[from].date;
    out.push({
      id: `WF-${k + 1}`,
      // 학습 구간은 검증 구간 직전 12개월이다. 리포트 구간보다 앞설 수 있는데,
      // 그건 학습 데이터가 리포트 시작 이전에도 존재하기 때문이고 성과 집계에는
      // 들어가지 않는다.
      trainFrom: shiftMonths(testFrom, -TRAIN_MONTHS),
      trainTo: testFrom,
      testFrom,
      testTo: series[to].date,
      ret: s.total,
      buyHoldRet: b.total,
      excess: s.total - b.total,
      mdd: s.mdd,
      buyHoldMdd: b.mdd,
      sharpe: s.sharpe,
    });
  }
  return out;
})();

// --- 시스템 하드캡 (db/seeds/01_hardcap_v0_1.sql, hardcap_versions v0.1) -----
// Validator 4단계가 사용자 요청값을 이 값으로 클램프한다.
// 주의: 이 수치는 시드 파일에 "팀 확정 전 보수적 기본값 / TODO" 로 적혀 있다.
export const HARDCAP = {
  version: "v0.1",
  maxWeightPerAsset: 0.3,
  cashMin: 0.05,
  maxLossPerTrade: 0.05,
  maxDrawdown: 0.25,
  minIntervalDays: 5,
  leverageAllowed: false,
};

// --- 사용자 성향 · 사전 허용범위(1단) ----------------------------------------
// db/seeds/02_preset_v0_1.sql — asset_bound_presets(risk_level=3 위험중립형)와
// risk_profile_defaults(cash_min 0.10) 의 값을 그대로 옮겼다.
export const PROFILE = {
  label: "위험중립형",
  riskLevel: 3,
  presetVersion: "v0.1",
  cashMin: 0.1,
  gradeCap: { G1: 0.0, G2: 0.1, G3: 0.25, G4: 0.35, G5: 0.5, G6: 1.0 } as Record<string, number>,
  source: "허용범위 프리셋 v0.1 (asset_bound_presets, risk_level=3)",
};

// --- 2계층 자산 그룹과 그룹캡 -------------------------------------------------
// db/seeds/03_asset_groups.sql — group_caps(preset_version='v0.1').
// 레벨1은 risk_level=3(위험중립형) 행, 레벨2는 risk_level=0(성향 무관 고정) 행.
export const GROUP_LABEL: Record<string, string> = {
  EQUITY: "주식계",
  BOND: "채권계",
  COMMODITY: "원자재계",
  SECTOR_SEMICONDUCTOR: "반도체",
  SECTOR_OTHER: "기타",
  COUNTRY_KR: "한국",
  COUNTRY_US: "미국",
  COUNTRY_OTHER: "기타",
};

export const GROUP_CAPS: Record<string, number> = {
  EQUITY: 0.6,
  BOND: 0.5,
  COMMODITY: 0.15,
  SECTOR_SEMICONDUCTOR: 0.3,
  SECTOR_OTHER: 0.3,
  COUNTRY_KR: 0.5,
  COUNTRY_US: 0.5,
  COUNTRY_OTHER: 0.5,
};

// SECTOR_OTHER(기타)에는 캡을 적용하지 않는다. 시드의 섹터 목록에는 산업 섹터
// 6개와 "기타" 하나뿐이라, 시장대표·국채·원자재 ETF가 전부 기타 한 바구니로
// 들어간다. 여기에 30% 캡을 그대로 걸면 이 유니버스의 기타 합이 60%를 넘어
// 항상 캡에 걸리고 잔여가 전부 현금으로 빠진다.
// 팀 확정이 필요한 항목이라 적용하지 않고 화면에 사유를 표시한다.
export const SECTOR_OTHER_CAP_APPLIED = false;

// --- 유니버스 · Spec 비중범위(2단) -------------------------------------------
// weight_max_raw 는 LLM 원출력, weight_max 는 허용범위 프리셋과 하드캡으로
// 보정된 확정값이다 (backend/app/contracts/spec.py UniverseItem 독스트링,
// spec_universe.weight_max_raw / weight_max).
export type Asset = {
  ticker: string;
  name: string;
  grade: string;
  assetGroup: "EQUITY" | "BOND" | "COMMODITY";
  sectorGroup: string;
  countryGroup: string;
  minRaw: number;
  maxRaw: number;
  signal: number; // 종목 신호 s ∈ [-1, +1]
};

const universeRaw: Asset[] = [
  { ticker: "069500", name: "KODEX 200", grade: "G4", assetGroup: "EQUITY", sectorGroup: "SECTOR_OTHER", countryGroup: "COUNTRY_KR", minRaw: 0.05, maxRaw: 0.35, signal: 0.35 },
  { ticker: "232080", name: "TIGER 코스닥150", grade: "G3", assetGroup: "EQUITY", sectorGroup: "SECTOR_OTHER", countryGroup: "COUNTRY_KR", minRaw: 0.0, maxRaw: 0.2, signal: -0.2 },
  { ticker: "091160", name: "KODEX 반도체", grade: "G3", assetGroup: "EQUITY", sectorGroup: "SECTOR_SEMICONDUCTOR", countryGroup: "COUNTRY_KR", minRaw: 0.05, maxRaw: 0.25, signal: 0.62 },
  { ticker: "360750", name: "TIGER 미국S&P500", grade: "G4", assetGroup: "EQUITY", sectorGroup: "SECTOR_OTHER", countryGroup: "COUNTRY_US", minRaw: 0.05, maxRaw: 0.3, signal: 0.44 },
  { ticker: "148070", name: "KOSEF 국고채10년", grade: "G5", assetGroup: "BOND", sectorGroup: "SECTOR_OTHER", countryGroup: "COUNTRY_KR", minRaw: 0.1, maxRaw: 0.4, signal: -0.1 },
  { ticker: "132030", name: "KODEX 골드선물(H)", grade: "G4", assetGroup: "COMMODITY", sectorGroup: "SECTOR_OTHER", countryGroup: "COUNTRY_OTHER", minRaw: 0.0, maxRaw: 0.15, signal: 0.05 },
];

export type Bound = { min: number; max: number; clampedBy: string | null };

// Validator 2단(허용범위 프리셋) + 4단(하드캡 클램프)을 순서대로 적용한다.
export const bounds: Record<string, Bound> = Object.fromEntries(
  universeRaw.map((a) => {
    const presetCap = PROFILE.gradeCap[a.grade];
    const cap = Math.min(a.maxRaw, presetCap, HARDCAP.maxWeightPerAsset);
    const by =
      cap >= a.maxRaw - 1e-9
        ? null
        : cap === HARDCAP.maxWeightPerAsset && HARDCAP.maxWeightPerAsset <= presetCap
          ? "하드캡"
          : "프리셋";
    return [a.ticker, { min: a.minRaw, max: cap, clampedBy: by }];
  }),
);

export const universe = universeRaw;

export const groupOf = (a: Asset, level: "asset" | "sector" | "country") =>
  level === "asset" ? a.assetGroup : level === "sector" ? a.sectorGroup : a.countryGroup;

// --- 신호 → 비중 매핑 파이프라인 (통제 계층 재현) ----------------------------
export type WeightRow = {
  asset: Asset;
  bound: Bound;
  raw: number; // 1 선형 매핑
  normalized: number; // 2~4 정규화 + 클램프
  afterAssetCap: number; // 5a 상위 자산군 캡
  final: number; // 5b 하위 국가·섹터 캡
  capped: string | null;
};

export type CapLog = { stage: string; groupId: string; label: string; before: number; cap: number; factor: number };

const pipeline = (() => {
  const investable = 1 - PROFILE.cashMin;

  // 1 선형 매핑 w = min + (s+1)/2 * (max - min). max 는 하드캡까지 클램프된 확정값.
  const raw = universe.map((a) => {
    const b = bounds[a.ticker];
    return b.min + ((a.signal + 1) / 2) * (b.max - b.min);
  });

  // 2~4 정규화 + 클램프 (최대 3회)
  let w = [...raw];
  for (let it = 0; it < 3; it++) {
    const sum = w.reduce((x, y) => x + y, 0);
    if (sum === 0) break;
    w = w.map((v) => (v * investable) / sum);
    w = w.map((v, i) => Math.min(Math.max(v, bounds[universe[i].ticker].min), bounds[universe[i].ticker].max));
  }
  const normalized = [...w];

  const logs: CapLog[] = [];
  const cappedBy: (string | null)[] = universe.map(() => null);
  const current = [...w];

  const applyLevel = (level: "asset" | "sector" | "country", stage: string) => {
    const groups = [...new Set(universe.map((a) => groupOf(a, level)))];
    for (const g of groups) {
      if (level === "sector" && g === "SECTOR_OTHER" && !SECTOR_OTHER_CAP_APPLIED) continue;
      const cap = GROUP_CAPS[g];
      if (cap === undefined) continue;
      const idx = universe.map((a, i) => (groupOf(a, level) === g ? i : -1)).filter((i) => i >= 0);
      const before = idx.reduce((s, i) => s + current[i], 0);
      if (before > cap + 1e-9) {
        const factor = cap / before;
        idx.forEach((i) => {
          current[i] *= factor;
          cappedBy[i] = cappedBy[i] ? `${cappedBy[i]}·${stage}` : stage;
        });
        logs.push({ stage, groupId: g, label: GROUP_LABEL[g] ?? g, before, cap, factor });
      }
    }
  };

  // 상위(자산군)를 먼저, 하위(국가 → 섹터)를 나중에 적용한다.
  applyLevel("asset", "자산군");
  const afterAsset = [...current];
  applyLevel("country", "국가");
  applyLevel("sector", "섹터");

  const rows: WeightRow[] = universe.map((a, i) => ({
    asset: a,
    bound: bounds[a.ticker],
    raw: raw[i],
    normalized: normalized[i],
    afterAssetCap: afterAsset[i],
    final: current[i],
    capped: cappedBy[i],
  }));

  return { rows, logs, cash: 1 - current.reduce((x, y) => x + y, 0) };
})();

export const weightRows = pipeline.rows;
export const capLogs = pipeline.logs;
export const targetCash = pipeline.cash;

// --- 3관점 · Hedge 가중치 갱신 ----------------------------------------------
export const VIEW_META = [
  { key: "market", label: "시장 분석", basis: "momentum_20d · rsi_14 · volume_ratio_20d", kind: "지도학습 (LightGBM + SHAP)", meanLoss: 0.185, prob: 0.68 },
  { key: "sentiment", label: "감성 분석", basis: "대형주 · 중소형주 뉴스 48시간", kind: "사전학습 언어모델 + 감성 렌즈", meanLoss: 0.243, prob: 0.55 },
  { key: "temperature", label: "시장 온도", basis: "KOSPI 200일 이동평균 · VKOSPI", kind: "규칙 및 통계 모형", meanLoss: 0.211, prob: 0.61 },
] as const;

export const ETA = 0.5;
export const W_FLOOR = 0.1;
export const EVAL_WINDOW = 60;

export type WeightHistory = { date: string; market: number; sentiment: number; temperature: number };

// 정규화 후 하한을 적용한다. 단순 클립 뒤 재정규화하면 하한이 다시 깨지므로,
// 하한에 걸린 관점은 고정하고 남은 몫만 나머지 관점에 비례 배분한다.
function normalizeWithFloor(raw: number[], floor: number): number[] {
  const w = [...raw];
  for (let pass = 0; pass < w.length; pass++) {
    const sum = w.reduce((a, b) => a + b, 0);
    const scaled = w.map((x) => x / sum);
    const pinned = scaled.map((x) => x <= floor);
    if (!pinned.some(Boolean)) return scaled;
    const freeBudget = 1 - floor * pinned.filter(Boolean).length;
    const freeSum = scaled.reduce((a, x, i) => (pinned[i] ? a : a + x), 0);
    for (let i = 0; i < w.length; i++) {
      w[i] = pinned[i] ? floor : (scaled[i] / freeSum) * freeBudget;
    }
  }
  return w;
}

export const viewWeightHistory: WeightHistory[] = (() => {
  const rnd = mulberry32(SEED + 7);
  // 손실은 Brier score. 전일까지 확정된 성과만, 평가 윈도우 길이만큼만 반영한다.
  const lossLog: number[][] = [];
  const out: WeightHistory[] = [];

  for (const p of series) {
    const window = lossLog.slice(-EVAL_WINDOW);
    const cumulative = VIEW_META.map((_, k) => window.reduce((a, row) => a + row[k], 0));
    const minLoss = Math.min(...cumulative); // 언더플로 방지용 상수 이동. 정규화 결과는 동일하다.
    const w = normalizeWithFloor(
      cumulative.map((L) => Math.exp(-ETA * (L - minLoss))),
      W_FLOOR,
    );
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

// --- 리밸런싱 이력 -----------------------------------------------------------
export type RebalanceRow = {
  ticker: string;
  name: string;
  before: number;
  target: number;
  amount: number;
};

export const lastRebalance = {
  // 기준 시점이 속한 달의 첫 영업일 (목데이터라 1일로 단순화)
  date: `${AS_OF.slice(0, 7)}-01`,
  trigger: "calendar · monthly · first_trading_day",
  minInterval: 20,
};

export const rebalanceRows: RebalanceRow[] = (() => {
  const rnd = mulberry32(SEED + 13);
  return weightRows.map((r) => {
    const before = Math.max(0, r.final + (rnd() - 0.5) * 0.05);
    return {
      ticker: r.asset.ticker,
      name: r.asset.name,
      before,
      target: r.final,
      amount: Math.round(((r.final - before) * strategy.final) / 10) * 10,
    };
  });
})();

// --- 전략 Spec ---------------------------------------------------------------
// backend/app/contracts/spec.py 의 SpecV0_1 형태를 그대로 따른다.
// constraint 는 사용자 요청값이고, 하드캡은 Spec 스키마에 자리가 없다 —
// 아래 constraintResolved 가 Validator 4단계 클램프 결과다.
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
  rebalance: {
    trigger: { type: "calendar", freq: "monthly", day: 1 },
    min_interval_days: 20,
  },
  signal_rules: {
    market_analysis: { indicators: ["momentum_20d", "rsi_14", "volume_ratio_20d"] },
    sentiment: { target_sectors: ["대형주", "중소형주"], lookback_hours: 48 },
    market_temperature: { trend_index: "KOSPI", trend_ma_window: 200, volatility_index: "VKOSPI" },
  },
  constraint: {
    max_weight_per_asset: 0.35,
    min_weight_per_asset: 0.0,
    cash_min: 0.1,
    max_loss_per_trade: 0.05,
    max_drawdown: 0.2,
  },
};

// Validator 4단계 하드캡 클램프 결과. requested 가 하드캡을 넘으면 잘린다.
export type ClampRow = { field: string; requested: number; hardcap: number; resolved: number; clamped: boolean };

export const constraintResolved: ClampRow[] = [
  { field: "max_weight_per_asset", requested: spec.constraint.max_weight_per_asset, hardcap: HARDCAP.maxWeightPerAsset, resolved: Math.min(spec.constraint.max_weight_per_asset, HARDCAP.maxWeightPerAsset), clamped: spec.constraint.max_weight_per_asset > HARDCAP.maxWeightPerAsset },
  { field: "cash_min", requested: spec.constraint.cash_min, hardcap: HARDCAP.cashMin, resolved: Math.max(spec.constraint.cash_min, HARDCAP.cashMin), clamped: spec.constraint.cash_min < HARDCAP.cashMin },
  { field: "max_loss_per_trade", requested: spec.constraint.max_loss_per_trade, hardcap: HARDCAP.maxLossPerTrade, resolved: Math.min(spec.constraint.max_loss_per_trade, HARDCAP.maxLossPerTrade), clamped: spec.constraint.max_loss_per_trade > HARDCAP.maxLossPerTrade },
  { field: "max_drawdown", requested: spec.constraint.max_drawdown, hardcap: HARDCAP.maxDrawdown, resolved: Math.min(spec.constraint.max_drawdown, HARDCAP.maxDrawdown), clamped: spec.constraint.max_drawdown > HARDCAP.maxDrawdown },
  { field: "min_interval_days", requested: spec.rebalance.min_interval_days, hardcap: HARDCAP.minIntervalDays, resolved: Math.max(spec.rebalance.min_interval_days, HARDCAP.minIntervalDays), clamped: spec.rebalance.min_interval_days < HARDCAP.minIntervalDays },
];

// --- 지표 해설 ---------------------------------------------------------------
// 숫자만 보여주면 일반투자자는 0.45 가 좋은 건지 나쁜 건지 알 수 없다.
// 정의 · 실제 값을 넣은 계산식 · 읽는 법을 같이 준다.
//
// 판정 문구는 하드코딩하지 않고 실제 값에서 만든다. 수치가 바뀌면 문장도
// 따라 바뀌어야 하는데, 고정 문장을 두면 조용히 어긋난다.

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
const n2 = (n: number) => n.toFixed(2); // 포맷터 섹션이 아래에 있어 num 을 아직 못 쓴다

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
    caveat:
      "오르는 쪽 흔들림도 위험으로 함께 벌점을 매깁니다. 크게 오른 달이 많아도 지수는 내려갈 수 있어서, 소르티노지수와 같이 봐야 합니다.",
  },
  {
    id: "sortino",
    title: "소르티노지수",
    english: "Sortino Ratio",
    summary: "샤프지수에서 위험을 내려간 쪽 흔들림만으로 다시 계산한 값입니다.",
    formula: `(연환산 수익률 ${p1(strategy.cagr)} − 무위험 ${p1(RF)}) ÷ 하방편차 ${p1(strategy.downside)} = ${n2(strategy.sortino)}`,
    reading: `높을수록 좋습니다. ${sortinoBand}`,
    caveat:
      "손실 구간이 적을수록 분모가 작아져 값이 급격히 커집니다. 관측 기간이 짧으면 과장되기 쉬우니 절대값보다 샤프지수와의 차이를 보는 편이 낫습니다.",
  },
  {
    id: "calmar",
    title: "칼마지수",
    english: "Calmar Ratio",
    summary: "가장 크게 물렸던 낙폭 1만큼당 1년에 얼마를 벌었는지를 나타냅니다.",
    formula: `연환산 수익률 ${p1(strategy.cagr)} ÷ 최대낙폭 ${p1(Math.abs(strategy.mdd))} = ${n2(strategy.calmar)}`,
    reading: `높을수록 좋습니다. ${calmarBand}`,
    caveat:
      "최대낙폭 한 지점에만 의존합니다. 그 한 번이 우연이었는지 반복되는 성질인지는 이 지수만으로 알 수 없어서, 워크포워드 구간별 낙폭을 같이 봐야 합니다.",
  },
];

export const guideById = (id: string) => METRIC_GUIDES.find((g) => g.id === id);

// --- 포맷터 ------------------------------------------------------------------
export const won = (n: number) =>
  new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 }).format(n);

export const pct = (n: number, d = 2) => `${n >= 0 ? "+" : "−"}${Math.abs(n * 100).toFixed(d)}%`;
export const pctPlain = (n: number, d = 1) => `${(n * 100).toFixed(d)}%`;
export const pp = (n: number, d = 1) => `${n >= 0 ? "+" : "−"}${Math.abs(n * 100).toFixed(d)}%p`;
export const num = (n: number, d = 2) => n.toFixed(d);
export const ym = (iso: string) => iso.slice(2, 7).replace("-", ".");
