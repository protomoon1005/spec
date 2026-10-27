"use client";

// 전략서 하나의 내용 — GET /specs/{spec_id}. 전략 완료 화면과 전략 관리 상세 화면이 같이 쓴다.
//
// 규칙 칸을 JSON 그대로 늘어놓지 않고 사람이 읽는 말로 바꾼다.
//   리밸런싱  {"type":"calendar","freq":"weekly"} → "매주 (최소 7일 간격)"
//   신호      market_temperature → "시장온도 — 코스피200 20일 추세 · VIX"
//   제약      cash_min 0.15 → "현금 최소 15%"
// 종목 비중 범위는 이중막대로 그린다(U05 전략 초안 이중막대) — 위 줄은 성향이 허용한 범위,
// 아래 줄은 AI 가 실제로 고른 범위. "AI 는 허용범위 안에서만 골랐다" 가 눈에 보이게 하려는 것.
// 검증기가 범위를 고쳤으면(was_adjusted) 확정 범위를 아래 줄에 겹쳐 그린다. 노란 선은
// 하드캡 종목당 상한(lib/policy.ts HARDCAP — 정책 3중 사본 대조 테스트 대상).
// 모르는 키나 모양은 버리지 않고 원래 값 그대로
// 보여 준다 — 서버가 새 규칙을 보내도 화면에서 사라지지 않게.
//
// 리밸런싱이 백테스트에서 돌 수 없는 규칙이면 미리 경고한다. 러너가 받는 규칙은
// backend/app/backtest/inputs.py 의 _SCHEDULES 두 개뿐이다(BACKTESTABLE). 러너가 늘면
// 여기도 같이 고친다. 이름표(INDICATOR_LABEL)는 BBL 지표 블록의 title 이다.

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { HARDCAP } from "@/lib/policy";

type Rules = Record<string, unknown> | null;

type SpecDetail = {
  spec_id: string;
  name: string;
  status: string;
  input_prompt: string | null;
  rebalance: Rules;
  signal_rules: Rules;
  constraint_user: Rules;
  // 전략서가 만들어질 때의 성향
  risk_level?: number | null;
  universe: {
    ticker: string;
    name: string;
    // 확정 범위(검증기가 고친 뒤). 검증 전에는 AI 원래 범위와 같다
    weight_min: number | null;
    weight_max: number | null;
    // AI 가 낸 원래 범위
    weight_min_raw?: number | null;
    weight_max_raw?: number | null;
    was_adjusted?: boolean;
    // 양식에 들어간 허용범위(기준표 행)
    risk_tag?: string | null;
    allowed_min?: number | null;
    allowed_max?: number | null;
  }[];
};

const pct = (v: number | null) => (v === null ? "-" : `${Math.round(v * 1000) / 10}%`);
const pctOf = (v: number) => `${Math.round(v * 1000) / 10}%`;

export default function SpecContents({ specId }: { specId: string }) {
  const [spec, setSpec] = useState<SpecDetail | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api<SpecDetail>(`/specs/${specId}`)
      .then(setSpec)
      .catch((err) => setMessage(`전략서를 받지 못했습니다: ${err instanceof Error ? err.message : String(err)}`));
  }, [specId]);

  if (!spec) {
    return message ? (
      <p className="flow-msg err" role="alert">
        {message}
      </p>
    ) : (
      <p className="flow-state">불러오는 중…</p>
    );
  }

  const rebalance = describeRebalance(spec.rebalance);

  return (
    <>
      <dl className="flow-kv">
        <dt>번호</dt>
        <dd className="mono">{spec.spec_id}</dd>
        <dt>이름</dt>
        <dd className="prose">{spec.name}</dd>
        <dt>상태</dt>
        <dd>
          <StatusBadge status={spec.status} />
        </dd>
        <dt>요청 문장</dt>
        <dd className="prose">{spec.input_prompt ?? "-"}</dd>
      </dl>

      <h3>담은 종목과 비중 범위 · {spec.universe.length}개</h3>
      <Holdings universe={spec.universe} />

      <h3>규칙</h3>
      <dl className="flow-kv">
        <dt>언제 비중을 맞추나</dt>
        <dd>
          {rebalance.text}
          {rebalance.backtestable === false && (
            <span className="flow-badge warn" style={{ marginLeft: 8 }}>
              이 규칙은 아직 백테스트할 수 없어요
            </span>
          )}
        </dd>
        <dt>사고팔 판단</dt>
        <dd>
          {describeSignals(spec.signal_rules).map((line) => (
            <span key={line} style={{ display: "block" }}>
              {line}
            </span>
          ))}
        </dd>
        <dt>지킬 제약</dt>
        <dd>{describeConstraints(spec.constraint_user)}</dd>
      </dl>
    </>
  );
}

// 막대의 오른쪽 끝. 허용 상한이 100%(G6)여도 AI 범위가 보이도록, 그려지는 값이 다 0.4 안이면
// 0.4 로, 넘으면 1 로 잡는다. 허용범위가 끝을 넘으면 막대 끝에 화살표를 단다.
function trackOf(universe: SpecDetail["universe"]): number {
  const drawn = universe.flatMap((u) => [u.weight_max_raw ?? u.weight_max ?? 0, u.weight_max ?? 0, HARDCAP.maxWeightPerAsset]);
  return Math.max(...drawn) <= 0.4 ? 0.4 : 1;
}

function Holdings({ universe }: { universe: SpecDetail["universe"] }) {
  const track = trackOf(universe);
  const adjusted = universe.some((u) => u.was_adjusted);
  return (
    <>
      <p className="flow-legend" aria-hidden="true">
        <span className="allowed" /> 성향이 허용한 범위 <span className="ai" /> AI가 고른 범위
        {adjusted && (
          <>
            <span className="final" /> 검증 후 확정 범위
          </>
        )}
        <span className="cap" /> 하드캡 {pctOf(HARDCAP.maxWeightPerAsset)}
      </p>
      <div className="flow-table">
        <table style={{ minWidth: 560 }}>
          <thead>
            <tr>
              <th>종목</th>
              <th style={{ width: "38%" }}>허용 · AI 범위</th>
              <th className="num">허용</th>
              <th className="num">AI</th>
            </tr>
          </thead>
          <tbody>
            {universe.map((u) => {
              const rawMin = u.weight_min_raw ?? u.weight_min;
              const rawMax = u.weight_max_raw ?? u.weight_max;
              const hasAllowed = u.allowed_min != null && u.allowed_max != null;
              return (
                <tr key={u.ticker}>
                  <td>
                    {u.name} <span className="mono flow-hint">{u.ticker}</span>
                    {u.risk_tag && (
                      <span className="flow-badge muted" style={{ marginLeft: 6 }}>
                        {u.risk_tag}
                      </span>
                    )}
                  </td>
                  <td>
                    <DualBar
                      track={track}
                      allowed={hasAllowed ? [u.allowed_min as number, u.allowed_max as number] : null}
                      ai={rawMin !== null && rawMax !== null ? [rawMin, rawMax] : null}
                      final={
                        u.was_adjusted && u.weight_min !== null && u.weight_max !== null
                          ? [u.weight_min, u.weight_max]
                          : null
                      }
                    />
                  </td>
                  <td className="num flow-hint">
                    {hasAllowed ? `${pct(u.allowed_min ?? null)} ~ ${pct(u.allowed_max ?? null)}` : "-"}
                  </td>
                  <td className="num" style={{ color: "var(--c-bright)" }}>
                    {pct(rawMin)} ~ {pct(rawMax)}
                    {u.was_adjusted && (
                      <span style={{ display: "block", color: "var(--c-warn)", fontSize: 11 }}>
                        확정 {pct(u.weight_min)} ~ {pct(u.weight_max)}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

// 위 줄: 허용범위(옅은 띠), 아래 줄: AI 범위(진한 막대) + 확정 범위(노란 테두리), 하드캡 세로선.
function DualBar({
  track,
  allowed,
  ai,
  final,
}: {
  track: number;
  allowed: [number, number] | null;
  ai: [number, number] | null;
  final: [number, number] | null;
}) {
  const at = (v: number) => `${Math.min(100, (v / track) * 100)}%`;
  const span = ([lo, hi]: [number, number]) => ({
    left: at(lo),
    width: `max(3px, calc(${at(hi)} - ${at(lo)}))`,
  });
  return (
    <span className="flow-dual" aria-hidden="true">
      <span className="row">
        {allowed && <span className="allowed" style={span(allowed)} />}
        {allowed && allowed[1] > track && <em>→ {pctOf(allowed[1])}</em>}
      </span>
      <span className="row">
        {ai && <span className="ai" style={span(ai)} />}
        {final && <span className="final" style={span(final)} />}
      </span>
      <i style={{ left: at(HARDCAP.maxWeightPerAsset) }} />
    </span>
  );
}

// 전략서 상태 표시. draft 는 아직 고칠 수 있는 상태라 주의색, 승인은 확정색.
export function StatusBadge({ status }: { status: string }) {
  const tone = status === "approved" ? "ok" : status === "draft" ? "warn" : "muted";
  return <span className={`flow-badge ${tone}`}>{status}</span>;
}

// --- 규칙을 말로 ------------------------------------------------------------

// backend/app/backtest/inputs.py _SCHEDULES 의 키 (type, freq, day). min_interval_days 도 있어야 한다.
const BACKTESTABLE = new Set(["calendar/monthly/1", "calendar/weekly/1"]);

function describeRebalance(rule: Rules): { text: string; backtestable: boolean | null } {
  if (!rule) return { text: "없음", backtestable: null };
  const trigger = (rule.trigger ?? {}) as { type?: string; freq?: string; day?: number | null };
  const interval = typeof rule.min_interval_days === "number" ? rule.min_interval_days : null;

  let what: string | null = null;
  if (trigger.type === "calendar") {
    if (trigger.freq === "weekly") what = "매주";
    else if (trigger.freq === "monthly") what = trigger.day === 1 ? "매달 첫 거래일" : trigger.day ? `매달 ${trigger.day}일` : "매달";
    else if (trigger.freq === "quarterly") what = "분기마다";
  } else if (trigger.type === "threshold") what = "비중이 정한 범위를 벗어날 때";
  else if (trigger.type === "signal") what = "신호가 바뀔 때";

  const text = what ? `${what}${interval !== null ? ` (최소 ${interval}일 간격)` : ""}` : JSON.stringify(rule);
  const key = `${trigger.type}/${trigger.freq}/${trigger.day}`;
  return { text, backtestable: BACKTESTABLE.has(key) && interval !== null };
}

const VIEW_LABEL: Record<string, string> = {
  market_temperature: "시장온도",
  market_analysis: "시장분석",
  sentiment: "뉴스 감성",
};

const INDICATOR_LABEL: Record<string, string> = {
  KOSPI200: "코스피200",
  VIX_CLOSE: "VIX",
  atr_14_pct: "평균실체범위 비율(14)",
  ma_gap_20: "20일 이격도",
  ma_gap_60: "60일 이격도",
  ret_1: "1일 수익률",
  ret_5: "5일 수익률",
  ret_20: "20일 수익률",
  rsi_14: "RSI(14)",
  vol_20: "20일 변동성",
  volume_ratio_20: "거래량 비율(20일)",
};

const label = (v: unknown) => (typeof v === "string" ? (INDICATOR_LABEL[v] ?? v) : JSON.stringify(v));

function describeSignals(rules: Rules): string[] {
  if (!rules) return ["없음"];
  const used: string[] = [];
  const unused: string[] = [];
  for (const [key, value] of Object.entries(rules)) {
    const name = VIEW_LABEL[key] ?? key;
    if (value === null || value === undefined) {
      unused.push(name);
      continue;
    }
    const v = value as Record<string, unknown>;
    let detail: string;
    if (key === "market_temperature") {
      const parts = [
        v.trend_index !== undefined &&
          `${label(v.trend_index)}${typeof v.trend_ma_window === "number" ? ` ${v.trend_ma_window}일` : ""} 추세`,
        v.volatility_index !== undefined && label(v.volatility_index),
      ].filter(Boolean);
      detail = parts.join(" · ");
    } else if (key === "market_analysis" && Array.isArray(v.indicators)) {
      detail = v.indicators.map(label).join(" · ");
    } else if (key === "sentiment") {
      const sectors = Array.isArray(v.target_sectors) && v.target_sectors.length ? v.target_sectors.join(" · ") : "전체";
      detail = `${sectors}${typeof v.lookback_hours === "number" ? ` · 최근 ${v.lookback_hours}시간` : ""}`;
    } else {
      detail = JSON.stringify(value);
    }
    used.push(`${name} — ${detail || "사용"}`);
  }
  if (unused.length) used.push(`${unused.join(" · ")} — 안 씀`);
  return used;
}

const CONSTRAINT_LABEL: Record<string, string> = {
  cash_min: "현금 최소",
  max_drawdown: "최대 낙폭",
  max_loss_per_trade: "한 번 손실 최대",
  max_weight_per_asset: "종목당 최대",
  min_weight_per_asset: "종목당 최소",
};

function describeConstraints(rules: Rules): string {
  if (!rules) return "없음";
  const entries = Object.entries(rules).filter(([, v]) => v !== null && v !== undefined);
  if (!entries.length) return "없음";
  return entries
    .map(([k, v]) => `${CONSTRAINT_LABEL[k] ?? k} ${typeof v === "number" ? pctOf(v) : JSON.stringify(v)}`)
    .join(" · ");
}
