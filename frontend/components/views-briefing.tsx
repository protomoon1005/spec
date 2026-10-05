"use client";

// 관점 브리핑 본문. 값 계산은 lib/views-briefing.ts 가 하고 여기는 그리기만 한다.
//
// 관점 색은 리포트의 관점 색(시장분석 파랑 · 감성 주황 · 시장온도 초록)과 같은 색상에서
// 어두운 바탕용으로 한 단계 낮춘 것이다. 그대로 쓰면 어두운 바탕에서 너무 밝아 선 세 개가
// 같은 무게로 보이지 않는다(dataviz 검증기: 밝기 대역 · 색각이상 구분 · 대비 모두 통과).

import Link from "next/link";
import { useEffect, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import RequireLogin from "@/components/require-login";
import { C, MONO, R } from "@/components/report/tokens";
import { useReducedMotion } from "@/components/report/motion";
import { api, ApiError, loadBacktestRun, loadCompileResult } from "@/lib/api";
import {
  briefingFromRun,
  probTint,
  RULES,
  VIEWS,
  type Briefing,
  type NoBriefing,
  type RunForBriefing,
  type ViewKey,
} from "@/lib/views-briefing";

const VIEW_COLOR: Record<ViewKey, string> = { market: "#4a8ee6", sentiment: "#bd8220", regime: "#22ad74" };

const SOURCE: Record<string, { label: string; tone: string; note: string }> = {
  real: { label: "실모델", tone: "ok", note: "학습한 모델이 그 시점까지의 데이터로 낸 확률입니다." },
  mock: { label: "목업", tone: "warn", note: "아직 모델이 연결되지 않아 고정된 가짜 점수입니다." },
  neutral: { label: "중립 고정", tone: "muted", note: "항상 판단 불가(확률 0.5)를 냅니다." },
};
// 감성은 일부러 중립이다. 이유를 출처 배지 옆에 적는다.
const SENTIMENT_NOTE = "과거 뉴스를 확보할 수 없어 중립으로 고정했습니다 (팀 결정).";

const pctW = (v: number, d = 1) => `${(v * 100).toFixed(d)}%`;
const ppW = (v: number) => `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)}%p`;
const ym = (iso: string) => iso.slice(2, 7).replace("-", ".");

export default function ViewsBriefing({ runId }: { runId?: string }) {
  return <RequireLogin>{() => (runId === undefined ? <NoRun /> : <Run runId={runId} />)}</RequireLogin>;
}

// --- run_id 없이 들어왔을 때 ------------------------------------------------

function NoRun() {
  // undefined: 확인 중, null: 이번 탭에 돌린 실행 없음
  const [saved, setSaved] = useState<number | null | undefined>(undefined);

  useEffect(() => {
    const r = loadCompileResult();
    setSaved(r?.status === "completed" ? (loadBacktestRun(r.spec_id)?.run_id ?? null) : null);
  }, []);

  return (
    <>
      {saved === undefined ? (
        <p className="flow-state">이번 탭에서 돌린 백테스트를 찾는 중…</p>
      ) : saved === null ? (
        <div className="flow-empty">
          <strong>브리핑할 실행이 없습니다.</strong>
          백테스트를 돌리면 그 실행의 관점 기록을 여기서 볼 수 있습니다.{" "}
          <Link href="/backtest">백테스트로</Link>
        </div>
      ) : (
        <div className="flow-empty" style={{ borderStyle: "solid" }}>
          <strong>이번 전략서로 돌린 실행 #{saved} 가 있습니다.</strong>
          <Link href={`/views?run_id=${saved}`}>실행 #{saved} 브리핑 보기</Link>
        </div>
      )}
      <RulesNote />
      <div className="flow-next" style={{ justifyContent: "flex-start" }}>
        <Link href="/compile">처음으로 — 전략 요청</Link>
      </div>
    </>
  );
}

// --- 실행 한 건 -------------------------------------------------------------

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | NoBriefing
  | (Briefing & { asof: string | null });

function Run({ runId }: { runId: string }) {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const id = Number(runId);
    if (!Number.isInteger(id) || id <= 0) {
      setState({ kind: "error", message: `실행 번호가 올바르지 않습니다: ${runId}` });
      return;
    }
    // 개발 모드는 effect 를 두 번 돌린다. 먼저 돈 쪽이 늦게 끝나 결과를 덮어쓰지 않게 한다.
    let alive = true;
    api<RunForBriefing>(`/backtest/runs/${id}`)
      .then((run) => {
        const b = briefingFromRun(run);
        if (alive) setState(b.kind === "ok" ? { ...b, asof: run.data_snapshot_asof } : b);
      })
      .catch((err) => {
        if (!alive) return;
        const message =
          err instanceof ApiError && err.status === 404
            ? "실행 기록을 찾을 수 없습니다. 없는 번호이거나 다른 사람이 돌린 실행입니다."
            : `실행 기록을 받지 못했습니다 — ${err instanceof Error ? err.message : String(err)}`;
        setState({ kind: "error", message });
      });
    return () => {
      alive = false;
    };
  }, [runId]);

  if (state.kind === "loading") return <p className="flow-state">실행 #{runId} 의 관점 기록을 받아 오는 중…</p>;
  if (state.kind !== "ok") {
    const empty = state.kind === "no-views";
    return (
      <>
        {empty ? (
          <div className="flow-empty">
            <strong>이 실행에는 관점 기록이 없습니다.</strong>
            관점 기록을 남기기 전에 돌린 실행입니다. 백테스트를 다시 돌리면 생깁니다.
          </div>
        ) : (
          <p className={state.kind === "pending" ? "flow-msg warn" : "flow-msg err"} role="alert">
            {state.message}
          </p>
        )}
        <div className="flow-actions" style={{ marginTop: 16 }}>
          <Link href="/backtest">백테스트 화면으로</Link>
        </div>
        <RulesNote />
      </>
    );
  }
  return <Body b={state} />;
}

function Body({ b }: { b: Briefing & { asof: string | null } }) {
  return (
    <>
      <div className="flow-actions" style={{ justifyContent: "space-between", marginBottom: 18 }}>
        <span className="flow-hint">
          실행 <b className="mono" style={{ color: C.bright }}>#{b.runId}</b>, 마지막 리밸런싱{" "}
          <span className="mono">{b.latest.date}</span>
          {b.asof && (
            <>
              , 데이터 기준 <span className="mono">{b.asof}</span>
            </>
          )}
        </span>
        <Link href={`/report?run_id=${b.runId}`}>이 실행의 리포트</Link>
      </div>

      <ViewCards b={b} />
      <WeightChart b={b} />
      <ProbTable b={b} />
      <RulesNote />
    </>
  );
}

// (b) 최신 가중치 · 출처 -------------------------------------------------------

function ViewCards({ b }: { b: Briefing }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
      {VIEWS.map((v) => {
        const w = b.latest[v.key];
        const src = b.sources[v.key];
        const s = src ? SOURCE[src] : undefined;
        return (
          <section key={v.key} className="flow-card" style={{ borderTop: `3px solid ${VIEW_COLOR[v.key]}`, margin: 0 }}>
            <div className="flow-actions" style={{ justifyContent: "space-between" }}>
              <h3 style={{ margin: 0 }}>{v.label}</h3>
              <span className={`flow-badge ${s?.tone ?? "muted"}`}>{s?.label ?? src ?? "출처 기록 없음"}</span>
            </div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, margin: "10px 0 4px" }}>
              <span style={{ fontFamily: MONO, fontSize: 30, fontWeight: 600, color: C.bright, lineHeight: 1.1 }}>
                {pctW(w)}
              </span>
              <span className="flow-hint">현재 가중치</span>
            </div>
            <WeightBar value={w} color={VIEW_COLOR[v.key]} />
            <p style={{ fontSize: 12, color: C.muted, marginTop: 8 }}>
              첫 리밸런싱 대비{" "}
              <span className="mono" style={{ color: C.text }}>
                {ppW(b.drift[v.key])}
              </span>
              {w <= RULES.floor + 1e-9 && <span style={{ color: C.warn }}>, 하한에 닿음</span>}
            </p>
            <p style={{ fontSize: 13, color: C.muted, marginTop: 10, paddingTop: 10, borderTop: `1px solid ${C.border}` }}>
              {v.basis}
              <br />
              {v.key === "sentiment" && src === "neutral" ? SENTIMENT_NOTE : s?.note}
            </p>
          </section>
        );
      })}
    </div>
  );
}

// 0~100% 막대 위에 하한(10%)과 균등(1/3) 눈금을 같이 둔다.
function WeightBar({ value, color }: { value: number; color: string }) {
  return (
    <div
      style={{ position: "relative", height: 8, background: "#0e141b", border: `1px solid ${C.border}`, borderRadius: R.pill }}
      role="img"
      aria-label={`가중치 ${pctW(value)}`}
    >
      <div style={{ width: `${value * 100}%`, height: "100%", background: color, borderRadius: R.pill }} />
      {[RULES.floor, RULES.equal].map((t) => (
        <span
          key={t}
          style={{ position: "absolute", left: `${t * 100}%`, top: -3, bottom: -3, width: 1, background: C.muted, opacity: 0.6 }}
        />
      ))}
    </div>
  );
}

// (a) 가중치 추이 ---------------------------------------------------------------

function WeightChart({ b }: { b: Briefing }) {
  const reduce = useReducedMotion();
  // 세로축은 0 부터가 아니라 값 범위에 맞춘다. 가중치는 몇 %p 씩 움직여서 0~100% 축에서는
  // 선이 거의 평평해 보인다. 하한(10%) 선은 늘 축 안에 둔다.
  const all = b.history.flatMap((p) => VIEWS.map((v) => p[v.key]));
  const step = 0.05;
  const bottom = Math.max(0, Math.floor((Math.min(RULES.floor, ...all) - 0.02) / step) * step);
  const top = Math.min(1, Math.ceil((Math.max(...all) + 0.02) / step) * step);
  const ticks: number[] = [];
  for (let t = bottom; t <= top + 1e-9; t += step) ticks.push(Math.round(t * 100) / 100);
  const n = b.history.length;

  return (
    <section className="flow-card" style={{ marginTop: 16 }}>
      <Heading
        title="관점 가중치 추이"
        sub={
          b.firstMove
            ? `리밸런싱 ${n}회 중 ${b.changes}회 갱신, ${b.firstMove} 부터 균등 1/3 에서 벗어남`
            : `리밸런싱 ${n}회 내내 균등 1/3 — 아직 채점된 실적이 가중치를 움직이지 않았습니다`
        }
      />
      <div className="flow-actions" style={{ gap: 18, margin: "4px 0 10px" }} aria-hidden>
        {VIEWS.map((v) => (
          <span key={v.key} style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 12, color: C.text }}>
            <span style={{ width: 14, height: 2, background: VIEW_COLOR[v.key], borderRadius: 2 }} />
            {v.label}
            <span className="mono" style={{ color: C.bright }}>
              {pctW(b.latest[v.key])}
            </span>
          </span>
        ))}
        <span style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 12, color: C.muted }}>
          <span style={{ width: 14, borderTop: `1px dashed ${C.muted}` }} />
          점선: 하한 {pctW(RULES.floor, 0)}, 균등 {pctW(RULES.equal)}
        </span>
      </div>
      <div style={{ width: "100%", height: 280 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={b.history} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={C.border} strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="date"
              tickFormatter={ym}
              tick={{ fontFamily: MONO, fontSize: 10, fill: C.muted }}
              tickLine={false}
              axisLine={{ stroke: C.border }}
              minTickGap={28}
            />
            <YAxis
              domain={[bottom, top]}
              ticks={ticks}
              tickFormatter={(t: number) => `${Math.round(t * 100)}%`}
              tick={{ fontFamily: MONO, fontSize: 10, fill: C.muted }}
              tickLine={false}
              axisLine={false}
              width={40}
            />
            <ReferenceLine
              y={RULES.floor}
              stroke={C.muted}
              strokeDasharray="4 4"
              label={{ value: "하한", position: "insideTopLeft", fill: C.muted, fontSize: 10, fontFamily: MONO }}
            />
            <ReferenceLine y={RULES.equal} stroke={C.dim} strokeDasharray="2 4" />
            <Tooltip content={<WeightTip />} cursor={{ stroke: C.grid }} />
            {VIEWS.map((v) => (
              <Line
                key={v.key}
                type="stepAfter"
                dataKey={v.key}
                name={v.label}
                stroke={VIEW_COLOR[v.key]}
                strokeWidth={2}
                dot={n <= 24 ? { r: 3, strokeWidth: 0, fill: VIEW_COLOR[v.key] } : false}
                activeDot={{ r: 4, stroke: C.surface, strokeWidth: 2 }}
                isAnimationActive={!reduce}
                animationDuration={700}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="flow-hint" style={{ marginTop: 8 }}>
        리밸런싱일마다 그날 판단에 실제로 쓴 가중치입니다. 전날까지 확정된 최근 {RULES.window}거래일 성적(Brier 손실)으로
        정해지고, 다음 리밸런싱까지 유지됩니다.
      </p>
      <details style={{ marginTop: 10 }}>
        <summary style={{ cursor: "pointer", fontSize: 13, color: C.muted }}>표로 보기</summary>
        <div className="flow-table" style={{ marginTop: 10, maxHeight: 320, overflowY: "auto" }}>
          <table style={{ minWidth: 420 }}>
            <thead>
              <tr>
                <th>리밸런싱일</th>
                {VIEWS.map((v) => (
                  <th key={v.key} className="num">
                    {v.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {b.history.map((p) => (
                <tr key={p.date}>
                  <td className="mono">{p.date}</td>
                  {VIEWS.map((v) => (
                    <td key={v.key} className="num">
                      {pctW(p[v.key])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}

type TipProps = { active?: boolean; label?: string | number; payload?: { name?: string; value?: number; color?: string }[] };

function WeightTip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: C.surface2, border: `1px solid ${C.grid}`, borderRadius: R.inner, padding: "9px 13px" }}>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted, marginBottom: 5 }}>{label}</div>
      {[...payload]
        .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
        .map((p) => (
          <div key={p.name} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: C.text }}>
            <span style={{ width: 8, height: 2, background: p.color, borderRadius: 2 }} />
            <span style={{ minWidth: 64 }}>{p.name}</span>
            <span style={{ fontFamily: MONO, color: C.bright }}>{pctW(p.value ?? 0)}</span>
          </div>
        ))}
    </div>
  );
}

// (c) 종목 × 관점 확률 --------------------------------------------------------

function ProbTable({ b }: { b: Briefing }) {
  return (
    <section className="flow-card" style={{ marginTop: 16 }}>
      <Heading title="종목별 관점 확률" sub={`${b.latest.date} 리밸런싱, 20거래일 뒤 상승 확률 (보정 후)`} />
      {b.rows.length === 0 ? (
        <p className="flow-hint">이 리밸런싱에는 종목별 확률이 기록되지 않았습니다.</p>
      ) : (
        <div className="flow-table" style={{ background: "transparent" }}>
          <table style={{ minWidth: 520 }}>
            <thead>
              <tr>
                <th>종목</th>
                {VIEWS.map((v) => (
                  <th key={v.key} className="num">
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <span style={{ width: 8, height: 8, borderRadius: 2, background: VIEW_COLOR[v.key] }} />
                      {v.label} {pctW(b.latest[v.key], 0)}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {b.rows.map((r) => (
                <tr key={r.ticker}>
                  <td>
                    <span className="mono" style={{ color: C.muted, marginRight: 8 }}>
                      {r.ticker}
                    </span>
                    {r.name}
                  </td>
                  {VIEWS.map((v) => {
                    const p = r.probs[v.key];
                    return (
                      <td
                        key={v.key}
                        className="num"
                        style={{ background: probTint(p, C.profit, C.loss), color: p === null ? C.dim : C.bright }}
                      >
                        {p === null ? "–" : p.toFixed(2)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="flow-actions" style={{ gap: 14, marginTop: 10, fontSize: 12, color: C.muted }}>
        <ScaleChip p={0.25} label="0.25 이하" />
        <ScaleChip p={0.5} label="0.50 중립" />
        <ScaleChip p={0.75} label="0.75 이상" />
        <span>0.5 보다 높으면 상승, 낮으면 하락 쪽 판단입니다. 감성 열이 전부 0.50 인 것은 중립 고정이기 때문입니다.</span>
      </div>
    </section>
  );
}

function ScaleChip({ p, label }: { p: number; label: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span
        style={{
          width: 14,
          height: 14,
          borderRadius: 3,
          border: `1px solid ${C.border}`,
          background: probTint(p, C.profit, C.loss) ?? C.surface,
        }}
      />
      {label}
    </span>
  );
}

// (d) 통합 규칙 ---------------------------------------------------------------

function RulesNote() {
  return (
    <section className="flow-card" style={{ marginTop: 16 }}>
      <Heading title="세 관점을 합치는 규칙" sub="확정 수치, 판단 때마다 같은 순서로 계산" />
      <dl className="flow-kv">
        <dt>합치기</dt>
        <dd className="prose">관점 점수 × 관점 가중치를 더한 값 m 을 s = tanh(m / {RULES.tanhScale}) 로 −1 ~ +1 안에 눌러 담는다</dd>
        <dt>데드존</dt>
        <dd className="prose">절댓값 {RULES.deadzone.toFixed(2)} 미만의 작은 신호는 0 으로 본다</dd>
        <dt>가중치 하한</dt>
        <dd className="prose">어떤 관점도 {pctW(RULES.floor, 0)} 밑으로 내려가지 않는다</dd>
        <dt>가중치 갱신</dt>
        <dd className="prose">
          처음엔 1/3 씩. 최근 {RULES.window}거래일 동안 잘 맞힌 관점의 비중을 높인다. 어제까지 확정된 성적만 쓴다
        </dd>
      </dl>
    </section>
  );
}

function Heading({ title, sub }: { title: string; sub?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
      <h2 style={{ margin: 0, padding: 0, border: 0 }}>{title}</h2>
      {sub && <span className="flow-hint">{sub}</span>}
    </div>
  );
}
