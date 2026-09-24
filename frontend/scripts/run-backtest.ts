// 저장소 루트 data/ 의 시세·유니버스를 읽어 백테스트를 돌리고
// frontend/data/backtest-result.json 을 쓴다.
//
// 입력이 루트 data/ 인 이유: 그쪽이 정본이다. frontend/data/ 에 있던 옛 수집분은
// risk_tag 를 2026-09-11 까지의 변동성으로 매겨서, 2023~2025 를 평가하는 백테스트의
// 등급 상한 판단에 그 뒤 구간 정보가 섞여 있었다 — 시점 무결성 위반이다.
// 루트 data/ 는 2025-12-31 까지만 쓰고 OHLCV 도 갖고 있다(M3 피처가 쓴다).
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
import { runBacktest, runBuyAndHold, type Costs, type Holding, type PriceTable } from "../lib/backtest.ts";
import { capsFor, HARDCAP, profileFor, type Grade } from "../lib/policy.ts";

const ROOT = join(import.meta.dirname, "..");     // frontend/
const REPO = join(ROOT, "..");                    // 저장소 루트
const SRC = join(REPO, "data");                   // 입력 — 정본
const OUT = join(ROOT, "data");                   // 출력 — 화면이 import 하는 곳

// --- 설정 -------------------------------------------------------------------
const MARKET_TICKER = "069500"; // KODEX 200

// 성과를 집계하는 구간의 시작. 데이터는 2022-06 부터 있지만 그 앞 구간은 지표
// 워밍업(모멘텀 20 · RSI 14, M3 피처는 120행)으로만 쓰고 평가에 넣지 않는다.
// 데모 계획 1.4 의 백테스트 구간이 2023-01-01 ~ 2025-12-31 이다.
const PERIOD_START = "2023-01-01";
const INITIAL = 10_000_000;
const COSTS: Costs = { fee: 0.00015, slippage: 0.0005, tax: 0 };


// 성향은 러너 인자다. 환경변수로 바꿔 5개 성향 아무거나 돌릴 수 있다.
const RISK_LEVEL = Number(process.env.RISK_LEVEL ?? 4);
const profile = profileFor(RISK_LEVEL);
const caps = capsFor(RISK_LEVEL);

// 데모 Spec 의 유니버스. 자산군 3개와 반도체 섹터, 국내·미국이 모두 들어가도록 골랐다.
// 그래야 그룹캡 세 계층이 전부 시연된다.
const SPEC_UNIVERSE: { ticker: string; min: number; max: number }[] = [
  { ticker: "069500", min: 0.05, max: 0.35 }, // KODEX 200
  { ticker: "091160", min: 0.0, max: 0.25 }, // KODEX 반도체
  { ticker: "360750", min: 0.05, max: 0.3 }, // TIGER 미국S&P500
  { ticker: "133690", min: 0.0, max: 0.25 }, // TIGER 미국나스닥100
  { ticker: "273130", min: 0.1, max: 0.4 }, // KODEX 종합채권(AA-이상)
  // 아래 둘은 정본 교체(60종목) 때 옛 종목이 유니버스에서 빠져 대체한 것이다.
  { ticker: "357870", min: 0.0, max: 0.3 }, // TIGER CD금리투자KIS — 459580 KODEX CD금리 대체
  { ticker: "411060", min: 0.0, max: 0.15 }, // ACE KRX금현물 — 132030 골드선물 대체(롤코스트 없음)
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

const universeRows = parseCsv(join(SRC, "universe.csv"));
const priceRows = parseCsv(join(SRC, "prices.csv"));

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


// --- 평가·리밸런싱 일정 ------------------------------------------------------
// 평가는 주간(각 주의 마지막 거래일), 리밸런싱은 월간(각 달의 첫 평가일).
const marketBars = prices.get(MARKET_TICKER) ?? [];
// 평가 시점만 자른다. prices 는 그대로 둔다 — 지표가 PERIOD_START 이전 종가를
// 되돌아볼 수 있어야 첫 리밸런싱부터 신호가 나온다.
const allDates = marketBars.map((b) => b.date).filter((d) => d >= PERIOD_START);

/** 각 주의 마지막 거래일 */
function weeklyDates(dates: string[]): string[] {
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

/** 각 달의 첫 평가일 */
function monthlyFirst(dates: string[]): string[] {
  const out: string[] = [];
  let lastMonth = "";
  for (const d of dates) {
    const m = d.slice(0, 7);
    if (m !== lastMonth) {
      out.push(d);
      lastMonth = m;
    }
  }
  return out;
}

const weekly = weeklyDates(allDates);
const rebalance = monthlyFirst(weekly);

console.log(`성향        ${profile.label} (risk_level ${RISK_LEVEL})`);
console.log(`유니버스    ${holdings.length}종목`);
console.log(`평가 시점   ${weekly.length}개 (${weekly[0]} ~ ${weekly[weekly.length - 1]})`);
console.log(`리밸런싱    ${rebalance.length}회`);

// --- 실행 -------------------------------------------------------------------
const strategy = runBacktest({
  holdings, prices, profile, caps, costs: COSTS,
  hardcapMaxPerAsset: HARDCAP.maxWeightPerAsset,
  valuationDates: weekly, rebalanceDates: rebalance,
  initialCash: INITIAL, mode: "signal",
});

const control = runBacktest({
  holdings, prices, profile, caps, costs: COSTS,
  hardcapMaxPerAsset: HARDCAP.maxWeightPerAsset,
  valuationDates: weekly, rebalanceDates: rebalance,
  initialCash: INITIAL, mode: "equalWeight",
});

const market = runBuyAndHold(marketBars, weekly, INITIAL, COSTS);

const meta = JSON.parse(readFileSync(join(SRC, "meta.json"), "utf-8"));
// 가격 설명(price_field)은 meta.json 이 아니라 fetch_prices.py 가 쓰는 prices.meta.json 에 있다.
const pricesMeta = JSON.parse(readFileSync(join(SRC, "prices.meta.json"), "utf-8"));

const out = {
  as_of: weekly[weekly.length - 1],
  period_start: weekly[0],
  initial: INITIAL,
  profile: { label: profile.label, risk_level: RISK_LEVEL, cash_min: profile.cashMin },
  costs: COSTS,
  hardcap_max_per_asset: HARDCAP.maxWeightPerAsset,
  data_source: meta.source,
  price_field: pricesMeta.price_field,
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

writeFileSync(join(OUT, "backtest-result.json"), JSON.stringify(out, null, 2), "utf-8");

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
