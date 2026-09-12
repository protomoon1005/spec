// data/prices.csv + data/universe.csv 를 읽어 백테스트를 돌리고
// data/backtest-result.json 을 쓴다.
//
// 실행:  node --experimental-strip-types scripts/run-backtest.ts
//
// 세 계열을 함께 낸다.
//   strategy   3관점 신호 사용
//   control    같은 제약·같은 유니버스, 신호 없음 (대조군)
//   market     KODEX 200 매수 후 보유 (시장 참조)
//
// 전략과 대조군의 차이가 곧 "신호가 더한 가치"다. 시장은 맥락일 뿐
// 우열을 다투는 대상이 아니다 — 위험 수준이 다르기 때문이다.

import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  runBacktest,
  runBuyAndHold,
  type Caps,
  type Costs,
  type Grade,
  type Holding,
  type PriceBar,
  type PriceTable,
  type Profile,
} from "../lib/backtest.ts";

const ROOT = join(import.meta.dirname, "..");
const DATA = join(ROOT, "data");

// --- 설정 -------------------------------------------------------------------
const MARKET_TICKER = "069500"; // KODEX 200
const INITIAL = 10_000_000;
const COSTS: Costs = { fee: 0.00015, slippage: 0.0005, tax: 0 };
const HARDCAP_MAX_PER_ASSET = 0.3; // hardcap_versions v0.1

// 데모 Spec 의 성향. 러너는 성향을 인자로 받으므로 여기만 바꾸면 다른 성향으로 돌아간다.
const PROFILES: Record<number, Profile> = {
  1: { label: "안정투자형", riskLevel: 1, cashMin: 0.2, gradeCap: { G1: 0, G2: 0, G3: 0, G4: 0.1, G5: 0.3, G6: 1 } },
  2: { label: "안정추구형", riskLevel: 2, cashMin: 0.15, gradeCap: { G1: 0, G2: 0, G3: 0.1, G4: 0.25, G5: 0.4, G6: 1 } },
  3: { label: "위험중립형", riskLevel: 3, cashMin: 0.1, gradeCap: { G1: 0, G2: 0.1, G3: 0.25, G4: 0.35, G5: 0.5, G6: 1 } },
  4: { label: "성장투자형", riskLevel: 4, cashMin: 0.05, gradeCap: { G1: 0.1, G2: 0.25, G3: 0.35, G4: 0.4, G5: 0.6, G6: 1 } },
  5: { label: "공격투자형", riskLevel: 5, cashMin: 0.05, gradeCap: { G1: 0.25, G2: 0.35, G3: 0.4, G4: 0.5, G5: 0.7, G6: 1 } },
};

const ASSET_CAP: Record<string, Record<number, number>> = {
  EQUITY: { 1: 0.25, 2: 0.4, 3: 0.6, 4: 0.75, 5: 0.85 },
  BOND: { 1: 0.7, 2: 0.6, 3: 0.5, 4: 0.35, 5: 0.25 },
  COMMODITY: { 1: 0.05, 2: 0.1, 3: 0.15, 4: 0.2, 5: 0.25 },
};

const RISK_LEVEL = Number(process.env.RISK_LEVEL ?? 4); // 기본 성장투자형
const profile = PROFILES[RISK_LEVEL];

// 데모 Spec 의 유니버스. 자산군 3개와 반도체 섹터, 국내·미국이 모두 들어가도록 골랐다.
// 그래야 그룹캡 세 계층이 전부 시연된다.
const SPEC_UNIVERSE: { ticker: string; min: number; max: number }[] = [
  { ticker: "069500", min: 0.05, max: 0.35 }, // KODEX 200
  { ticker: "091160", min: 0.0, max: 0.25 }, // KODEX 반도체
  { ticker: "360750", min: 0.05, max: 0.3 }, // TIGER 미국S&P500
  { ticker: "133690", min: 0.0, max: 0.25 }, // TIGER 미국나스닥100
  { ticker: "273130", min: 0.1, max: 0.4 }, // KODEX 종합채권(AA-이상)
  { ticker: "459580", min: 0.0, max: 0.3 }, // KODEX CD금리액티브
  { ticker: "132030", min: 0.0, max: 0.15 }, // KODEX 골드선물(H)
];

// --- CSV 읽기 ---------------------------------------------------------------
function parseCsv(path: string): Record<string, string>[] {
  const text = readFileSync(path, "utf-8").replace(/^﻿/, "");
  const lines = text.trim().split(/\r?\n/);
  const head = lines[0].split(",");
  return lines.slice(1).map((line) => {
    // 종목명에 쉼표가 없다는 전제. build_universe.py 가 그렇게 저장한다.
    const cells = line.split(",");
    return Object.fromEntries(head.map((h, i) => [h, cells[i] ?? ""]));
  });
}

const universeRows = parseCsv(join(DATA, "universe.csv"));
const priceRows = parseCsv(join(DATA, "prices.csv"));

const byTicker = new Map(universeRows.map((r) => [r.ticker, r]));

const prices: PriceTable = new Map();
for (const r of priceRows) {
  const arr = prices.get(r.ticker) ?? [];
  arr.push({ date: r.date, close: Number(r.close) });
  prices.set(r.ticker, arr);
}
for (const arr of prices.values()) arr.sort((a, b) => a.date.localeCompare(b.date));

// --- 유니버스 구성 -----------------------------------------------------------
const holdings: Holding[] = [];
for (const spec of SPEC_UNIVERSE) {
  const meta = byTicker.get(spec.ticker);
  if (!meta) {
    console.error(`  ! ${spec.ticker} 가 universe.csv 에 없다 — 건너뜀`);
    continue;
  }
  holdings.push({
    ticker: spec.ticker,
    grade: meta.risk_tag as Grade,
    assetGroup: meta.asset_group as Holding["assetGroup"],
    sectorGroup: meta.sector_group,
    countryGroup: meta.country_group,
    weightMinRaw: spec.min,
    weightMaxRaw: spec.max,
  });
}

const caps: Caps = {
  group: {
    EQUITY: ASSET_CAP.EQUITY[RISK_LEVEL],
    BOND: ASSET_CAP.BOND[RISK_LEVEL],
    COMMODITY: ASSET_CAP.COMMODITY[RISK_LEVEL],
    SECTOR_SEMICONDUCTOR: 0.3,
    SECTOR_BATTERY: 0.3,
    SECTOR_BIOHEALTH: 0.3,
    SECTOR_FINANCE: 0.3,
    SECTOR_INTERNET_PLATFORM: 0.3,
    SECTOR_CONSUMER: 0.3,
    COUNTRY_KR: 0.5,
    COUNTRY_US: 0.5,
    COUNTRY_OTHER: 0.5,
  },
  // SECTOR_OTHER 는 산업 섹터가 아니라 미분류 버킷이라 캡을 걸지 않는다.
  exempt: ["SECTOR_OTHER"],
};

// --- 평가·리밸런싱 일정 ------------------------------------------------------
// 평가는 주간(각 주의 마지막 거래일), 리밸런싱은 월간(각 달의 첫 평가일).
const marketBars = prices.get(MARKET_TICKER) ?? [];
const allDates = marketBars.map((b) => b.date);

const weekly: string[] = [];
let lastWeekKey = "";
for (const d of allDates) {
  const dt = new Date(`${d}T00:00:00Z`);
  const week = `${dt.getUTCFullYear()}-${Math.floor((dt.getTime() / 86400000 + 4) / 7)}`;
  if (week !== lastWeekKey && weekly.length) weekly[weekly.length - 1] = weekly[weekly.length - 1];
  if (week !== lastWeekKey) {
    weekly.push(d);
    lastWeekKey = week;
  } else {
    weekly[weekly.length - 1] = d;
  }
}

const rebalance: string[] = [];
let lastMonth = "";
for (const d of weekly) {
  const m = d.slice(0, 7);
  if (m !== lastMonth) {
    rebalance.push(d);
    lastMonth = m;
  }
}

console.log(`성향        ${profile.label} (risk_level ${RISK_LEVEL})`);
console.log(`유니버스    ${holdings.length}종목`);
console.log(`평가 시점   ${weekly.length}개 (${weekly[0]} ~ ${weekly[weekly.length - 1]})`);
console.log(`리밸런싱    ${rebalance.length}회`);

// --- 실행 -------------------------------------------------------------------
const strategy = runBacktest({
  holdings, prices, profile, caps, costs: COSTS,
  hardcapMaxPerAsset: HARDCAP_MAX_PER_ASSET,
  valuationDates: weekly, rebalanceDates: rebalance,
  initialCash: INITIAL, mode: "signal",
});

const control = runBacktest({
  holdings, prices, profile, caps, costs: COSTS,
  hardcapMaxPerAsset: HARDCAP_MAX_PER_ASSET,
  valuationDates: weekly, rebalanceDates: rebalance,
  initialCash: INITIAL, mode: "equalWeight",
});

const market = runBuyAndHold(marketBars, weekly, INITIAL, COSTS);

const meta = JSON.parse(readFileSync(join(DATA, "meta.json"), "utf-8"));

const out = {
  as_of: weekly[weekly.length - 1],
  period_start: weekly[0],
  initial: INITIAL,
  profile: { label: profile.label, risk_level: RISK_LEVEL, cash_min: profile.cashMin },
  costs: COSTS,
  hardcap_max_per_asset: HARDCAP_MAX_PER_ASSET,
  data_source: meta.source,
  price_field: meta.price_field,
  universe: holdings.map((h) => ({
    ticker: h.ticker,
    name: byTicker.get(h.ticker)?.name ?? h.ticker,
    grade: h.grade,
    asset_group: h.assetGroup,
    sector_group: h.sectorGroup,
    country_group: h.countryGroup,
    weight_min_raw: h.weightMinRaw,
    weight_max_raw: h.weightMaxRaw,
  })),
  caps: caps.group,
  series: weekly.map((d, i) => ({
    date: d,
    strategy: strategy.series[i].equity,
    control: control.series[i].equity,
    market: market[i].equity,
  })),
  decisions: strategy.decisions,
  control_decisions: control.decisions,
};

writeFileSync(join(DATA, "backtest-result.json"), JSON.stringify(out, null, 2), "utf-8");

// --- 요약 -------------------------------------------------------------------
const summary = (s: { date: string; equity: number }[]) => {
  const first = s[0].equity;
  const last = s[s.length - 1].equity;
  const years = (new Date(s[s.length - 1].date).getTime() - new Date(s[0].date).getTime()) / (365.25 * 864e5);
  let peak = first;
  let mdd = 0;
  for (const p of s) {
    if (p.equity > peak) peak = p.equity;
    mdd = Math.min(mdd, p.equity / peak - 1);
  }
  return { total: last / first - 1, cagr: (last / first) ** (1 / years) - 1, mdd };
};

const pc = (x: number) => `${(x * 100).toFixed(1)}%`;
for (const [name, s] of [["전략", strategy.series], ["대조군", control.series], ["시장", market]] as const) {
  const m = summary(s);
  console.log(`${name.padEnd(5)} 누적 ${pc(m.total).padStart(8)}  연환산 ${pc(m.cagr).padStart(7)}  MDD ${pc(m.mdd).padStart(7)}`);
}
const capCount = strategy.decisions.reduce((a, d) => a + d.capApplications.length, 0);
console.log(`그룹캡 적용 ${capCount}회 / 리밸런싱 ${strategy.decisions.length}회`);
console.log(`저장: data/backtest-result.json`);
