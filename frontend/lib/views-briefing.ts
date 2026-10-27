// 관점 브리핑(U09, /views?run_id=) 이 그리는 값을 실행 기록에서 뽑는다. 화면과 떼어 둔
// 순수 함수라 서버 없이 확인할 수 있다 (scripts/check-views.ts).
//
// 재료: GET /backtest/runs/{run_id} 의
//   decisions[i].views = { weights: {market, sentiment, regime}, probs: {관점: {종목: 보정 확률}} }
//     신호를 쓰는 전략 계열의 리밸런싱마다 러너가 남긴다 (backend/app/views/bridge.py last_views).
//     weights 는 그 판단에 실제로 쓴 관점 가중치 — 전날까지 확정된 성적으로 정해진 값이다.
//   scorer_sources = { market, sentiment, regime: "real" | "mock" | "neutral" }
//
// node 의 타입 제거 실행이 읽을 수 있게 런타임 import 를 두지 않는다.

export type ViewKey = "market" | "sentiment" | "regime";

export const VIEWS: readonly { key: ViewKey; label: string; basis: string }[] = [
  { key: "market", label: "시장분석", basis: "가격 지표 9개 (피처셋 v0.1-ta9)" },
  { key: "sentiment", label: "뉴스 감성", basis: "업종별 뉴스 기사의 긍정·부정" },
  { key: "regime", label: "시장온도", basis: "변동성 지수, 신용 스프레드, 환율, 지수 추세" },
];

// 통합 규칙 확정 수치 (backend/app/views/integrator.py · hedge.py). 화면 설명용 사본이다.
// 판단에는 쓰지 않는다. 정본이 바뀌면 여기도 고친다 — 정책 3중 사본 대조 테스트 밖이다.
export const RULES = {
  tanhScale: 0.5,
  deadzone: 0.1,
  floor: 0.1,
  window: 60,
  equal: 1 / 3,
} as const;

export type ViewsRecord = {
  weights: Record<ViewKey, number>;
  probs: Partial<Record<ViewKey, Record<string, number>>>;
};

/** GET /backtest/runs/{run_id} 중 브리핑이 쓰는 것 */
export type RunForBriefing = {
  run_id: number;
  status: "queued" | "running" | "done" | "failed" | null;
  reason: string | null;
  data_snapshot_asof: string | null;
  scorer_sources: Record<string, string> | null;
  universe: { ticker: string; name: string }[] | null;
  decisions: { date: string; views?: ViewsRecord | null }[] | null;
};

export type WeightPoint = { date: string } & Record<ViewKey, number>;

export type ProbRow = { ticker: string; name: string; probs: Record<ViewKey, number | null> };

export type Briefing = {
  kind: "ok";
  runId: number;
  history: WeightPoint[];
  latest: WeightPoint;
  // 첫 리밸런싱 대비 최신 가중치 변화 (비율, 0.05 = 5%p)
  drift: Record<ViewKey, number>;
  // 가중치가 균등 1/3 에서 처음 벗어난 리밸런싱일. 끝까지 균등이면 null
  firstMove: string | null;
  // 직전 리밸런싱과 가중치가 달라진 횟수
  changes: number;
  sources: Record<ViewKey, string | null>;
  rows: ProbRow[];
};

export type NoBriefing = { kind: "pending" | "failed" | "no-views"; runId: number; message: string };

const EPS = 1e-9;

export function briefingFromRun(run: RunForBriefing): Briefing | NoBriefing {
  const runId = run.run_id;
  if (run.status === "failed") {
    return { kind: "failed", runId, message: `백테스트가 실패했습니다 — ${run.reason ?? "이유가 기록되지 않았습니다"}` };
  }
  if (run.status !== "done") {
    return { kind: "pending", runId, message: `백테스트가 아직 끝나지 않았습니다 (상태: ${run.status ?? "알 수 없음"})` };
  }

  const recorded = (run.decisions ?? []).filter(
    (d): d is { date: string; views: ViewsRecord } => !!d.views && !!d.views.weights,
  );
  if (!recorded.length) {
    return {
      kind: "no-views",
      runId,
      message: "이 실행에는 관점 기록이 없습니다. 관점 기록을 남기기 전에 돌린 실행입니다 — 백테스트를 다시 돌리면 생깁니다.",
    };
  }

  const history: WeightPoint[] = recorded.map((d) => ({
    date: d.date,
    market: d.views.weights.market,
    sentiment: d.views.weights.sentiment,
    regime: d.views.weights.regime,
  }));
  const first = history[0];
  const latest = history[history.length - 1];
  const keys = VIEWS.map((v) => v.key);

  const moved = history.find((p) => keys.some((k) => Math.abs(p[k] - RULES.equal) > EPS));
  let changes = 0;
  for (let i = 1; i < history.length; i++) {
    if (keys.some((k) => Math.abs(history[i][k] - history[i - 1][k]) > EPS)) changes++;
  }

  const sources = Object.fromEntries(keys.map((k) => [k, run.scorer_sources?.[k] ?? null])) as Record<ViewKey, string | null>;
  const drift = Object.fromEntries(keys.map((k) => [k, latest[k] - first[k]])) as Record<ViewKey, number>;

  // 표의 행: 실행 종목 순서를 따르고, 종목 목록에 없는 코드는 뒤에 코드 순으로 붙인다.
  const probs = recorded[recorded.length - 1].views.probs ?? {};
  const seen = new Set<string>();
  for (const k of keys) for (const t of Object.keys(probs[k] ?? {})) seen.add(t);
  const names = new Map((run.universe ?? []).map((u) => [u.ticker, u.name]));
  const ordered = [
    ...(run.universe ?? []).map((u) => u.ticker).filter((t) => seen.has(t)),
    ...[...seen].filter((t) => !names.has(t)).sort(),
  ];
  const rows: ProbRow[] = ordered.map((ticker) => ({
    ticker,
    name: names.get(ticker) ?? "",
    probs: Object.fromEntries(keys.map((k) => [k, probs[k]?.[ticker] ?? null])) as Record<ViewKey, number | null>,
  }));

  return { kind: "ok", runId, history, latest, drift, firstMove: moved?.date ?? null, changes, sources, rows };
}

// 확률 칸 바탕색. 0.5 가 중립(무채색), 위로 갈수록 상승색, 아래로 갈수록 하락색이 짙어진다.
// 0.5 에서 0.25 이상 벗어나면 가장 짙다. 글자색은 바꾸지 않는다 — 색만으로 값을 읽게 하지 않는다.
export function probTint(p: number | null, up: string, down: string): string | undefined {
  if (p === null) return undefined;
  const d = p - 0.5;
  if (Math.abs(d) < 0.005) return undefined;
  const alpha = Math.round(Math.min(1, Math.abs(d) / 0.25) * 0x40);
  return `${d > 0 ? up : down}${alpha.toString(16).padStart(2, "0")}`;
}
