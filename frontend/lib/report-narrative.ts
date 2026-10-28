// 리포트 요약 문단 — LLM 을 쓰지 않는 템플릿 문장.
//
// 문장에 들어갈 수 있는 값은 NARRATIVE_FIELDS 에 적은 것뿐이다. 이 상수 하나가 화이트리스트이고,
// 타입(NarrativeValues)도 여기서 나온다. 문장 틀(sentence)은 자기가 쓰겠다고 적은 칸(uses)만 받으므로
// 화이트리스트 밖 값이나 적지 않은 칸을 문장에 넣으려 하면 타입 검사에서 막힌다.
//
//   - 값이 하나라도 없으면(null) 그 문장을 통째로 뺀다. 빈칸이나 추정값을 채우지 않는다.
//   - 숫자 옆에는 기준 시점을 붙인다(데이터 기준일 · 리밸런싱일).
//   - 관점 가중치는 실행 기록(decisions[].views.weights)에 남은 실제 값만 쓴다. 리포트의
//     "3관점 판단" 탭 그래프는 아직 모의값이라 여기 넣지 않는다.
//
// node 의 타입 제거 실행(scripts/check-narrative.ts)이 읽을 수 있게 런타임 import 를 두지 않는다.

export type ViewKey = "market" | "sentiment" | "regime";
export type ValidationSource = "stored" | "recorded" | "revalidated";

// 칸 종류 → 값 타입
type KindType = {
  date: string;
  ratio: number;
  number: number;
  count: number;
  weights: Record<ViewKey, number>;
  source: ValidationSource;
};

// --- 화이트리스트 (이 상수 하나) --------------------------------------------
export const NARRATIVE_FIELDS = {
  period_start: { kind: "date", label: "백테스트 시작일" },
  period_end: { kind: "date", label: "백테스트 종료일" },
  as_of: { kind: "date", label: "데이터 기준일 (마지막 종가)" },
  cagr: { kind: "ratio", label: "연환산 수익률 (CAGR)" },
  mdd: { kind: "ratio", label: "최대낙폭 (MDD)" },
  sharpe: { kind: "number", label: "샤프지수" },
  rebalance_count: { kind: "count", label: "리밸런싱 횟수" },
  view_weights: { kind: "weights", label: "마지막 관점 가중치 (실행 기록)" },
  view_weights_date: { kind: "date", label: "마지막 관점 가중치의 리밸런싱일" },
  bounds_adjusted_count: { kind: "count", label: "검증이 범위를 보정한 종목 수" },
  cash_min: { kind: "ratio", label: "현금 하한(전략서 · 하드캡 중 큰 값)" },
  cash_target: { kind: "ratio", label: "검증 확정 현금 목표" },
  validation_source: { kind: "source", label: "검증 확정값 출처" },
} as const satisfies Record<string, { kind: keyof KindType; label: string }>;

export type NarrativeField = keyof typeof NARRATIVE_FIELDS;

export type NarrativeValues = {
  [K in NarrativeField]: KindType[(typeof NARRATIVE_FIELDS)[K]["kind"]] | null;
};

// --- 문장 틀 ----------------------------------------------------------------

type Present<K extends NarrativeField> = { readonly [P in K]: NonNullable<NarrativeValues[P]> };

export type Sentence = {
  id: string;
  uses: readonly NarrativeField[];
  // 값이 다 있으면 문장, 아니면 null
  render: (v: NarrativeValues) => string | null;
};

// render 는 uses 에 적은 칸만, null 이 아닌 값으로 받는다.
function sentence<const K extends NarrativeField>(
  id: string,
  uses: readonly K[],
  render: (v: Present<K>) => string,
): Sentence {
  return {
    id,
    uses,
    render(values) {
      const picked: Partial<Record<K, unknown>> = {};
      for (const k of uses) {
        const x = values[k];
        if (x === null || x === undefined || (typeof x === "number" && !Number.isFinite(x))) return null;
        picked[k] = x;
      }
      return render(picked as Present<K>);
    },
  };
}

const pct = (v: number, d = 1) => {
  const s = (Math.abs(v) * 100).toFixed(d);
  return v > 0 ? `+${s}%` : v < 0 ? `−${s}%` : `${s}%`;
};
const pctPlain = (v: number, d = 1) => `${(v * 100).toFixed(d)}%`;
const num2 = (v: number) => (v < 0 ? `−${Math.abs(v).toFixed(2)}` : v.toFixed(2));

export const VIEW_LABEL: Record<ViewKey, string> = { market: "시장분석", sentiment: "뉴스 감성", regime: "시장온도" };

const SOURCE_LABEL: Record<ValidationSource, string> = {
  stored: "승인 시점에 저장한 검증 확정값",
  recorded: "마지막 검증 기록의 확정값",
  revalidated: "이번 실행에서 같은 검사를 다시 돌려 얻은 확정값",
};

export const SENTENCES: readonly Sentence[] = [
  sentence("period", ["period_start", "period_end", "rebalance_count"], (v) =>
    `${v.period_start}부터 ${v.period_end}까지 돌렸고, 그동안 리밸런싱을 ${v.rebalance_count}회 했습니다.`,
  ),
  sentence("cagr", ["cagr", "as_of"], (v) => `연환산 수익률(CAGR)은 ${pct(v.cagr)}입니다(${v.as_of} 종가 기준).`),
  sentence("risk", ["mdd", "sharpe", "as_of"], (v) =>
    `최대낙폭(MDD)은 ${pct(v.mdd)}, 샤프지수는 ${num2(v.sharpe)}입니다(${v.as_of} 종가 기준).`,
  ),
  sentence("views", ["view_weights", "view_weights_date"], (v) => {
    const keys: ViewKey[] = ["market", "sentiment", "regime"];
    const parts = keys.map((k) => `${VIEW_LABEL[k]} ${pctPlain(v.view_weights[k])}`);
    return `마지막 리밸런싱(${v.view_weights_date})에 쓴 관점 가중치는 ${parts.join(", ")}입니다.`;
  }),
  sentence("bounds", ["bounds_adjusted_count", "cash_min", "cash_target", "as_of"], (v) => {
    const raised = v.cash_target > v.cash_min + 1e-9;
    const ranges =
      v.bounds_adjusted_count > 0
        ? `검증 단계가 ${v.bounds_adjusted_count}종목의 비중 범위를 보정했고`
        : "검증 단계가 비중 범위를 보정하지 않았고";
    const cash = raised
      ? `현금 목표를 현금 하한 ${pctPlain(v.cash_min, 0)}에서 ${pctPlain(v.cash_target, 0)}로 올렸습니다`
      : `현금 목표는 현금 하한 그대로 ${pctPlain(v.cash_target, 0)}입니다`;
    return `${ranges}, ${cash}(${v.as_of} 실행 기준).`;
  }),
  sentence("source", ["validation_source"], (v) => `비중 범위와 현금 목표는 ${SOURCE_LABEL[v.validation_source]}을 썼습니다.`),
];

export type NarrativeLine = { id: string; text: string; uses: readonly NarrativeField[] };

export function narrate(values: NarrativeValues, sentences: readonly Sentence[] = SENTENCES): NarrativeLine[] {
  const out: NarrativeLine[] = [];
  for (const s of sentences) {
    const text = s.render(values);
    if (text !== null) out.push({ id: s.id, text, uses: s.uses });
  }
  return out;
}

// --- 리포트 · 실행 기록 → 화이트리스트 값 ------------------------------------
// 리포트 전체(Report)가 아니라 필요한 것만 받는다. 여기서 고른 값 말고는 문장에 들어갈 길이 없다.

export type NarrativeSource = {
  report: {
    PERIOD_START: string;
    PERIOD_END: string;
    AS_OF: string;
    strategy: { cagr: number; mdd: number; sharpe: number };
    rebalanceCount: number;
    // 실행 기록의 m2.cash_target. 없으면 null — 성향 하한으로 채우지 않는다
    cashTarget: number | null;
    // 확정값으로 돈 실행에서만 있다(정적 데모는 null)
    feasibility: { adjusted: { ranges: number; cashFrom: number } | null };
  };
  // GET /backtest/runs/{id} 에서. 정적 데모에는 없다.
  run?: {
    decisions?: { date: string; views?: { weights?: Partial<Record<ViewKey, number>> | null } | null }[] | null;
    validation?: { status?: string; source?: string } | null;
  } | null;
};

const finite = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export function narrativeValues(src: NarrativeSource): NarrativeValues {
  const r = src.report;
  const adjusted = r.feasibility.adjusted;

  // 관점 가중치: 세 관점이 모두 기록된 마지막 결정
  let viewWeights: Record<ViewKey, number> | null = null;
  let viewDate: string | null = null;
  const decisions = src.run?.decisions ?? [];
  for (let i = decisions.length - 1; i >= 0; i--) {
    const w = decisions[i].views?.weights;
    const m = finite(w?.market);
    const s = finite(w?.sentiment);
    const g = finite(w?.regime);
    if (m !== null && s !== null && g !== null) {
      viewWeights = { market: m, sentiment: s, regime: g };
      viewDate = decisions[i].date;
      break;
    }
  }

  const validation = src.run?.validation;
  const source =
    validation?.status === "passed" &&
    (validation.source === "stored" || validation.source === "recorded" || validation.source === "revalidated")
      ? validation.source
      : null;

  return {
    period_start: r.PERIOD_START || null,
    period_end: r.PERIOD_END || null,
    as_of: r.AS_OF || null,
    cagr: finite(r.strategy.cagr),
    mdd: finite(r.strategy.mdd),
    sharpe: finite(r.strategy.sharpe),
    rebalance_count: finite(r.rebalanceCount),
    view_weights: viewWeights,
    view_weights_date: viewDate,
    bounds_adjusted_count: adjusted ? adjusted.ranges : null,
    cash_min: adjusted ? adjusted.cashFrom : null,
    cash_target: adjusted ? finite(r.cashTarget) : null,
    validation_source: source,
  };
}
