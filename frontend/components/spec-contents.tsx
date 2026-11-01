"use client";

// 전략서 하나의 내용 — GET /specs/{strategy_id}. 전략 완료 화면과 전략 관리 상세 화면이 같이 쓴다.
//
// 규칙 칸을 JSON 그대로 늘어놓지 않고 사람이 읽는 말로 바꾼다.
//   리밸런싱  {"type":"calendar","freq":"weekly"} → "매주 (최소 7일 간격)"
//   신호      market_temperature → "시장온도 — 코스피200 20일 추세 · VIX"
//   제약      cash_min 0.15 → "현금 최소 15%"
// 종목 비중 범위는 AI 가 고른 범위와, 검증기가 고쳤으면(was_adjusted) 확정 범위를 같이 적는다.
// 모르는 키나 모양은 버리지 않고 원래 값 그대로
// 보여 준다 — 서버가 새 규칙을 보내도 화면에서 사라지지 않게.
//
// 리밸런싱이 백테스트에서 돌 수 없는 규칙이면 미리 경고한다. 러너가 받는 규칙은
// backend/app/backtest/inputs.py 의 _SCHEDULES 다섯 개(리밸런싱 블록)다(BACKTESTABLE). 러너가 늘면
// 여기도 같이 고친다. 이름표(INDICATOR_LABEL)는 BBL 지표 블록의 title 이다.

import { useEffect, useState } from "react";

import { api } from "@/lib/api";

type Rules = Record<string, unknown> | null;

type SpecDetail = {
  strategy_id: string;
  name: string;
  status: string;
  input_prompt: string | null;
  rebalance: Rules;
  signal_rules: Rules;
  constraint_user: Rules;
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
  }[];
};

const pct = (v: number | null) => (v === null ? "-" : `${Math.round(v * 1000) / 10}%`);

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
        <dd className="mono">{spec.strategy_id}</dd>
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

function Holdings({ universe }: { universe: SpecDetail["universe"] }) {
  return (
    <div className="flow-table">
      <table>
        <thead>
          <tr>
            <th>종목</th>
            <th className="num">AI 범위</th>
            <th className="num">확정 범위</th>
          </tr>
        </thead>
        <tbody>
          {universe.map((u) => (
            <tr key={u.ticker}>
              <td>
                {u.name} <span className="mono flow-hint">{u.ticker}</span>
              </td>
              <td className="num" style={{ color: "var(--c-bright)" }}>
                {pct(u.weight_min_raw ?? u.weight_min)} ~ {pct(u.weight_max_raw ?? u.weight_max)}
              </td>
              <td className="num" style={u.was_adjusted ? { color: "var(--c-warn)" } : undefined}>
                {pct(u.weight_min)} ~ {pct(u.weight_max)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// 전략서 상태 표시. draft 는 아직 고칠 수 있는 상태라 주의색, 승인은 확정색.
export function StatusBadge({ status }: { status: string }) {
  const tone = status === "approved" ? "ok" : status === "draft" ? "warn" : "muted";
  return <span className={`flow-badge ${tone}`}>{status}</span>;
}

// --- 규칙을 말로 ------------------------------------------------------------

// backend/app/backtest/inputs.py _SCHEDULES 의 키 (type, freq, day). min_interval_days 도 있어야 한다.
const BACKTESTABLE = new Set([
  "calendar/monthly/1",
  "calendar/monthly/15",
  "calendar/quarterly/1",
  "calendar/weekly/1",
  "threshold/daily/null",
]);

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

  const text = what ? `${what}${interval !== null ? ` (최소 ${interval}일 간격)` : ""}` : JSON.stringify(rule);
  // day 가 없으면 서버 키의 None 과 같게 null 로 적는다.
  const key = `${trigger.type}/${trigger.freq}/${trigger.day ?? null}`;
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
    .map(([k, v]) => `${CONSTRAINT_LABEL[k] ?? k} ${typeof v === "number" ? pct(v) : JSON.stringify(v)}`)
    .join(" · ");
}
