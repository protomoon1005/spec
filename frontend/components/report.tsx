"use client";

import { useState, type ReactNode } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  bestMonth,
  benchmark,
  capLogs,
  COST_MODEL,
  drawdownSeries,
  ETA,
  EVAL_WINDOW,
  excess,
  FEATURESET,
  folds,
  GROUP_CAPS,
  GROUP_LABEL,
  HARDCAP,
  constraintResolved,
  INITIAL,
  integratedProb,
  integratedSignal,
  lastRebalance,
  monthlyReturns,
  num,
  PERIOD_END,
  PERIOD_START,
  pct,
  pctPlain,
  pp,
  PROFILE,
  rebalanceRows,
  SEED,
  series,
  SNAPSHOT,
  spec,
  strategy,
  targetCash,
  VIEW_META,
  viewWeightHistory,
  viewWeightOf,
  weightRows,
  W_FLOOR,
  won,
  worstMonth,
  ym,
} from "@/lib/backtest-mock";

// --- 디자인 토큰 -------------------------------------------------------------
const C = {
  bg: "#0b0f14",
  surface: "#131920",
  surface2: "#1a2330",
  border: "#1e2d3d",
  grid: "#22344a",
  text: "#c9d6e3",
  bright: "#e2ecf5",
  muted: "#7f9ab5",
  dim: "#4d6a84",
  profit: "#00e676",
  loss: "#ff5c7a",
  accent: "#3d9bff",
  warn: "#ffb020",
};

const MONO = "var(--font-mono)";
const SANS = "var(--font-sans)";

const sign = (n: number) => (n >= 0 ? C.profit : C.loss);

// --- 공통 조각 ---------------------------------------------------------------
function Panel({
  title,
  sub,
  source,
  children,
}: {
  title: string;
  sub?: string;
  source?: string;
  children: ReactNode;
}) {
  return (
    <section style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <div
        style={{ padding: "14px 20px", borderBottom: `1px solid ${C.border}` }}
        className="flex items-baseline gap-3 flex-wrap"
      >
        <h2
          style={{
            fontFamily: MONO,
            fontSize: 12,
            fontWeight: 600,
            color: C.bright,
            letterSpacing: "0.06em",
            margin: 0,
          }}
        >
          {title}
        </h2>
        {sub && <span style={{ fontFamily: SANS, fontSize: 12, color: C.muted }}>{sub}</span>}
      </div>
      <div style={{ padding: "16px 20px" }}>{children}</div>
      {source && (
        <div
          style={{
            padding: "8px 20px",
            borderTop: `1px solid ${C.border}`,
            fontFamily: MONO,
            fontSize: 10,
            color: C.muted,
            letterSpacing: "0.03em",
          }}
        >
          {source}
        </div>
      )}
    </section>
  );
}

function Kpi({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
}) {
  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}` }} className="p-4 flex flex-col gap-1">
      <span
        style={{ fontFamily: SANS, color: C.muted, fontSize: 11, letterSpacing: "0.06em" }}
      >
        {label}
      </span>
      <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 600, color: color ?? C.bright, lineHeight: 1.15 }}>
        {value}
      </span>
      {sub && <span style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}>{sub}</span>}
    </div>
  );
}

type Col = { key: string; label: string; align: "left" | "right" };

function DataTable({ cols, children, minWidth = 620 }: { cols: Col[]; children: ReactNode; minWidth?: number }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", minWidth }}>
        <thead>
          <tr style={{ borderBottom: `1px solid ${C.border}` }}>
            {cols.map((c) => (
              <th
                key={c.key}
                scope="col"
                style={{
                  fontFamily: MONO,
                  fontSize: 10,
                  color: C.muted,
                  letterSpacing: "0.06em",
                  padding: "10px 14px",
                  textAlign: c.align,
                  fontWeight: 500,
                  whiteSpace: "nowrap",
                }}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function Cel({
  children,
  align = "right",
  color,
  bold,
}: {
  children: ReactNode;
  align?: "left" | "right";
  color?: string;
  bold?: boolean;
}) {
  return (
    <td
      style={{
        padding: "11px 14px",
        textAlign: align,
        fontFamily: MONO,
        fontSize: 13,
        fontWeight: bold ? 600 : 400,
        color: color ?? C.text,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </td>
  );
}

function Tag({ text, color }: { text: string; color: string }) {
  return (
    <span
      style={{
        fontFamily: MONO,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: "0.05em",
        padding: "2px 7px",
        color,
        background: `${color}14`,
        border: `1px solid ${color}40`,
        whiteSpace: "nowrap",
      }}
    >
      {text}
    </span>
  );
}

function KeyValue({ rows }: { rows: [string, string, string?][] }) {
  return (
    <div className="flex flex-col gap-2">
      {rows.map(([k, v, color]) => (
        <div key={k} className="flex items-baseline justify-between gap-4 flex-wrap">
          <span style={{ fontFamily: SANS, fontSize: 13, color: C.muted, whiteSpace: "nowrap" }}>{k}</span>
          <span
            style={{
              fontFamily: MONO,
              fontSize: 13,
              fontWeight: 500,
              color: color ?? C.text,
              textAlign: "right",
              marginLeft: "auto",
            }}
          >
            {v}
          </span>
        </div>
      ))}
    </div>
  );
}

// --- 차트 툴팁 ---------------------------------------------------------------
function TipShell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ background: C.surface2, border: `1px solid ${C.grid}`, padding: "9px 13px" }}>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted, marginBottom: 5 }}>{label}</div>
      {children}
    </div>
  );
}

type TipProps = { active?: boolean; label?: string | number; payload?: { name?: string; value?: number; color?: string }[] };

function EquityTip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <TipShell label={String(label)}>
      {payload.map((p) => (
        <div key={p.name} style={{ fontFamily: MONO, fontSize: 12, color: p.color, marginBottom: 2 }}>
          {p.name}: {won(p.value ?? 0)}
        </div>
      ))}
    </TipShell>
  );
}

function DdTip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <TipShell label={String(label)}>
      <div style={{ fontFamily: MONO, fontSize: 12, color: C.loss }}>
        낙폭 {(payload[0].value ?? 0).toFixed(2)}%
      </div>
    </TipShell>
  );
}

function MonthTip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;
  const v = payload[0].value ?? 0;
  return (
    <TipShell label={String(label)}>
      <div style={{ fontFamily: MONO, fontSize: 12, color: sign(v) }}>
        {v >= 0 ? "+" : "−"}
        {Math.abs(v).toFixed(2)}%
      </div>
    </TipShell>
  );
}

function ViewTip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <TipShell label={String(label)}>
      {payload.map((p) => (
        <div key={p.name} style={{ fontFamily: MONO, fontSize: 12, color: p.color, marginBottom: 2 }}>
          {p.name}: {((p.value ?? 0) * 100).toFixed(1)}%
        </div>
      ))}
    </TipShell>
  );
}

const axisTick = { fontFamily: MONO, fontSize: 10, fill: C.muted };

// --- 허용범위 밴드 -----------------------------------------------------------
const TRACK = 0.4;

function WeightBand({
  min,
  max,
  policyCap,
  raw,
  final,
}: {
  min: number;
  max: number;
  policyCap: number;
  raw: number;
  final: number;
}) {
  const p = (v: number) => `${Math.min(100, (v / TRACK) * 100)}%`;
  return (
    <div style={{ position: "relative", height: 24, minWidth: 190, background: "#0e141b", border: `1px solid ${C.border}` }}>
      {/* Spec 허용밴드 */}
      <div
        style={{
          position: "absolute",
          left: p(min),
          width: `calc(${p(max)} - ${p(min)})`,
          top: 0,
          bottom: 0,
          background: "rgba(61,155,255,0.12)",
          borderLeft: `1px solid ${C.accent}55`,
          borderRight: `1px solid ${C.accent}55`,
        }}
      />
      {/* 성향 상한 */}
      {policyCap <= TRACK && (
        <div style={{ position: "absolute", left: p(policyCap), top: 0, bottom: 0, width: 1, background: C.warn, opacity: 0.85 }} />
      )}
      {/* 신호 기준 비중 (캡 적용 전) */}
      <div style={{ position: "absolute", left: p(raw), top: 3, bottom: 3, width: 1, background: C.muted }} />
      {/* 최종 목표비중 */}
      <div style={{ position: "absolute", left: 0, width: p(final), top: 8, bottom: 8, background: C.accent }} />
      <div style={{ position: "absolute", left: `calc(${p(final)} - 1px)`, top: 1, bottom: 1, width: 2, background: C.bright }} />
    </div>
  );
}

// --- 탭 ----------------------------------------------------------------------
const TABS = [
  { id: "overview", label: "성과 개요" },
  { id: "weights", label: "비중과 리밸런싱" },
  { id: "views", label: "3관점 판단" },
  { id: "walkforward", label: "워크포워드" },
  { id: "spec", label: "전략 Spec" },
] as const;

type TabId = (typeof TABS)[number]["id"];

// --- 화면 --------------------------------------------------------------------
export default function Report() {
  const [tab, setTab] = useState<TabId>("overview");
  const [note, setNote] = useState("");

  const provenance = `데이터 스냅샷 ${SNAPSHOT} 종가 · 피처셋 ${FEATURESET} · seed ${SEED} · 비용모델 수수료 ${(COST_MODEL.fee * 100).toFixed(3)}% / 세금 ${(COST_MODEL.tax * 100).toFixed(0)}% / 슬리피지 ${(COST_MODEL.slippage * 10000).toFixed(0)}bp`;
  const equitySource = `출처: KRX 일별시세 · 기준시점 ${SNAPSHOT} 종가 · 벤치마크 069500 KODEX 200 · ${provenance}`;

  return (
    <div
      style={{ background: C.bg, fontFamily: SANS, color: C.text, minHeight: "100vh" }}
      className="report-root flex flex-col"
    >
      {/* 헤더 */}
      <header style={{ borderBottom: `1px solid ${C.border}`, background: C.bg }} className="sticky top-0 z-10">
        <div className="max-w-[1400px] mx-auto px-6 py-4 flex items-start justify-between gap-6 flex-wrap">
          <div className="flex items-start gap-4">
            <div style={{ width: 8, height: 8, borderRadius: "50%", background: C.warn, boxShadow: `0 0 8px ${C.warn}`, marginTop: 6 }} />
            <div>
              <div className="flex items-center gap-3 flex-wrap">
                <span style={{ fontFamily: MONO, fontSize: 15, fontWeight: 600, color: C.bright, letterSpacing: "0.02em" }}>
                  {spec.name}
                </span>
                <Tag text={`${spec.spec_id} v${spec.spec_version}`} color={C.accent} />
                <Tag text={`승인 대기`} color={C.warn} />
              </div>
              <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted, marginTop: 5 }}>
                {PERIOD_START} → {PERIOD_END} · 국내 상장 ETF {weightRows.length}종목 · 월간 리밸런싱 · {PROFILE.label}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-5 flex-wrap">
            <div style={{ fontFamily: MONO, fontSize: 12 }}>
              <span style={{ color: C.muted }}>시드머니 </span>
              <span style={{ color: C.text }}>{won(INITIAL)}</span>
            </div>
            <div style={{ fontFamily: MONO, fontSize: 12 }}>
              <span style={{ color: C.muted }}>최종 평가액 </span>
              <span style={{ color: sign(strategy.total) }}>{won(strategy.final)}</span>
            </div>
            <div
              style={{
                padding: "5px 12px",
                border: `1px solid ${sign(strategy.total)}40`,
                background: `${sign(strategy.total)}12`,
                fontFamily: MONO,
                fontSize: 13,
                fontWeight: 600,
                color: sign(strategy.total),
              }}
            >
              {pct(strategy.total, 1)}
            </div>
          </div>
        </div>

        <div
          style={{
            borderTop: `1px solid ${C.border}`,
            background: "#0e141b",
            fontFamily: MONO,
            fontSize: 10,
            color: C.muted,
            letterSpacing: "0.03em",
          }}
        >
          <div className="max-w-[1400px] mx-auto px-6 py-2">{provenance}</div>
        </div>
      </header>

      <main className="max-w-[1400px] mx-auto w-full px-6 py-6 flex flex-col gap-6">
        {/* 핵심 지표 */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(168px, 1fr))", gap: 1 }}>
          <Kpi label="누적수익률" value={pct(strategy.total, 1)} color={sign(strategy.total)} sub={`3년 · ${PERIOD_START.slice(0, 7)}~`} />
          <Kpi label="연환산 (CAGR)" value={pct(strategy.cagr, 1)} color={sign(strategy.cagr)} sub="기하평균" />
          <Kpi label="벤치마크 대비" value={pp(excess, 1)} color={sign(excess)} sub={`KODEX 200 ${pct(benchmark.total, 1)}`} />
          <Kpi label="최대낙폭 (MDD)" value={pct(strategy.mdd, 1)} color={C.loss} sub={`제약 상한 ${pctPlain(spec.constraint.max_drawdown, 0)}`} />
          <Kpi label="샤프지수" value={num(strategy.sharpe)} sub={`무위험 ${pctPlain(0.025, 1)} 기준`} />
          <Kpi label="소르티노" value={num(strategy.sortino)} sub="하방편차 기준" />
          <Kpi label="칼마지수" value={num(strategy.calmar)} sub="CAGR / |MDD|" />
          <Kpi label="연변동성" value={pctPlain(strategy.vol, 1)} sub={`벤치마크 ${pctPlain(benchmark.vol, 1)}`} />
        </div>

        {/* 탭 */}
        <div role="tablist" aria-label="리포트 구획" style={{ borderBottom: `1px solid ${C.border}` }} className="flex flex-wrap">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`panel-${t.id}`}
              onClick={() => setTab(t.id)}
              style={{
                fontFamily: MONO,
                fontSize: 12,
                letterSpacing: "0.04em",
                padding: "10px 18px",
                background: "transparent",
                border: "none",
                borderBottom: `2px solid ${tab === t.id ? C.accent : "transparent"}`,
                color: tab === t.id ? C.accent : C.muted,
                cursor: "pointer",
              }}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* 성과 개요 */}
        {tab === "overview" && (
          <div role="tabpanel" id="panel-overview" aria-labelledby="tab-overview" className="flex flex-col gap-5">
            <Panel title="자산곡선" sub="전략 vs 벤치마크(KODEX 200 매수 후 보유)" source={equitySource}>
              <ResponsiveContainer width="100%" height={280}>
                <AreaChart data={series} margin={{ top: 4, right: 20, left: 8, bottom: 0 }}>
                  <defs>
                    <linearGradient id="gEq" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={C.accent} stopOpacity={0.2} />
                      <stop offset="95%" stopColor={C.accent} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                  <XAxis dataKey="date" tickFormatter={ym} tick={axisTick} tickLine={false} axisLine={{ stroke: C.border }} interval={25} minTickGap={24} />
                  <YAxis
                    domain={["auto", "auto"]}
                    tickFormatter={(v: number) => `${(v / 10000).toFixed(0)}만`}
                    tick={axisTick}
                    tickLine={false}
                    axisLine={false}
                    width={52}
                  />
                  <Tooltip content={<EquityTip />} />
                  <Legend
                    verticalAlign="top"
                    align="right"
                    height={26}
                    wrapperStyle={{ fontFamily: MONO, fontSize: 11, color: C.muted }}
                  />
                  <Area isAnimationActive={false} type="monotone" dataKey="benchmark" name="벤치마크" stroke={C.dim} strokeWidth={1.2} fill="none" dot={false} />
                  <Area isAnimationActive={false} type="monotone" dataKey="equity" name="전략" stroke={C.accent} strokeWidth={2} fill="url(#gEq)" dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </Panel>

            <Panel title="낙폭" sub="고점 대비 하락률" source={equitySource}>
              <ResponsiveContainer width="100%" height={170}>
                <AreaChart data={drawdownSeries} margin={{ top: 4, right: 20, left: 8, bottom: 8 }}>
                  <defs>
                    <linearGradient id="gDd" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={C.loss} stopOpacity={0.35} />
                      <stop offset="95%" stopColor={C.loss} stopOpacity={0.04} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                  <XAxis dataKey="date" tickFormatter={ym} tick={axisTick} tickLine={false} axisLine={{ stroke: C.border }} interval={25} minTickGap={24} />
                  <YAxis domain={[Math.min(-22, Math.floor(strategy.mdd * 115)), 0]} tickFormatter={(v: number) => `${v}%`} tick={axisTick} tickLine={false} axisLine={false} width={52} />
                  <Tooltip content={<DdTip />} />
                  <ReferenceLine y={0} stroke={C.border} />
                  <ReferenceLine
                    y={-spec.constraint.max_drawdown * 100}
                    stroke={C.warn}
                    strokeDasharray="4 4"
                    label={{ value: `제약 상한 ${pctPlain(spec.constraint.max_drawdown, 0)}`, fill: C.warn, fontSize: 10, fontFamily: MONO, position: "insideBottomLeft" }}
                  />
                  <Area isAnimationActive={false} type="monotone" dataKey="drawdown" stroke={C.loss} strokeWidth={1.5} fill="url(#gDd)" dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </Panel>

            <Panel title="월별 수익률" sub="최근 18개월" source={equitySource}>
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={monthlyReturns} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                  <XAxis dataKey="month" tickFormatter={ym} tick={axisTick} tickLine={false} axisLine={{ stroke: C.border }} />
                  <YAxis tickFormatter={(v: number) => `${v}%`} tick={axisTick} tickLine={false} axisLine={false} width={44} />
                  <Tooltip content={<MonthTip />} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
                  <ReferenceLine y={0} stroke={C.grid} />
                  <Bar dataKey="ret" isAnimationActive={false} radius={[2, 2, 0, 0]}>
                    {monthlyReturns.map((m) => (
                      <Cell key={m.month} fill={sign(m.ret)} fillOpacity={0.82} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </Panel>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 20 }}>
              <Panel title="수익" source={`기준시점 ${SNAPSHOT} · 비용 차감 후`}>
                <KeyValue
                  rows={[
                    ["누적수익률", pct(strategy.total), sign(strategy.total)],
                    ["연환산 수익률 (CAGR)", pct(strategy.cagr), sign(strategy.cagr)],
                    ["벤치마크 누적", pct(benchmark.total), sign(benchmark.total)],
                    ["초과수익", pp(excess), sign(excess)],
                    [`최고 월 (${ym(bestMonth.month)})`, `+${bestMonth.ret.toFixed(2)}%`, C.profit],
                    [`최저 월 (${ym(worstMonth.month)})`, `${worstMonth.ret.toFixed(2)}%`, C.loss],
                  ]}
                />
              </Panel>
              <Panel title="위험" source={`기준시점 ${SNAPSHOT} · 주간 수익률 기준 연환산`}>
                <KeyValue
                  rows={[
                    ["최대낙폭 (MDD)", pct(strategy.mdd), C.loss],
                    ["벤치마크 MDD", pct(benchmark.mdd), C.loss],
                    ["연변동성", pctPlain(strategy.vol)],
                    ["샤프지수", num(strategy.sharpe)],
                    ["소르티노지수", num(strategy.sortino)],
                    ["칼마지수", num(strategy.calmar)],
                  ]}
                />
              </Panel>
            </div>
          </div>
        )}

        {/* 비중과 리밸런싱 */}
        {tab === "weights" && (
          <div role="tabpanel" id="panel-weights" aria-labelledby="tab-weights" className="flex flex-col gap-5">
            <Panel
              title="허용범위와 목표비중"
              sub={`${PROFILE.label} · 1단 사전 허용범위 안에서 2단 Spec 범위, 그 안에서 신호가 결정한 지점`}
              source={`1단 출처: ${PROFILE.source} · 2단: ${spec.spec_id} v${spec.spec_version} · 신호 기준시점 ${SNAPSHOT}`}
            >
              <div className="flex items-center gap-5 flex-wrap" style={{ fontFamily: MONO, fontSize: 10, color: C.muted, marginBottom: 14 }}>
                <span className="flex items-center gap-2">
                  <i style={{ width: 14, height: 8, background: "rgba(61,155,255,0.25)", border: `1px solid ${C.accent}55` }} /> Spec 허용밴드
                </span>
                <span className="flex items-center gap-2">
                  <i style={{ width: 2, height: 12, background: C.warn }} /> 성향 상한
                </span>
                <span className="flex items-center gap-2">
                  <i style={{ width: 2, height: 12, background: C.muted }} /> 신호 기준(캡 적용 전)
                </span>
                <span className="flex items-center gap-2">
                  <i style={{ width: 14, height: 6, background: C.accent }} /> 최종 목표비중
                </span>
                <span>눈금 0 ~ {pctPlain(TRACK, 0)}</span>
              </div>

              <DataTable
                cols={[
                  { key: "t", label: "종목", align: "left" },
                  { key: "g", label: "등급", align: "left" },
                  { key: "b", label: "허용범위 밴드", align: "left" },
                  { key: "band", label: "범위", align: "right" },
                  { key: "s", label: "신호 s", align: "right" },
                  { key: "w", label: "목표비중", align: "right" },
                  { key: "c", label: "캡", align: "right" },
                ]}
              >
                {weightRows.map((r) => (
                  <tr key={r.asset.ticker} style={{ borderBottom: `1px solid ${C.border}` }}>
                    <td style={{ padding: "11px 14px", textAlign: "left" }}>
                      <div style={{ fontFamily: MONO, fontSize: 13, color: C.bright, whiteSpace: "nowrap" }}>{r.asset.name}</div>
                      <div style={{ fontFamily: MONO, fontSize: 10, color: C.muted, whiteSpace: "nowrap" }}>
                        {r.asset.ticker} · {GROUP_LABEL[r.asset.assetGroup]} · {GROUP_LABEL[r.asset.sectorGroup]} · {GROUP_LABEL[r.asset.countryGroup]}
                      </div>
                    </td>
                    <Cel align="left">
                      <Tag text={r.asset.grade} color={C.muted} />
                    </Cel>
                    <td style={{ padding: "11px 14px" }}>
                      <WeightBand
                        min={r.bound.min}
                        max={r.bound.max}
                        policyCap={PROFILE.gradeCap[r.asset.grade]}
                        raw={r.normalized}
                        final={r.final}
                      />
                    </td>
                    <Cel color={C.muted}>
                      {pctPlain(r.bound.min, 0)} ~ {pctPlain(r.bound.max, 0)}
                    </Cel>
                    <Cel color={sign(r.asset.signal)}>
                      {r.asset.signal >= 0 ? "+" : "−"}
                      {Math.abs(r.asset.signal).toFixed(2)}
                    </Cel>
                    <Cel bold color={C.bright}>
                      {pctPlain(r.final)}
                    </Cel>
                    <Cel>{r.capped ? <Tag text={r.capped} color={C.warn} /> : <span style={{ color: C.dim }}>—</span>}</Cel>
                  </tr>
                ))}
                <tr>
                  <Cel align="left" color={C.muted}>
                    현금
                  </Cel>
                  <Cel align="left" color={C.dim}>
                    —
                  </Cel>
                  <Cel align="left" color={C.dim}>
                    —
                  </Cel>
                  <Cel color={C.muted}>하한 {pctPlain(PROFILE.cashMin, 0)}</Cel>
                  <Cel color={C.dim}>—</Cel>
                  <Cel bold color={C.bright}>
                    {pctPlain(targetCash)}
                  </Cel>
                  <Cel color={C.dim}>잔여 흡수</Cel>
                </tr>
              </DataTable>
            </Panel>

            <Panel
              title="그룹 캡 적용 내역"
              sub="상위 자산군을 먼저 적용하고 하위 국가·섹터를 적용한다"
              source={`출처: db/seeds/03_asset_groups.sql · group_caps(preset_version=v0.1) · EQUITY ${pctPlain(GROUP_CAPS.EQUITY, 0)} / BOND ${pctPlain(GROUP_CAPS.BOND, 0)} / COMMODITY ${pctPlain(GROUP_CAPS.COMMODITY, 0)} / 단일 국가 ${pctPlain(GROUP_CAPS.COUNTRY_KR, 0)} / 단일 섹터 ${pctPlain(GROUP_CAPS.SECTOR_SEMICONDUCTOR, 0)}`}
            >
              {capLogs.length === 0 ? (
                <div style={{ fontFamily: SANS, fontSize: 13, color: C.muted }}>적용된 캡이 없습니다.</div>
              ) : (
                <div className="flex flex-col gap-3">
                  {capLogs.map((l) => (
                    <div
                      key={`${l.stage}-${l.groupId}`}
                      style={{ background: "#0e141b", border: `1px solid ${C.border}`, borderLeft: `2px solid ${C.warn}`, padding: "10px 14px" }}
                    >
                      <div style={{ fontFamily: MONO, fontSize: 11, color: C.warn, marginBottom: 4 }}>
                        {l.stage} · {l.label} ({l.groupId})
                      </div>
                      <div style={{ fontFamily: SANS, fontSize: 13, color: C.text }}>
                        신호대로면 {pctPlain(l.before)}인데 상한 {pctPlain(l.cap, 0)}에 걸려 묶음 안에서 {num(l.factor, 4)}배로 비례
                        축소했습니다.
                      </div>
                    </div>
                  ))}
                  <div style={{ fontFamily: SANS, fontSize: 13, color: C.muted }}>
                    축소로 남은 {pctPlain(targetCash - PROFILE.cashMin)}는 현금으로 보냈습니다. 현금 하한{" "}
                    {pctPlain(PROFILE.cashMin, 0)}을 만족합니다.
                  </div>
                  <div
                    style={{
                      background: "#0e141b",
                      border: `1px solid ${C.border}`,
                      borderLeft: `2px solid ${C.dim}`,
                      padding: "10px 14px",
                      fontFamily: SANS,
                      fontSize: 13,
                      color: C.muted,
                      lineHeight: 1.7,
                    }}
                  >
                    <b style={{ color: C.text }}>미적용 · SECTOR_OTHER(기타) 30%</b> — 시드의 섹터 그룹은 산업 섹터
                    6개와 기타 하나뿐이라, 시장대표·국채·원자재 ETF가 전부 기타 한 바구니에 들어갑니다. 이
                    유니버스에서 기타 합은 {pctPlain(
                      weightRows
                        .filter((r) => r.asset.sectorGroup === "SECTOR_OTHER")
                        .reduce((a, r) => a + r.final, 0),
                    )}
                    이라 캡을 그대로 걸면 항상 걸리고 잔여가 전부 현금으로 빠집니다. 팀 확정 전까지 적용하지
                    않았습니다.
                  </div>
                </div>
              )}
            </Panel>

            <Panel
              title="범위 실행가능성 검사"
              sub="Σ weight_min ≤ 1 − cash_min ≤ Σ weight_max"
              source="검증 계층 3단계 · 논리 검증 로그"
            >
              <KeyValue
                rows={[
                  ["Σ weight_min", pctPlain(weightRows.reduce((s, r) => s + r.bound.min, 0)), C.text],
                  ["1 − cash_min", pctPlain(1 - PROFILE.cashMin), C.text],
                  ["Σ weight_max", pctPlain(weightRows.reduce((s, r) => s + r.bound.max, 0)), C.text],
                  ["자동 보정", "없음 (조건 충족)", C.profit],
                ]}
              />
            </Panel>

            <Panel
              title="리밸런싱 이력"
              sub={`${lastRebalance.date} · ${lastRebalance.trigger} · 최소 간격 ${lastRebalance.minInterval}일`}
              source={`평가액 ${won(strategy.final)} 기준 · 체결가정: 익일 시가 · 슬리피지 ${(COST_MODEL.slippage * 10000).toFixed(0)}bp`}
            >
              <DataTable
                cols={[
                  { key: "n", label: "종목", align: "left" },
                  { key: "t", label: "코드", align: "left" },
                  { key: "b", label: "이전 비중", align: "right" },
                  { key: "g", label: "목표 비중", align: "right" },
                  { key: "d", label: "변화", align: "right" },
                  { key: "s", label: "방향", align: "right" },
                  { key: "a", label: "체결 금액", align: "right" },
                ]}
              >
                {rebalanceRows.map((r) => {
                  const diff = r.target - r.before;
                  return (
                    <tr key={r.ticker} style={{ borderBottom: `1px solid ${C.border}` }}>
                      <Cel align="left" color={C.bright}>
                        {r.name}
                      </Cel>
                      <Cel align="left" color={C.muted}>
                        {r.ticker}
                      </Cel>
                      <Cel color={C.muted}>{pctPlain(r.before)}</Cel>
                      <Cel bold>{pctPlain(r.target)}</Cel>
                      <Cel color={sign(diff)}>{pp(diff)}</Cel>
                      <Cel>
                        <Tag text={diff >= 0 ? "매수" : "매도"} color={diff >= 0 ? C.profit : C.loss} />
                      </Cel>
                      <Cel color={sign(diff)}>
                        {diff >= 0 ? "+" : "−"}
                        {won(Math.abs(r.amount))}
                      </Cel>
                    </tr>
                  );
                })}
              </DataTable>
            </Panel>
          </div>
        )}

        {/* 3관점 판단 */}
        {tab === "views" && (
          <div role="tabpanel" id="panel-views" aria-labelledby="tab-views" className="flex flex-col gap-5">
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 20 }}>
              {VIEW_META.map((v) => {
                const w = viewWeightOf[v.key];
                return (
                  <Panel key={v.key} title={v.label} sub={v.kind}>
                    <div className="flex items-baseline gap-3" style={{ marginBottom: 12 }}>
                      <span style={{ fontFamily: MONO, fontSize: 26, fontWeight: 600, color: v.prob >= 0.5 ? C.profit : C.loss }}>
                        {(v.prob * 100).toFixed(0)}%
                      </span>
                      <span style={{ fontFamily: SANS, fontSize: 12, color: C.muted }}>보정된 상승 확률</span>
                    </div>
                    <div style={{ height: 6, background: "#0e141b", border: `1px solid ${C.border}`, marginBottom: 12 }}>
                      <div style={{ width: `${w * 100}%`, height: "100%", background: C.accent }} />
                    </div>
                    <KeyValue
                      rows={[
                        ["현재 가중치", pctPlain(w), C.bright],
                        [`Brier 손실 (${EVAL_WINDOW}일 평균)`, num(v.meanLoss, 3)],
                      ]}
                    />
                    <div style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid ${C.border}` }}>
                      <div style={{ fontFamily: SANS, fontSize: 12, color: C.muted, marginBottom: 5 }}>판단 근거</div>
                      <div style={{ fontFamily: MONO, fontSize: 11, color: C.text, lineHeight: 1.6, wordBreak: "break-word" }}>
                        {v.basis}
                      </div>
                    </div>
                  </Panel>
                );
              })}
            </div>

            <Panel
              title="통합 신호"
              sub="가중 합산 후 [-1, +1] 로 변환"
              source={`가중치는 Spec 밖 운용 상태 · 전날까지 확정된 성과만 반영 · 갱신식 Hedge (η=${ETA}, 하한 ${pctPlain(W_FLOOR, 0)}, 윈도우 ${EVAL_WINDOW}거래일)`}
            >
              <div style={{ fontFamily: MONO, fontSize: 13, color: C.text, lineHeight: 1.9 }}>
                {VIEW_META.map((v, i) => (
                  <span key={v.key}>
                    {i > 0 && <span style={{ color: C.muted }}> + </span>}
                    <span style={{ color: C.accent }}>{pctPlain(viewWeightOf[v.key])}</span>
                    <span style={{ color: C.muted }}> × </span>
                    <span>{v.prob.toFixed(2)}</span>
                  </span>
                ))}
                <span style={{ color: C.muted }}> = </span>
                <span style={{ color: C.bright, fontWeight: 600 }}>{integratedProb.toFixed(4)}</span>
              </div>
              <div style={{ fontFamily: MONO, fontSize: 13, color: C.text, marginTop: 6 }}>
                <span style={{ color: C.muted }}>s = 2 × {integratedProb.toFixed(4)} − 1 = </span>
                <span style={{ color: sign(integratedSignal), fontWeight: 600, fontSize: 18 }}>
                  {integratedSignal >= 0 ? "+" : "−"}
                  {Math.abs(integratedSignal).toFixed(4)}
                </span>
              </div>
            </Panel>

            <Panel
              title="관점 가중치 추이"
              sub="실적으로 자기적응 · 세 관점 합은 항상 1"
              source={`ViewWeightStore · 초기값 균등 1/3 · 가중치 하한 ${pctPlain(W_FLOOR, 0)} · 기준시점 ${SNAPSHOT}`}
            >
              <ResponsiveContainer width="100%" height={240}>
                <AreaChart data={viewWeightHistory} margin={{ top: 4, right: 20, left: 8, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                  <XAxis dataKey="date" tickFormatter={ym} tick={axisTick} tickLine={false} axisLine={{ stroke: C.border }} interval={25} minTickGap={24} />
                  <YAxis
                    domain={[0, 1]}
                    tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
                    tick={axisTick}
                    tickLine={false}
                    axisLine={false}
                    width={44}
                  />
                  <Tooltip content={<ViewTip />} />
                  <Legend verticalAlign="top" align="right" height={26} wrapperStyle={{ fontFamily: MONO, fontSize: 11 }} />
                  <Area isAnimationActive={false} type="monotone" dataKey="market" name="시장 분석" stackId="1" stroke={C.accent} fill={C.accent} fillOpacity={0.55} />
                  <Area isAnimationActive={false} type="monotone" dataKey="sentiment" name="감성 분석" stackId="1" stroke={C.warn} fill={C.warn} fillOpacity={0.4} />
                  <Area isAnimationActive={false} type="monotone" dataKey="temperature" name="시장 온도" stackId="1" stroke={C.profit} fill={C.profit} fillOpacity={0.32} />
                </AreaChart>
              </ResponsiveContainer>
            </Panel>

            <Panel title="판단 브리핑" sub={`${SNAPSHOT} 기준`}>
              <p style={{ fontFamily: SANS, fontSize: 14, lineHeight: 1.85, color: C.text, margin: 0 }}>
                시장 분석은 20일 모멘텀과 14일 RSI가 함께 상승을 가리켜 상승 확률 {(VIEW_META[0].prob * 100).toFixed(0)}%를 냈고,
                최근 {EVAL_WINDOW}거래일 Brier 손실이 세 관점 중 가장 낮아 가중치가 {pctPlain(viewWeightOf.market)}까지 올라왔습니다.
                감성 분석은 {(VIEW_META[1].prob * 100).toFixed(0)}%로 중립에 가깝고 손실이 가장 커 가중치가{" "}
                {pctPlain(viewWeightOf.sentiment)}로 눌려 있습니다. 시장 온도는 KOSPI가 200일 이동평균 위에 있고 VKOSPI가 낮아
                위험선호 국면으로 판별했습니다.
                <br />
                <br />
                통합 신호는 <b style={{ color: sign(integratedSignal) }}>{integratedSignal >= 0 ? "+" : "−"}
                {Math.abs(integratedSignal).toFixed(2)}</b>이고, 이 값이 종목별 허용범위 안 어느 지점에 놓이는지가 목표비중입니다.
                다만 주식계 합이 상한에 걸려 비례 축소되었으므로, 실제 비중은 신호가 가리킨 지점보다 낮습니다. 자세한 내역은
                비중과 리밸런싱 탭에 있습니다.
              </p>
            </Panel>
          </div>
        )}

        {/* 워크포워드 */}
        {tab === "walkforward" && (
          <div role="tabpanel" id="panel-walkforward" aria-labelledby="tab-walkforward" className="flex flex-col gap-5">
            <Panel
              title="워크포워드 구간별 결과"
              sub="학습 12개월 → 검증 6개월, 6개월씩 전진"
              source={`구간 분할: rolling · 검증 구간만 성과에 반영 · ${provenance}`}
            >
              <DataTable
                cols={[
                  { key: "id", label: "구간", align: "left" },
                  { key: "tr", label: "학습", align: "left" },
                  { key: "te", label: "검증", align: "left" },
                  { key: "r", label: "전략", align: "right" },
                  { key: "b", label: "벤치마크", align: "right" },
                  { key: "e", label: "초과", align: "right" },
                  { key: "m", label: "전략 MDD", align: "right" },
                  { key: "bm", label: "벤치 MDD", align: "right" },
                  { key: "s", label: "샤프", align: "right" },
                ]}
              >
                {folds.map((f) => (
                  <tr key={f.id} style={{ borderBottom: `1px solid ${C.border}` }}>
                    <Cel align="left" color={C.bright}>
                      {f.id}
                    </Cel>
                    <Cel align="left" color={C.muted}>
                      {ym(f.trainFrom)} ~ {ym(f.trainTo)}
                    </Cel>
                    <Cel align="left" color={C.text}>
                      {ym(f.testFrom)} ~ {ym(f.testTo)}
                    </Cel>
                    <Cel color={sign(f.ret)}>{pct(f.ret, 1)}</Cel>
                    <Cel color={C.muted}>{pct(f.bmRet, 1)}</Cel>
                    <Cel bold color={sign(f.excess)}>
                      {pp(f.excess)}
                    </Cel>
                    <Cel color={C.loss}>{pct(f.mdd, 1)}</Cel>
                    <Cel color={C.muted}>{pct(f.bmMdd, 1)}</Cel>
                    <Cel>{num(f.sharpe)}</Cel>
                  </tr>
                ))}
              </DataTable>
            </Panel>

            <Panel title="구간별 초과수익" sub="전략 − 벤치마크" source={`기준시점 ${SNAPSHOT}`}>
              <ResponsiveContainer width="100%" height={230}>
                <BarChart data={folds.map((f) => ({ id: f.id, excess: Number((f.excess * 100).toFixed(2) )}))} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                  <XAxis dataKey="id" tick={{ ...axisTick, fontSize: 11 }} tickLine={false} axisLine={{ stroke: C.border }} />
                  <YAxis tickFormatter={(v: number) => `${v}%p`} tick={axisTick} tickLine={false} axisLine={false} width={50} />
                  <Tooltip content={<MonthTip />} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
                  <ReferenceLine y={0} stroke={C.grid} />
                  <Bar dataKey="excess" isAnimationActive={false} radius={[2, 2, 0, 0]}>
                    {folds.map((f) => (
                      <Cell key={f.id} fill={sign(f.excess)} fillOpacity={0.82} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </Panel>
          </div>
        )}

        {/* 전략 Spec */}
        {tab === "spec" && (
          <div role="tabpanel" id="panel-spec" aria-labelledby="tab-spec" className="flex flex-col gap-5">
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 20 }}>
              <Panel title="판단 기준" sub="승인 시점에 확정되며 이후 변경되지 않는다">
                <KeyValue
                  rows={[
                    ["시장 분석 지표", spec.signal_rules.market_analysis.indicators.join(", ")],
                    ["감성 대상 섹터", spec.signal_rules.sentiment.target_sectors.join(", ")],
                    ["감성 lookback", `${spec.signal_rules.sentiment.lookback_hours}시간`],
                    ["추세 지수", `${spec.signal_rules.market_temperature.trend_index} ${spec.signal_rules.market_temperature.trend_ma_window}일선`],
                    ["변동성 지수", spec.signal_rules.market_temperature.volatility_index],
                  ]}
                />
              </Panel>
              <Panel
                title="제약 · 하드캡 클램프"
                sub="Validator 4단계 — 사용자 요청값이 시스템 하드캡을 넘으면 잘린다"
                source={`하드캡 출처: db/seeds/01_hardcap_v0_1.sql · hardcap_versions ${HARDCAP.version} · 레버리지 ${HARDCAP.leverageAllowed ? "허용" : "금지"}`}
              >
                <DataTable
                  minWidth={380}
                  cols={[
                    { key: "f", label: "필드", align: "left" },
                    { key: "r", label: "요청값", align: "right" },
                    { key: "h", label: "하드캡", align: "right" },
                    { key: "v", label: "확정값", align: "right" },
                    { key: "c", label: "", align: "right" },
                  ]}
                >
                  {constraintResolved.map((c) => (
                    <tr key={c.field} style={{ borderBottom: `1px solid ${C.border}` }}>
                      <Cel align="left" color={C.bright}>
                        {c.field}
                      </Cel>
                      <Cel color={c.clamped ? C.muted : C.text}>
                        {c.field === "min_interval_days" ? `${c.requested}일` : pctPlain(c.requested, 0)}
                      </Cel>
                      <Cel color={C.muted}>
                        {c.field === "min_interval_days" ? `${c.hardcap}일` : pctPlain(c.hardcap, 0)}
                      </Cel>
                      <Cel bold color={c.clamped ? C.warn : C.text}>
                        {c.field === "min_interval_days" ? `${c.resolved}일` : pctPlain(c.resolved, 0)}
                      </Cel>
                      <Cel>{c.clamped ? <Tag text="클램프" color={C.warn} /> : <span style={{ color: C.dim }}>—</span>}</Cel>
                    </tr>
                  ))}
                </DataTable>
              </Panel>
            </div>

            <Panel title="Spec JSON" sub="원문" source={`${spec.spec_id} v${spec.spec_version} · 생성 ${spec.created_at}`}>
              <details>
                <summary style={{ fontFamily: MONO, fontSize: 12, color: C.accent, cursor: "pointer" }}>펼쳐서 보기</summary>
                <pre
                  style={{
                    marginTop: 12,
                    padding: 16,
                    background: "#0e141b",
                    border: `1px solid ${C.border}`,
                    fontFamily: MONO,
                    fontSize: 12,
                    lineHeight: 1.6,
                    color: C.text,
                    overflowX: "auto",
                  }}
                >
                  {JSON.stringify(spec, null, 2)}
                </pre>
              </details>
            </Panel>
          </div>
        )}

        {/* 승인 */}
        <Panel title="전략 승인" sub="승인하면 모의운용이 시작되고, 거부하면 자연어로 수정 요청할 수 있습니다">
          <div className="flex flex-col gap-3">
            <label htmlFor="revise" style={{ fontFamily: SANS, fontSize: 13, color: C.muted }}>
              수정 요청 (JSON을 직접 고칠 필요는 없습니다)
            </label>
            <textarea
              id="revise"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              placeholder="예) 반도체 비중을 조금 더 싣되 낙폭은 12% 안쪽으로 막아줘"
              style={{
                width: "100%",
                background: "#0e141b",
                border: `1px solid ${C.border}`,
                color: C.text,
                fontFamily: SANS,
                fontSize: 14,
                padding: "10px 12px",
                resize: "vertical",
              }}
            />
            <div className="flex gap-3 flex-wrap">
              <button
                type="button"
                style={{
                  fontFamily: MONO,
                  fontSize: 13,
                  fontWeight: 600,
                  padding: "10px 20px",
                  background: `${C.accent}18`,
                  border: `1px solid ${C.accent}`,
                  color: C.accent,
                  cursor: "pointer",
                }}
              >
                승인하고 모의운용 시작
              </button>
              <button
                type="button"
                disabled={note.trim().length === 0}
                style={{
                  fontFamily: MONO,
                  fontSize: 13,
                  padding: "10px 20px",
                  background: "transparent",
                  border: `1px solid ${C.border}`,
                  color: note.trim().length === 0 ? C.dim : C.text,
                  cursor: note.trim().length === 0 ? "not-allowed" : "pointer",
                }}
              >
                수정 요청 보내기
              </button>
            </div>
          </div>
        </Panel>

        <footer style={{ fontFamily: MONO, fontSize: 10, color: C.muted, lineHeight: 1.8, paddingBottom: 24 }}>
          모든 수치는 백테스트 결과이며 실현 수익을 보장하지 않습니다. 검증 구간 성과만 집계했고 학습 구간은 제외했습니다.
          <br />
          {provenance} · 재현: 같은 seed와 같은 스냅샷으로 재실행하면 동일한 주문이 생성됩니다.
        </footer>
      </main>
    </div>
  );
}
