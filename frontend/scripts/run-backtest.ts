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

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  monthlyFirst,
  runBacktest,
  runBuyAndHold,
  scheduleFor,
  weeklyDates,
  type Costs,
  type Holding,
  type PriceTable,
  type RebalanceRule,
} from "../lib/backtest.ts";
import { capsFor, HARDCAP, profileFor, type Grade } from "../lib/policy.ts";

const ROOT = join(import.meta.dirname, "..");     // frontend/
const REPO = join(ROOT, "..");                    // 저장소 루트
const SRC = join(REPO, "data");                   // 입력 — 정본
const OUT = join(ROOT, "data");                   // 출력 — 화면이 import 하는 곳

// --- 설정 -------------------------------------------------------------------
const MARKET_TICKER = "069500"; // KODEX 200

// 성과를 집계하는 구간의 시작. 데이터는 2019-01 부터(종목별 상장일) 있지만 그 앞 구간은 지표
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

// --- 리밸런싱 블록별 대조군 --------------------------------------------------
// BBL 리밸런싱 블록(db/seeds/05_bbl.sql 의 params_schema 그대로)마다 대조군(신호 없음)을 돌려
// 일정과 목표 비중을 남긴다. backend/tests/test_backtest_parity.py 가 파이썬 러너와 블록별로 대조한다.
// 화면은 이 파일을 읽지 않는다. RB_SIGNAL 은 TS 에 3관점 판단이 없어 일정을 스스로 못 정한다 —
// 파이썬(scripts/run_backtest_vbt.py --signal-dates)이 정한 날짜 파일이 있으면 그 날짜로 대조군을 돌린다.
const BBL_RULES: Record<string, RebalanceRule> = {
  RB_MONTHLY_FIRST: { trigger: { type: "calendar", freq: "monthly", day: 1 }, min_interval_days: 20 },
  RB_MONTHLY_MID: { trigger: { type: "calendar", freq: "monthly", day: 15 }, min_interval_days: 20 },
  RB_QUARTERLY: { trigger: { type: "calendar", freq: "quarterly", day: 1 }, min_interval_days: 60 },
  RB_WEEKLY: { trigger: { type: "calendar", freq: "weekly", day: 1 }, min_interval_days: 5 },
  RB_THRESHOLD_5: { trigger: { type: "threshold", freq: "daily" }, min_interval_days: 5 },
};

const schedules = Object.entries(BBL_RULES).map(([blockId, rule]) => {
  const plan = scheduleFor(rule, weekly);
  const run = runBacktest({
    holdings, prices, profile, caps, costs: COSTS,
    hardcapMaxPerAsset: HARDCAP.maxWeightPerAsset,
    valuationDates: weekly, rebalanceDates: plan.rebalanceDates, drift: plan.drift,
    initialCash: INITIAL, mode: "equalWeight",
  });
  console.log(
    `  ${blockId.padEnd(17)} 리밸런싱 ${String(run.rebalanceDates.length).padStart(3)}회` +
      `  건너뜀 ${plan.skipped.length + run.skippedRebalanceDates.length}회  최종 ${run.series[run.series.length - 1].equity.toLocaleString()}`,
  );
  return {
    block_id: blockId,
    rule,
    candidates: plan.rebalanceDates,
    rebalance_dates: run.rebalanceDates,
    skipped_rebalance_dates: [...plan.skipped, ...run.skippedRebalanceDates],
    control_decisions: run.decisions.map((d) => ({
      date: d.date,
      target: d.target,
      cash: d.cash,
      capApplications: d.capApplications,
    })),
    final_equity: run.series[run.series.length - 1].equity,
  };
});

const signalFile = join(OUT, "signal-rebalance-dates.json");
if (existsSync(signalFile)) {
  const given = JSON.parse(readFileSync(signalFile, "utf-8"));
  if (given.risk_level !== RISK_LEVEL) {
    console.log(`  RB_SIGNAL          날짜 파일의 성향(${given.risk_level})이 달라 건너뜀`);
  } else {
    const run = runBacktest({
      holdings, prices, profile, caps, costs: COSTS,
      hardcapMaxPerAsset: HARDCAP.maxWeightPerAsset,
      valuationDates: weekly, rebalanceDates: given.rebalance_dates,
      initialCash: INITIAL, mode: "equalWeight",
    });
    console.log(`  RB_SIGNAL          리밸런싱 ${String(run.rebalanceDates.length).padStart(3)}회  (파이썬이 정한 날짜)`);
    schedules.push({
      block_id: "RB_SIGNAL",
      rule: given.rule,
      candidates: given.rebalance_dates,
      rebalance_dates: run.rebalanceDates,
      skipped_rebalance_dates: given.skipped_rebalance_dates,
      control_decisions: run.decisions.map((d) => ({
        date: d.date,
        target: d.target,
        cash: d.cash,
        capApplications: d.capApplications,
      })),
      final_equity: run.series[run.series.length - 1].equity,
    });
  }
}

writeFileSync(
  join(OUT, "backtest-schedules.json"),
  JSON.stringify(
    {
      as_of: weekly[weekly.length - 1],
      period_start: weekly[0],
      initial: INITIAL,
      risk_level: RISK_LEVEL,
      universe: out.universe,
      valuation_dates: weekly,
      schedules,
    },
    null,
    2,
  ),
  "utf-8",
);

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
console.log(`저장: data/backtest-result.json · data/backtest-schedules.json`);
