// 관점 브리핑(U09, /views?run_id=) 이 그리는 값을 실행 기록에서 뽑는다. 화면과 떼어 둔
// 순수 함수라 서버 없이 확인할 수 있다 (scripts/check-views.ts).
//
// 재료: GET /backtest/runs/{run_id} 의
//   decisions[i].views = { weights: {market, sentiment, regime}, probs: {관점: {종목: 보정 확률}} }
//     신호를 쓰는 전략 계열의 리밸런싱마다 러너가 남긴다 (backend/app/views/bridge.py last_views).
//     weights 는 그 판단에 실제로 쓴 관점 가중치 — 전날까지 확정된 성적으로 정해진 값이다.
//   scorer_sources = { market, sentiment, regime: "real" | "mock" | "neutral" }
//   decisions[i].views.evidence (있을 때만) — 관점별 근거. 뉴스 감성(빅카인즈 기사 · KR-FinBert-SC 채점,
//     자산군별 일 지수)은 lens_id · sector(EQUITY/BOND/COMMODITY) · n_articles · article_ids(그날 |극성|
//     상위 3건) · model_version, 그리고 z · state 가 올 예정이다. 모양이 확정되지 않아 sentimentEvidenceOf 가
//     방어적으로 읽고, 없거나 모르는 모양이면 무시한다. 기사 제목은 화면이 GET /news/articles?ids= 로 따로 받는다.
//
// node 의 타입 제거 실행이 읽을 수 있게 런타임 import 를 두지 않는다.

export type ViewKey = "market" | "sentiment" | "regime";

export const VIEWS: readonly { key: ViewKey; label: string; basis: string }[] = [
  { key: "market", label: "시장분석", basis: "가격 지표 9개 (피처셋 v0.1-ta9)" },
  { key: "sentiment", label: "뉴스 감성", basis: "빅카인즈 뉴스 기사 감성 (KR-FinBert-SC 채점, 자산군별 일 지수)" },
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
  evidence?: unknown;
};

// 뉴스 감성 근거 중 화면이 보여 주는 칸. 전부 선택 — 온 것만 그린다.
export type SentimentEvidence = {
  sector: string | null;
  lens_id: string | null;
  n_articles: number | null;
  article_ids: string[];
  model_version: string | null;
  z: number | null;
  state: string | null;
  as_of: string | null;
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
  // 마지막 리밸런싱의 뉴스 감성 근거(자산군마다 하나). 기록이 없거나 읽을 수 없는 모양이면 빈 배열
  sentimentEvidence: SentimentEvidence[];
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

  // 백엔드 bridge.last_views() 는 views.sentiment({묶음: 근거}) 에 싣는다. views.evidence.sentiment 는 옛 모양.
  const lastViews = recorded[recorded.length - 1].views as { evidence?: unknown; sentiment?: unknown };
  const sentimentEvidence = sentimentEvidenceOf(lastViews.sentiment ? { sentiment: lastViews.sentiment } : lastViews.evidence);

  return {
    kind: "ok",
    runId,
    history,
    latest,
    drift,
    firstMove: moved?.date ?? null,
    changes,
    sources,
    rows,
    sentimentEvidence,
  };
}

const EVIDENCE_KEYS = ["sector", "lens_id", "n_articles", "article_ids", "model_version", "z", "state", "as_of", "data_asof"] as const;

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function readEvidence(v: unknown): SentimentEvidence | null {
  if (!isRecord(v) || !EVIDENCE_KEYS.some((k) => k in v)) return null;
  const str = (x: unknown) => (typeof x === "string" && x ? x : typeof x === "number" && Number.isFinite(x) ? String(x) : null);
  const finite = (x: unknown) => (typeof x === "number" && Number.isFinite(x) ? x : null);
  const ids = Array.isArray(v.article_ids)
    ? v.article_ids.map((x) => str(x)).filter((x): x is string => x !== null)
    : [];
  const out: SentimentEvidence = {
    sector: str(v.sector),
    lens_id: str(v.lens_id),
    n_articles: finite(v.n_articles),
    article_ids: ids,
    model_version: str(v.model_version),
    z: finite(v.z),
    state: str(v.state),
    as_of: str(v.as_of ?? v.data_asof),
  };
  const { article_ids, ...rest } = out;
  return article_ids.length || Object.values(rest).some((x) => x !== null) ? out : null;
}

// evidence.sentiment 가 근거 하나({sector, n_articles, …})이거나 종목별 근거({종목: {…}})일 수 있다.
// 종목별이면 같은 자산군(sector) 종목은 같은 지수를 쓰므로 자산군마다 하나만 남긴다(코드 순 첫 종목).
export function sentimentEvidenceOf(evidence: unknown): SentimentEvidence[] {
  if (!isRecord(evidence)) return [];
  const e = evidence.sentiment;
  const direct = readEvidence(e);
  if (direct) return [direct];
  if (!isRecord(e)) return [];
  const bySector = new Map<string, SentimentEvidence>();
  for (const k of Object.keys(e).sort()) {
    const nested = readEvidence(e[k]);
    if (!nested) continue;
    const key = nested.sector ?? `종목 ${k}`;
    if (!bySector.has(key)) bySector.set(key, nested);
  }
  return [...bySector.values()].sort((a, b) => (a.sector ?? "~").localeCompare(b.sector ?? "~"));
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
