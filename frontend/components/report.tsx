"use client";

// 백테스트 리포트 — 실행 한 건(GET /backtest/runs/{run_id})을 그대로 그린다.
//
// 수익 곡선, 핵심 지표, 담은 종목과 비중 범위, 리밸런싱 기록만 남겼다(docs/pivot-plan.md 3.4).
// 값은 전부 실행 기록에서 읽는다. 화면에서 다시 계산하지 않는다.

import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { BacktestRun } from "@/lib/api";
import { Cel, DataTable, KeyValue, Kpi, Panel } from "./report/primitives";
import { C, MONO, SANS, sign } from "./report/tokens";

const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
const ratio = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));
const won = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v).toLocaleString("ko-KR")}원`);

export default function Report({ run }: { run: BacktestRun }) {
  const m = run.metrics;
  const strategy = run.summary?.strategy;
  const market = run.summary?.market;
  const universe = run.universe ?? [];
  const decisions = run.decisions ?? [];
  const names = Object.fromEntries(universe.map((u) => [u.ticker, u.name]));

  return (
    <div style={{ background: C.bg, fontFamily: SANS, color: C.text, minHeight: "100vh" }}>
      <main className="max-w-[1100px] mx-auto w-full box-border px-6 py-10 flex flex-col gap-6">
        <header>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}>
            백테스트 리포트 · 실행 #{run.run_id} · 전략서 {run.spec_id}
          </div>
          <h1 style={{ fontFamily: MONO, fontSize: 20, fontWeight: 600, color: C.bright, margin: "8px 0 0" }}>
            {run.period_start} ~ {run.period_end}
          </h1>
        </header>

        <section className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
          <Kpi label="최종 수익률" value={pct(strategy?.total)} color={sign(strategy?.total ?? 0)} sub={`시장 ${pct(market?.total)}`} />
          <Kpi label="연환산 수익률(CAGR)" value={pct(m?.cagr)} color={sign(m?.cagr ?? 0)} sub={`시장 ${pct(m?.benchmark_cagr)}`} />
          <Kpi label="최대 낙폭(MDD)" value={pct(m?.mdd)} color={C.loss} sub={`시장 ${pct(market?.mdd)}`} />
          <Kpi label="샤프 지수" value={ratio(m?.sharpe)} sub={`소르티노 ${ratio(m?.sortino)}`} />
          <Kpi label="최종 평가액" value={won(strategy?.final)} sub={`시작 ${won(run.seed_money)}`} />
        </section>

        <Panel title="수익 곡선" sub="전략과 시장(KODEX 200 매수 후 보유)의 평가액">
          <div style={{ height: 300 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={run.series ?? []} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                <XAxis dataKey="date" tick={{ fill: C.muted, fontSize: 11 }} tickLine={false} minTickGap={40} />
                <YAxis
                  tickFormatter={(v: number) => `${Math.round(v / 10_000).toLocaleString("ko-KR")}만`}
                  tick={{ fill: C.muted, fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                  width={64}
                  domain={["auto", "auto"]}
                />
                <Tooltip
                  formatter={(v: number) => won(v)}
                  contentStyle={{ background: C.surface2, border: `1px solid ${C.border}`, fontFamily: MONO, fontSize: 12 }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line type="monotone" dataKey="strategy" name="전략" stroke={C.accent} dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="market" name="시장" stroke={C.muted} dot={false} strokeWidth={1.5} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel title="담은 종목과 비중 범위" sub="AI 가 낸 범위와 검증기가 확정한 범위">
          <DataTable
            cols={[
              { key: "ticker", label: "종목코드", align: "left" },
              { key: "name", label: "종목명", align: "left" },
              { key: "raw", label: "AI 범위", align: "right" },
              { key: "final", label: "확정 범위", align: "right" },
            ]}
          >
            {universe.map((u) => (
              <tr key={u.ticker} style={{ borderBottom: `1px solid ${C.border}` }}>
                <Cel align="left">{u.ticker}</Cel>
                <Cel align="left">{u.name}</Cel>
                <Cel>
                  {pct(u.weight_min_raw)} ~ {pct(u.weight_max_raw)}
                </Cel>
                <Cel>
                  {pct(u.weight_min)} ~ {pct(u.weight_max)}
                </Cel>
              </tr>
            ))}
          </DataTable>
        </Panel>

        <Panel title="리밸런싱 기록" sub={run.schedule?.note ?? ""}>
          <DataTable
            cols={[
              { key: "date", label: "날짜", align: "left" },
              { key: "target", label: "목표 비중", align: "left" },
              { key: "cash", label: "현금", align: "right" },
            ]}
          >
            {decisions.map((d) => (
              <tr key={d.date} style={{ borderBottom: `1px solid ${C.border}` }}>
                <Cel align="left">{d.date}</Cel>
                <Cel align="left">
                  {Object.entries(d.target)
                    .map(([t, w]) => `${names[t] ?? t} ${pct(w)}`)
                    .join(" · ")}
                </Cel>
                <Cel>{pct(d.cash)}</Cel>
              </tr>
            ))}
          </DataTable>
        </Panel>

        <Panel title="실행 조건">
          <KeyValue
            rows={[
              ["데이터 마지막 거래일", run.data_snapshot_asof ?? "—"],
              ["수수료", pct(run.fee_rate)],
              ["세금", pct(run.tax_rate)],
              ["슬리피지", run.slippage_bp == null ? "—" : `${run.slippage_bp}bp`],
              ["건너뛴 리밸런싱", String(run.schedule?.skipped_rebalance_dates.length ?? 0)],
            ]}
          />
        </Panel>
      </main>
    </div>
  );
}
