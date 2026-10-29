// 시점 주입 백테스트 러너.
//
// 기획서 아이디어 7: 같은 코드에 과거 시점을 주입하면 백테스트, 오늘을 주입하면
// 모의운용이 된다. 그래서 이 모듈은 "현재"를 모른다 — 모든 판단이 인자로 받은
// 시점 t 와 t 이전 데이터만 본다.
//
// 파이썬 러너(vectorbt)가 붙으면 이 구현이 대조군이 된다. 같은 입력에 같은
// 주문이 나오는지 맞춰보면 재현성 주장이 검증된다.

import {
  resolveBounds,
  type Bound,
  type Caps,
  type Grade,
  type Profile,
} from "./policy.ts";

export type { Bound, Caps, Grade, Profile };
export { resolveBounds };

export type PriceBar = { date: string; close: number };
export type PriceTable = Map<string, PriceBar[]>; // ticker -> 날짜 오름차순

export type Holding = {
  ticker: string;
  grade: Grade;
  assetGroup: "EQUITY" | "BOND" | "COMMODITY";
  sectorGroup: string;
  countryGroup: string;
  /** Spec 이 요청한 원출력. 프리셋·하드캡 클램프 전 값 */
  weightMinRaw: number;
  weightMaxRaw: number;
};

export type Costs = { fee: number; slippage: number; tax: number };

export type CapApplication = {
  stage: "자산군" | "국가" | "섹터";
  groupId: string;
  sumBefore: number;
  cap: number;
  factor: number;
};

export type Decision = {
  date: string;
  signals: Record<string, number>;
  mapped: Record<string, number>;
  target: Record<string, number>;
  cash: number;
  capApplications: CapApplication[];
  turnover: number;
  cost: number;
};

export type RunResult = {
  series: { date: string; equity: number }[];
  decisions: Decision[];
  finalHoldings: Record<string, number>;
  /** 실제로 리밸런싱한 날 */
  rebalanceDates: string[];
  /** 이탈했지만 최소 간격에 걸려 건너뛴 날 (drift 일 때만 생긴다) */
  skippedRebalanceDates: string[];
};

// --- 일정 -------------------------------------------------------------------
// backend/app/backtest/runner.py · inputs.py 와 같은 규칙이다. 두 러너 대조
// (backend/tests/test_backtest_parity.py)가 블록별 리밸런싱 날짜를 비교한다.

/** 각 주의 마지막 거래일 */
export function weeklyDates(dates: string[]): string[] {
  const out: string[] = [];
  let lastKey = "";
  for (const d of dates) {
    // +4 는 1970-01-01(목)을 주 시작 요일에 맞추는 보정이다. 연도를 키에 넣어
    // 해를 넘는 주가 두 구간으로 갈리게 한다 — 원래 동작을 그대로 유지한다.
    const dt = new Date(`${d}T00:00:00Z`);
    const key = `${dt.getUTCFullYear()}-${Math.floor((dt.getTime() / 86400000 + 4) / 7)}`;
    if (key === lastKey) out[out.length - 1] = d;
    else {
      out.push(d);
      lastKey = key;
    }
  }
  return out;
}

/** 각 달의 첫 날짜 */
export function monthlyFirst(dates: string[]): string[] {
  return monthlyOnOrAfter(dates, 1);
}

/** 각 달에서 day 일 이후(포함) 첫 날짜. 그런 날짜가 없는 달은 건너뛴다 */
export function monthlyOnOrAfter(dates: string[], day: number): string[] {
  const out: string[] = [];
  let done = "";
  for (const d of dates) {
    if (d.slice(0, 7) !== done && Number(d.slice(8, 10)) >= day) {
      out.push(d);
      done = d.slice(0, 7);
    }
  }
  return out;
}

/** 각 분기(1·4·7·10월 시작)의 첫 날짜 */
export function quarterlyFirst(dates: string[]): string[] {
  const out: string[] = [];
  let last = "";
  for (const d of dates) {
    const key = `${d.slice(0, 4)}-Q${Math.floor((Number(d.slice(5, 7)) - 1) / 3)}`;
    if (key !== last) {
      out.push(d);
      last = key;
    }
  }
  return out;
}

function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86400000);
}

/** 앞 리밸런싱일과의 간격이 모자란 날짜를 건너뛴다 (FN-506 최소 간격 검사) */
export function applyMinInterval(candidates: string[], minIntervalDays: number): { kept: string[]; skipped: string[] } {
  const kept: string[] = [];
  const skipped: string[] = [];
  for (const d of candidates) {
    if (kept.length > 0 && daysBetween(kept[kept.length - 1], d) < minIntervalDays) skipped.push(d);
    else kept.push(d);
  }
  return { kept, skipped };
}

/**
 * RB_THRESHOLD_5 "목표 비중에서 5%p 이상 벌어진 종목이 생기면 되돌린다"(db/seeds/05_bbl.sql).
 * 전략서 trigger 에 임계값 칸이 없어 블록 정의에서 온다. runner.py 의 DRIFT_THRESHOLD 와 같다.
 */
export const DRIFT_THRESHOLD = 0.05;

export type RebalanceRule = { trigger: { type: string; freq: string; day?: number | null }; min_interval_days: number };

/** (type/freq/day) → 주간 평가일 → 후보일, 이탈 임계값. backend inputs._SCHEDULES 와 같다 */
export const SCHEDULES: Record<string, { candidates: (weekly: string[]) => string[]; drift: number | null }> = {
  "calendar/monthly/1": { candidates: monthlyFirst, drift: null },
  "calendar/monthly/15": { candidates: (w) => monthlyOnOrAfter(w, 15), drift: null },
  "calendar/quarterly/1": { candidates: quarterlyFirst, drift: null },
  "calendar/weekly/1": { candidates: (w) => [...w], drift: null },
  "threshold/daily/null": { candidates: (w) => [...w], drift: DRIFT_THRESHOLD },
};

/**
 * 리밸런싱 규칙 → runBacktest 의 rebalanceDates · drift. 달력형은 최소 간격을 여기서 미리 거르고,
 * threshold 는 후보일 전부를 주고 러너가 판정 중에 거른다. 신호형(signal)은 아직 없다.
 */
export function scheduleFor(
  rule: RebalanceRule,
  weekly: string[],
): { rebalanceDates: string[]; skipped: string[]; drift?: { threshold: number; minIntervalDays: number } } {
  const t = rule.trigger;
  const entry = SCHEDULES[`${t.type}/${t.freq}/${t.day ?? null}`];
  if (!entry) throw new Error(`러너가 지원하지 않는 리밸런싱 규칙: ${JSON.stringify(rule)}`);
  const candidates = entry.candidates(weekly);
  if (entry.drift === null) {
    const { kept, skipped } = applyMinInterval(candidates, rule.min_interval_days);
    return { rebalanceDates: kept, skipped };
  }
  return { rebalanceDates: candidates, skipped: [], drift: { threshold: entry.drift, minIntervalDays: rule.min_interval_days } };
}

/**
 * 직전 리밸런싱 목표 비중이 그 뒤 가격 변화로 흘러간 추정 비중.
 *   w_i = t_i·r_i / (Σ_j t_j·r_j + (1 − Σ_j t_j)),  r_i = P_i(지금) / P_i(직전 리밸런싱)
 * 현금은 움직이지 않는다고 보고 비용은 무시한다. 실제 보유 수량을 쓰지 않는 이유는 파이썬 러너
 * (vectorbt 일괄 체결)가 실행 중에 수량을 모르기 때문이다 — 두 러너가 같은 식을 쓴다.
 * runner.py 의 drifted_weights 와 같은 순서로 더한다. 가격이 없거나 0 이하이면 r=1.
 */
export function driftedWeights(
  target: Record<string, number>,
  prevPx: Record<string, number>,
  nowPx: Record<string, number>,
  tickers: string[],
): Record<string, number> {
  const grown: Record<string, number> = {};
  let invested = 0;
  let totalTarget = 0;
  for (const t of tickers) {
    const a = prevPx[t];
    const b = nowPx[t];
    const r = a !== undefined && b !== undefined && a > 0 && b > 0 ? b / a : 1;
    grown[t] = target[t] * r;
    invested += grown[t];
    totalTarget += target[t];
  }
  const denom = invested + (1 - totalTarget);
  return Object.fromEntries(tickers.map((t) => [t, grown[t] / denom]));
}

/** 종목별 |흘러간 비중 − 목표 비중| 의 최댓값 (절대 %p, 0.05 = 5%p) */
export function maxDrift(
  target: Record<string, number>,
  prevPx: Record<string, number>,
  nowPx: Record<string, number>,
  tickers: string[],
): number {
  const w = driftedWeights(target, prevPx, nowPx, tickers);
  return Math.max(...tickers.map((t) => Math.abs(w[t] - target[t])));
}

// --- 지표 -------------------------------------------------------------------
// 전부 t 시점까지의 종가만 본다. 미래를 참조하면 백테스트가 무의미해진다.

export function momentum(closes: number[], window: number): number | null {
  if (closes.length <= window) return null;
  const now = closes[closes.length - 1];
  const then = closes[closes.length - 1 - window];
  if (!then) return null;
  return now / then - 1;
}

export function rsi(closes: number[], window = 14): number | null {
  if (closes.length <= window) return null;
  let gain = 0;
  let loss = 0;
  for (let i = closes.length - window; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff >= 0) gain += diff;
    else loss -= diff;
  }
  if (gain + loss === 0) return 50;
  return (100 * gain) / (gain + loss);
}

/**
 * 종목 신호 s ∈ [-1, +1].
 *
 * Spec 의 signal_rules.market_analysis 가 지정한 지표(momentum_20d, rsi_14)를
 * 쓴다. 실제 M3 는 LightGBM 이 보정 확률을 내지만, 러너 검증 단계에서는 같은
 * 입력에서 결정론적으로 나오는 값이면 충분하다.
 */
export function signalFrom(closes: number[]): number {
  const mom = momentum(closes, 20);
  const r = rsi(closes, 14);
  if (mom === null || r === null) return 0;

  // 20일 ±10% 를 ±1 로 본다. 그 밖은 포화시킨다.
  const momScore = Math.max(-1, Math.min(1, mom / 0.1));
  // RSI 50 을 중립으로 두고 ±30 을 ±1 로 본다.
  const rsiScore = Math.max(-1, Math.min(1, (r - 50) / 30));
  return Math.max(-1, Math.min(1, 0.6 * momScore + 0.4 * rsiScore));
}

// --- 신호 → 비중 매핑 (통제 계층) --------------------------------------------
export function mapSignalsToWeights(
  holdings: Holding[],
  bounds: Record<string, Bound>,
  signals: Record<string, number>,
  profile: Profile,
  caps: Caps,
): { mapped: Record<string, number>; target: Record<string, number>; cash: number; applications: CapApplication[] } {
  const investable = 1 - profile.cashMin;

  // 1 선형 매핑 w = min + (s+1)/2 × (max − min)
  const raw = holdings.map((h) => {
    const b = bounds[h.ticker];
    const s = signals[h.ticker] ?? 0;
    return b.min + ((s + 1) / 2) * (b.max - b.min);
  });

  // 2~4 정규화 + 클램프 (최대 3회)
  let w = [...raw];
  for (let it = 0; it < 3; it++) {
    const sum = w.reduce((a, b) => a + b, 0);
    if (sum <= 0) break;
    w = w.map((v) => (v * investable) / sum);
    w = w.map((v, i) => {
      const b = bounds[holdings[i].ticker];
      return Math.min(Math.max(v, b.min), b.max);
    });
  }
  const mapped = Object.fromEntries(holdings.map((h, i) => [h.ticker, w[i]]));

  // 5 그룹 캡. 상위(자산군)를 먼저, 하위(국가 → 섹터)를 나중에 적용한다.
  const cur = [...w];
  const applications: CapApplication[] = [];

  const groupOf = (h: Holding, level: "자산군" | "국가" | "섹터") =>
    level === "자산군" ? h.assetGroup : level === "국가" ? h.countryGroup : h.sectorGroup;

  const applyLevel = (level: "자산군" | "국가" | "섹터") => {
    const groups = [...new Set(holdings.map((h) => groupOf(h, level)))];
    for (const g of groups) {
      if (caps.exempt.includes(g)) continue;
      const cap = caps.group[g];
      if (cap === undefined) continue;
      const idx = holdings.map((h, i) => (groupOf(h, level) === g ? i : -1)).filter((i) => i >= 0);
      const before = idx.reduce((a, i) => a + cur[i], 0);
      if (before > cap + 1e-9) {
        const factor = cap / before;
        idx.forEach((i) => (cur[i] *= factor));
        applications.push({ stage: level, groupId: g, sumBefore: before, cap, factor });
      }
    }
  };

  applyLevel("자산군");
  applyLevel("국가");
  applyLevel("섹터");

  const target = Object.fromEntries(holdings.map((h, i) => [h.ticker, cur[i]]));
  const cash = 1 - cur.reduce((a, b) => a + b, 0);
  return { mapped, target, cash, applications };
}

// --- 러너 -------------------------------------------------------------------

/** 해당 날짜까지(포함) 확정된 종가만 돌려준다. 미래 참조 차단. */
function closesUpTo(bars: PriceBar[], date: string): number[] {
  const out: number[] = [];
  for (const b of bars) {
    if (b.date > date) break;
    out.push(b.close);
  }
  return out;
}

function priceOn(bars: PriceBar[], date: string): number | null {
  let last: number | null = null;
  for (const b of bars) {
    if (b.date > date) break;
    last = b.close;
  }
  return last;
}

export type RunOptions = {
  holdings: Holding[];
  prices: PriceTable;
  profile: Profile;
  caps: Caps;
  costs: Costs;
  hardcapMaxPerAsset: number;
  /** 평가 시점 목록 (보통 주간). 오름차순 */
  valuationDates: string[];
  /** 리밸런싱 시점 목록. valuationDates 의 부분집합이어야 한다. drift 가 있으면 판정 후보일 */
  rebalanceDates: string[];
  /**
   * 비중 이탈 규칙. 주면 첫 후보일은 무조건, 그 뒤는 직전 목표의 추정 이탈(maxDrift)이 임계값
   * 이상이고 직전 리밸런싱과 minIntervalDays 이상 떨어진 후보일에만 리밸런싱한다
   */
  drift?: { threshold: number; minIntervalDays: number };
  initialCash: number;
  /** "signal" = 3관점 신호 사용, "equalWeight" = 신호 없이 동일가중 (대조군) */
  mode: "signal" | "equalWeight";
};

export function runBacktest(opts: RunOptions): RunResult {
  const { holdings, prices, profile, caps, costs, valuationDates, rebalanceDates, initialCash, mode } = opts;

  const bounds = resolveBounds(holdings, profile, opts.hardcapMaxPerAsset);
  const rebalanceSet = new Set(rebalanceDates);

  const shares: Record<string, number> = Object.fromEntries(holdings.map((h) => [h.ticker, 0]));
  let cash = initialCash;

  const series: { date: string; equity: number }[] = [];
  const decisions: Decision[] = [];
  const skippedRebalanceDates: string[] = [];
  const tickers = holdings.map((h) => h.ticker);
  let last: { date: string; target: Record<string, number>; px: Record<string, number> } | null = null;

  for (const date of valuationDates) {
    // 1) 이 시점의 평가액을 먼저 구한다
    const px: Record<string, number> = {};
    for (const h of holdings) {
      const p = priceOn(prices.get(h.ticker) ?? [], date);
      if (p !== null) px[h.ticker] = p;
    }
    let equity = cash;
    for (const h of holdings) equity += (shares[h.ticker] ?? 0) * (px[h.ticker] ?? 0);

    // 2) 리밸런싱일이면 신호를 내고 목표 비중으로 옮긴다. drift 면 이탈·간격을 먼저 본다
    let due = rebalanceSet.has(date) && equity > 0;
    if (due && opts.drift && last) {
      if (maxDrift(last.target, last.px, px, tickers) < opts.drift.threshold) due = false;
      else if (daysBetween(last.date, date) < opts.drift.minIntervalDays) {
        skippedRebalanceDates.push(date);
        due = false;
      }
    }
    if (due) {
      const signals: Record<string, number> = {};
      for (const h of holdings) {
        if (mode === "equalWeight") {
          // 대조군: 신호를 쓰지 않는다. s=0 이면 허용범위의 정중앙이 된다.
          signals[h.ticker] = 0;
        } else {
          const hist = closesUpTo(prices.get(h.ticker) ?? [], date);
          signals[h.ticker] = signalFrom(hist);
        }
      }

      const { mapped, target, cash: targetCash, applications } = mapSignalsToWeights(
        holdings,
        bounds,
        signals,
        profile,
        caps,
      );

      let turnover = 0;
      let costPaid = 0;
      for (const h of holdings) {
        const p = px[h.ticker];
        if (!p) continue;
        const desiredValue = equity * target[h.ticker];
        const currentValue = (shares[h.ticker] ?? 0) * p;
        const delta = desiredValue - currentValue;
        if (Math.abs(delta) < 1) continue;

        // 체결가는 슬리피지만큼 불리하게 잡는다. 사면 비싸게, 팔면 싸게.
        const fillPrice = p * (1 + Math.sign(delta) * costs.slippage);
        const qty = delta / fillPrice;
        const gross = Math.abs(qty * fillPrice);
        const fee = gross * costs.fee + (delta < 0 ? gross * costs.tax : 0);

        shares[h.ticker] = (shares[h.ticker] ?? 0) + qty;
        cash -= qty * fillPrice + fee;
        turnover += gross;
        costPaid += fee + gross * costs.slippage;
      }

      last = { date, target, px };
      decisions.push({
        date,
        signals,
        mapped,
        target,
        cash: targetCash,
        capApplications: applications,
        turnover: turnover / equity,
        cost: costPaid,
      });
    }

    // 3) 체결 후 평가액을 다시 계산해 기록한다
    let after = cash;
    for (const h of holdings) after += (shares[h.ticker] ?? 0) * (px[h.ticker] ?? 0);
    series.push({ date, equity: Math.round(after) });
  }

  return {
    series,
    decisions,
    finalHoldings: shares,
    rebalanceDates: decisions.map((d) => d.date),
    skippedRebalanceDates,
  };
}

/** 첫날 전액 매수 후 보유. 시장 참조용 벤치마크. */
export function runBuyAndHold(
  bars: PriceBar[],
  valuationDates: string[],
  initialCash: number,
  costs: Costs,
): { date: string; equity: number }[] {
  const first = priceOn(bars, valuationDates[0]);
  if (!first) return valuationDates.map((d) => ({ date: d, equity: initialCash }));
  const fill = first * (1 + costs.slippage);
  const qty = (initialCash * (1 - costs.fee)) / fill;
  return valuationDates.map((d) => {
    const p = priceOn(bars, d) ?? first;
    return { date: d, equity: Math.round(qty * p) };
  });
}
