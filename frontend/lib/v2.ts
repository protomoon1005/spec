// 전략서 v0.2 API(/v2)의 응답 모양과, 흐름 화면 사이에 넘기는 결과 보관.
//
// 응답 모양은 설계 문서 "판정 결과 응답 모양"과 backend/app/routers/strategy_v0_2.py 를 따른다.
//   ok      전략서를 draft 로 저장했다. 설명(explanation)과 검사(checks)가 온다
//   ask     되묻는다. 질문마다 선택지 id 와 글자만 온다. 선택지 값은 서버에만 있다
//   reject  만들 수 없다. 사유 코드, 사용자에게 보여 줄 문장, 문장 속 근거
// 결과는 탭 저장소(sessionStorage)에 둔다. 되묻기 대화도 서버에서 30분이면 만료된다.

export type CheckItem = { kind: "block" | "ask" | "warn"; code: string; message: string; path?: string };
export type Check = { stage: number; name?: string; result: "pass" | "warn" | "ask" | "block" | "not_run"; items: CheckItem[] };

export type Question = {
  id: string;
  slot: string;
  kind: string;
  text: string;
  choices: { id: string; label: string }[];
  allow_text: boolean;
};

export type Outcome =
  | { status: "ok"; session_id: string; strategy_id: string; spec: unknown; checks: Check[]; explanation: string[] }
  | { status: "ask"; session_id: string; round: number; remaining_total: number; questions: Question[] }
  | { status: "reject"; session_id: string; code: string; message: string; evidence: string; stage: number | null };

export type StrategyView = {
  strategy_id: string;
  name: string;
  status: string;
  input_prompt: string | null;
  spec: unknown;
  explanation: string[];
  checks: { stage: number; passed: boolean; items: CheckItem[] }[];
};

export type SeriesPoint = { date: string; strategy: number; hold_same: number; market_proxy: number };
export type Summary = { total_return: number; cagr: number | null; mdd: number; volatility: number; final_value: number };
export type Fill = {
  date: string;
  ticker: string;
  side: "buy" | "sell";
  shares: number;
  price: number;
  fee: number;
  tax: number;
  decided_on: string;
};
export type Trade = {
  ticker: string;
  shares: number;
  buy_date: string;
  buy_price: number;
  sell_date: string;
  sell_price: number;
  holding_days: number;
  pnl: number;
  return: number;
};

export type BacktestV2 = {
  run_id: number;
  strategy_id: string;
  period_start: string | null;
  period_end: string | null;
  seed_money: number | null;
  status: "queued" | "running" | "done" | "failed" | null;
  data_snapshot_asof: string | null;
  reason?: string;
  metrics?: { cagr: number | null; mdd: number | null; win_rate: number | null; benchmark_cagr: number | null };
  summary?: { strategy: Summary & { fills: number; round_trips: number }; hold_same: Summary; market_proxy: Summary };
  series?: SeriesPoint[];
  fills?: Fill[];
  trades?: Trade[];
  unfilled?: { ticker: string; side: string; decided_on: string; reason: string }[];
  holdings_at_end?: Record<string, number>;
  names?: Record<string, string>;
  notes?: string[];
  // 종목별 매매 지점 차트 자료(backend/app/backtest/trade_chart.py). 날짜는 series 와 같다
  trade_chart?: TradeChart[];
  // 같은 전략서를 한 번 더 돌린 결과 지문이 같은지(재현성)
  repro?: { first_hash: string; hash: string; match: boolean };
};

export type ChartLine = { label: string; values: (number | null)[] };
export type ChartPoint = { date: string; price: number; signal_date: string | null };
export type TradeChart = {
  ticker: string;
  name: string;
  close: (number | null)[];
  price_lines: ChartLine[];
  panels: { title: string; lines: ChartLine[]; refs: number[] }[];
  buys: ChartPoint[];
  sells: ChartPoint[];
};

// POST /v2/strategies/{id}/baseline 작업 결과(backend/app/baseline/compare.py)
export type OrderRow = { date: string; ticker: string; name: string; side: "buy" | "sell"; weight: number };
export type BaselineResult =
  | { status: "failed"; reason: string }
  | {
      status: "done";
      outcome: "code" | "clarify" | "reject" | "error";
      message: string;
      code: string | null;
      raw: string | null;
      seconds: number;
      cached: boolean;
      tickers: string[];
      period: [string, string];
      ours: { total_return: number | null; orders: OrderRow[] };
      base: { total_return: number | null; orders: OrderRow[]; ignored: number } | null;
      run_error: string | null;
      same_orders: boolean | null;
      series: { date: string; ours: number; base: number }[] | null;
    };

// 거절 사유 코드의 쉬운 이름(설계 문서 "거절하는 경우")
export const REJECT_LABELS: Record<string, string> = {
  FUTURE_REF: "미래를 봐야 함",
  UNKNOWN_INDICATOR: "없는 지표",
  UNSUPPORTED_ASSET: "못 다루는 상품",
  UNSUPPORTED_ACTION: "못 다루는 매매",
  INTRADAY: "장중 매매",
  NO_DATA: "데이터 없음",
  OUT_OF_DOMAIN: "투자와 무관",
  UNRESOLVED: "끝내 못 정함",
  VALIDATION: "검사에서 막힘",
};

export const STAGE_NAMES: Record<number, string> = { 1: "모양", 2: "존재", 3: "말이 되는지", 4: "근거" };

const OUTCOME_KEY = "spec.v2.outcome";

export function saveOutcome(outcome: Outcome): void {
  try {
    window.sessionStorage.setItem(OUTCOME_KEY, JSON.stringify(outcome));
  } catch {
    // 저장소가 막힌 브라우저 — 다음 화면이 결과를 못 찾고 처음으로 안내한다.
  }
}

export function loadOutcome(): Outcome | null {
  try {
    const raw = window.sessionStorage.getItem(OUTCOME_KEY);
    return raw ? (JSON.parse(raw) as Outcome) : null;
  } catch {
    return null;
  }
}

// 결과 상태에 맞는 다음 화면. reject 는 보낸 화면에서 보여 준다.
export function nextPath(outcome: Outcome): string | null {
  if (outcome.status === "ok") return `/compile/done?strategy=${encodeURIComponent(outcome.strategy_id)}`;
  if (outcome.status === "ask") return "/compile/answer";
  return null;
}

export const pct = (v: number | null | undefined, digits = 1) => (v == null ? "—" : `${(v * 100).toFixed(digits)}%`);
export const won = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v).toLocaleString("ko-KR")}원`);
