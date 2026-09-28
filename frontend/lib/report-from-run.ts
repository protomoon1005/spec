// 실행 한 건을 리포트가 그리는 모양으로 맞춘다.
//
// 리포트(lib/data.ts buildReport)는 정적 데모 결과 파일(data/backtest-result.json)의
// 모양을 입력으로 받는다. 이 모듈은 GET /backtest/runs/{run_id} 와 GET /specs/{id}
// 응답을 그 모양으로 옮긴다 — 둘이 같은 함수를 지나가야 같은 결과가 같은 화면이 된다.
//
// 결과 파일에만 있고 API 에는 없는 값은 정책(lib/policy.ts)에서 다시 만든다.
// 성향 이름·현금 하한·그룹캡은 전부 risk_level 하나로 정해지는 값이라 추정이 아니다.

import type {
  BacktestResult,
  ConstraintInput,
  Decision,
  Point,
  RebalanceRule,
  SignalRulesView,
  SpecMeta,
  UniverseItem,
} from "@/lib/data";
import { CASH_MIN, capsFor, HARDCAP, PROFILE_LABEL } from "@/lib/policy";

/** GET /backtest/runs/{run_id} (backend/app/routers/backtest.py BacktestRunDetail) 중 리포트가 쓰는 것 */
export type RunDetail = {
  run_id: number;
  spec_id: string;
  status: "queued" | "running" | "done" | "failed" | null;
  seed_money: number | null;
  fee_rate: number | null;
  tax_rate: number | null;
  slippage_bp: number | null;
  data_snapshot_asof: string | null;
  series: Point[] | null;
  schedule: { rule?: RebalanceRule | null; note?: string } | null;
  risk_level: number | null;
  universe: UniverseItem[] | null;
  decisions: Decision[] | null;
  control_decisions: Decision[] | null;
  reason: string | null;
};

/** GET /specs/{spec_id} (backend/app/routers/spec.py SpecDetailResponse) 중 리포트가 쓰는 것 */
export type SpecDetail = {
  spec_id: string;
  spec_version: string;
  user_id: number;
  name: string;
  created_at: string;
  input_prompt: string | null;
  rebalance: RebalanceRule | null;
  signal_rules: SignalRulesView | null;
  constraint_user: ConstraintInput | null;
};

/** 리포트를 그릴 수 없는 이유. 메시지를 화면에 그대로 보여 준다. */
export class ReportUnavailable extends Error {}

export function resultFromRun(run: RunDetail): BacktestResult {
  if (run.status === "failed") {
    throw new ReportUnavailable(`백테스트가 실패했습니다 — ${run.reason ?? "이유가 기록되지 않았습니다"}`);
  }
  if (run.status !== "done") {
    throw new ReportUnavailable(`백테스트가 아직 끝나지 않았습니다 (상태: ${run.status ?? "알 수 없음"})`);
  }

  const { series, universe, decisions, control_decisions: controlDecisions, risk_level: riskLevel } = run;
  // 종목 분류는 2026-09-29 부터 실행 기록에 남긴다. 그 전에 돌린 실행에는 없어서
  // 비중 표를 그릴 수 없다. 추정해서 채우지 않는다 — 등급을 모르면 상한도 모른다.
  if (!universe?.length) {
    throw new ReportUnavailable(
      "이 실행 기록에는 종목 분류가 남아 있지 않습니다. 2026-09-29 이전에 돌린 실행입니다 — 백테스트를 다시 돌려 주세요.",
    );
  }
  if (!series?.length || !decisions?.length || !controlDecisions?.length || riskLevel == null) {
    throw new ReportUnavailable("실행 기록에 곡선이나 결정 기록이 비어 있습니다.");
  }

  return {
    as_of: series[series.length - 1].date,
    period_start: series[0].date,
    initial: run.seed_money ?? 10_000_000,
    profile: { label: PROFILE_LABEL[riskLevel], risk_level: riskLevel, cash_min: CASH_MIN[riskLevel] },
    costs: {
      fee: run.fee_rate ?? 0,
      tax: run.tax_rate ?? 0,
      slippage: (run.slippage_bp ?? 0) / 10_000,
    },
    hardcap_max_per_asset: HARDCAP.maxWeightPerAsset,
    data_source: "price_daily",
    price_field: "종가 (시장가격, NAV 아님)",
    universe,
    caps: capsFor(riskLevel).group,
    series,
    decisions,
    control_decisions: controlDecisions,
  };
}

export function metaFromSpec(spec: SpecDetail, run: RunDetail): SpecMeta {
  // 리밸런싱 규칙은 전략서에 있다. 없으면 실행이 실제로 쓴 규칙(schedule.rule)을 쓴다.
  const rebalance = spec.rebalance ?? run.schedule?.rule;
  if (!rebalance) throw new ReportUnavailable("리밸런싱 규칙을 찾을 수 없습니다.");
  return {
    spec_id: spec.spec_id,
    spec_version: spec.spec_version,
    user_id: spec.user_id,
    name: spec.name,
    created_at: spec.created_at,
    rebalance,
    signal_rules: spec.signal_rules ?? {},
    constraint: spec.constraint_user,
    inputPrompt: spec.input_prompt,
    runId: run.run_id,
  };
}
