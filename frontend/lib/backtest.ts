// 시점 주입 백테스트 러너.
//
// 기획서 아이디어 7: 같은 코드에 과거 시점을 주입하면 백테스트, 오늘을 주입하면
// 모의운용이 된다. 그래서 이 모듈은 "현재"를 모른다 — 모든 판단이 인자로 받은
// 시점 t 와 t 이전 데이터만 본다.
//
// 파이썬 러너(vectorbt)가 붙으면 이 구현이 대조군이 된다. 같은 입력에 같은
// 주문이 나오는지 맞춰보면 재현성 주장이 검증된다.

export type PriceBar = { date: string; close: number };
export type PriceTable = Map<string, PriceBar[]>; // ticker -> 날짜 오름차순

export type Grade = "G1" | "G2" | "G3" | "G4" | "G5" | "G6";

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

export type Profile = {
  label: string;
  riskLevel: number;
  cashMin: number;
  gradeCap: Record<Grade, number>;
};

export type Caps = {
  /** group_id -> 합계 상한 */
  group: Record<string, number>;
  /** 캡을 적용하지 않을 group_id (미분류 버킷 등) */
  exempt: string[];
};

export type Costs = { fee: number; slippage: number; tax: number };

export type Bound = { min: number; max: number; clampedBy: string | null };

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
};

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

// --- 허용범위 (Validator 2단 프리셋 + 4단 하드캡) -----------------------------
export function resolveBounds(
  holdings: Holding[],
  profile: Profile,
  hardcapMaxPerAsset: number,
): Record<string, Bound> {
  const out: Record<string, Bound> = {};
  for (const h of holdings) {
    const presetCap = profile.gradeCap[h.grade];
    const cap = Math.min(h.weightMaxRaw, presetCap, hardcapMaxPerAsset);
    let by: string | null = null;
    if (cap < h.weightMaxRaw - 1e-9) {
      by = presetCap <= hardcapMaxPerAsset ? "프리셋" : "하드캡";
    }
    out[h.ticker] = { min: Math.min(h.weightMinRaw, cap), max: cap, clampedBy: by };
  }
  return out;
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
  /** 리밸런싱 시점 목록. valuationDates 의 부분집합이어야 한다 */
  rebalanceDates: string[];
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

  for (const date of valuationDates) {
    // 1) 이 시점의 평가액을 먼저 구한다
    const px: Record<string, number> = {};
    for (const h of holdings) {
      const p = priceOn(prices.get(h.ticker) ?? [], date);
      if (p !== null) px[h.ticker] = p;
    }
    let equity = cash;
    for (const h of holdings) equity += (shares[h.ticker] ?? 0) * (px[h.ticker] ?? 0);

    // 2) 리밸런싱일이면 신호를 내고 목표 비중으로 옮긴다
    if (rebalanceSet.has(date) && equity > 0) {
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

  return { series, decisions, finalHoldings: shares };
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
