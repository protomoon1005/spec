"use client";

// 매매 지점 차트와 LLM 코드 생성 비교 카드. 현서 님 demo/app.py 의 trade_chart · draw_baseline 을 옮겼다.

import { useMemo, useState } from "react";
import {
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { fmtPct, fmtWon } from "@/lib/chat";
import type { BaselineResult, OrderRow, TradeChart } from "@/lib/v2";

const ACCENT = "#2a78d6";
const UP = "#16a34a";
const DOWN = "#dc2626";
const MUTED = "#6b7280";
const GRID = "#eef0f2";
const CLOSE = "#4b5563";
const LINE_COLORS = [ACCENT, "#eb6834", "#8b5cf6", "#0d9488"];
const PAD = 20; // 처음 보여 줄 구간: 첫 매매 20거래일 전 ~ 마지막 매매 20거래일 뒤

type Row = Record<string, number | string | null>;

// ── 매매 지점 ─────────────────────────────────────────────────────────

export function TradeCharts({ dates, charts }: { dates: string[]; charts: TradeChart[] }) {
  const [tab, setTab] = useState(0);
  const [full, setFull] = useState(false);
  const chart = charts[Math.min(tab, charts.length - 1)];
  const window = useMemo(() => tradeWindow(dates, chart), [dates, chart]);
  if (!chart) return null;
  const [lo, hi] = full || !window ? [0, dates.length - 1] : window;
  return (
    <div>
      <div className="chart-head">
        <p className="chart-title">매매 지점</p>
        {window && (
          <label className="toggle">
            <input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} /> 전체 기간 보기
          </label>
        )}
      </div>
      {charts.length > 1 && (
        <div className="tabs">
          {charts.map((c, i) => (
            <button key={c.ticker} className={`tab${i === tab ? " on" : ""}`} onClick={() => setTab(i)}>
              {c.name}
            </button>
          ))}
        </div>
      )}
      <OneTicker dates={dates} chart={chart} lo={lo} hi={hi} />
    </div>
  );
}

function OneTicker({ dates, chart, lo, hi }: { dates: string[]; chart: TradeChart; lo: number; hi: number }) {
  const buys = new Map(chart.buys.map((p) => [p.date, p.price]));
  const sells = new Map(chart.sells.map((p) => [p.date, p.price]));
  const rows: Row[] = [];
  for (let k = lo; k <= hi; k++) {
    const d = dates[k];
    const row: Row = { date: d, close: chart.close[k] ?? null, buy: buys.get(d) ?? null, sell: sells.get(d) ?? null };
    chart.price_lines.forEach((ln, j) => (row[`p${j}`] = ln.values[k] ?? null));
    chart.panels.forEach((p, i) => p.lines.forEach((ln, j) => (row[`g${i}_${j}`] = ln.values[k] ?? null)));
    rows.push(row);
  }
  const inRange = (d: string | null) => d !== null && d >= dates[lo] && d <= dates[hi];
  const signals = [
    ...chart.buys.filter((p) => inRange(p.signal_date)).map((p) => ({ date: p.signal_date!, color: UP })),
    ...chart.sells.filter((p) => inRange(p.signal_date)).map((p) => ({ date: p.signal_date!, color: DOWN })),
  ];
  const sync = `trade-${chart.ticker}`;
  return (
    <>
      <div style={{ height: 300 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} syncId={sync} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="date" tick={{ fill: MUTED, fontSize: 11 }} tickFormatter={(d: string) => d.slice(0, 7)} minTickGap={48} />
            <YAxis
              tick={{ fill: MUTED, fontSize: 11 }}
              tickFormatter={(v: number) => v.toLocaleString("ko-KR")}
              width={64}
              domain={["auto", "auto"]}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip formatter={(v: number, name: string) => [fmtWon(v), name]} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line type="linear" dataKey="close" name="종가" stroke={CLOSE} dot={false} strokeWidth={1.4} isAnimationActive={false} />
            {chart.price_lines.map((ln, j) => (
              <Line
                key={ln.label}
                type="linear"
                dataKey={`p${j}`}
                name={ln.label}
                stroke={LINE_COLORS[j % LINE_COLORS.length]}
                dot={false}
                strokeWidth={1.2}
                isAnimationActive={false}
              />
            ))}
            <Scatter dataKey="buy" name="사기" fill={UP} shape={<Triangle color={UP} up />} isAnimationActive={false} />
            <Scatter dataKey="sell" name="팔기" fill={DOWN} shape={<Triangle color={DOWN} />} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      {chart.panels.map((p, i) => (
        <div key={p.title} style={{ height: 130 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={rows} syncId={sync} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="date" hide />
              <YAxis tick={{ fill: MUTED, fontSize: 11 }} width={64} domain={["auto", "auto"]} axisLine={false} tickLine={false} />
              <Tooltip formatter={(v: number, name: string) => [Number(v).toFixed(2), name]} />
              {signals.map((s, k) => (
                <ReferenceLine key={`s${k}`} x={s.date} stroke={s.color} strokeDasharray="2 3" strokeOpacity={0.6} />
              ))}
              {p.refs.map((v) => (
                <ReferenceLine key={`r${v}`} y={v} stroke={MUTED} strokeDasharray="4 4" label={{ value: `${v}`, fill: MUTED, fontSize: 11, position: "right" }} />
              ))}
              {p.lines.map((ln, j) => (
                <Line
                  key={ln.label}
                  type="linear"
                  dataKey={`g${i}_${j}`}
                  name={ln.label}
                  stroke={LINE_COLORS[(chart.price_lines.length + j) % LINE_COLORS.length]}
                  dot={false}
                  strokeWidth={1.2}
                  isAnimationActive={false}
                />
              ))}
              <Legend wrapperStyle={{ fontSize: 12 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ))}
    </>
  );
}

// Recharts 가 넘기는 cx · cy 에 삼각형을 그린다. 값이 없는 날은 그리지 않는다
function Triangle(props: { cx?: number; cy?: number; color: string; up?: boolean; payload?: Row; dataKey?: string }) {
  const { cx, cy, color, up } = props;
  if (cx == null || cy == null || Number.isNaN(cy)) return null;
  const s = 7;
  const d = up ? `M${cx},${cy - s} L${cx + s},${cy + s} L${cx - s},${cy + s} Z` : `M${cx},${cy + s} L${cx + s},${cy - s} L${cx - s},${cy - s} Z`;
  return <path d={d} fill={color} />;
}

function tradeWindow(dates: string[], chart?: TradeChart): [number, number] | null {
  if (!chart) return null;
  const points = [...chart.buys, ...chart.sells].map((p) => dates.indexOf(p.date)).filter((k) => k >= 0);
  if (!points.length) return null;
  const lo = Math.max(0, Math.min(...points) - PAD);
  const hi = Math.min(dates.length - 1, Math.max(...points) + PAD);
  return lo === 0 && hi === dates.length - 1 ? null : [lo, hi];
}

// ── LLM 코드 생성과 비교 ───────────────────────────────────────────────

const OUTCOME_BADGE: Record<string, { text: string; cls: string }> = {
  code: { text: "코드를 냈어요", cls: "blue" },
  clarify: { text: "되물었어요", cls: "amber" },
  reject: { text: "구현할 수 없다고 했어요", cls: "amber" },
  error: { text: "응답을 해석하지 못했어요", cls: "red" },
};

export function BaselineCard({ result }: { result: BaselineResult }) {
  if (result.status === "failed") return <div className="error-box">비교하지 못했어요: {result.reason}</div>;
  const badge = OUTCOME_BADGE[result.outcome];
  return (
    <div className="card">
      <p className="ai-title">
        LLM 코드 생성과 비교 <span className="muted">같은 모델 · 같은 설정, 프롬프트에는 데이터 · 출력 형식만</span>
      </p>
      <div className="badges">
        <span className={`badge ${badge.cls}`}>{badge.text}</span>
        {result.run_error && <span className="badge red">실행 실패</span>}
        {result.same_orders === true && <span className="badge green">주문 일치</span>}
        {result.same_orders === false && <span className="badge red">주문 다름</span>}
        <span className="badge gray">{result.cached ? "저장된 응답" : `${result.seconds}초`}</span>
      </div>
      {(result.outcome === "clarify" || result.outcome === "reject") && <p>LLM 의 답: {result.message}</p>}
      {result.run_error && <p className="muted">실행 오류: {result.run_error}</p>}

      {result.series && result.base && (
        <>
          <div className="metrics two">
            <div className="metric">
              <div className="muted">우리 방식 총수익률</div>
              <div className="metric-value">{fmtPct(result.ours.total_return)}</div>
            </div>
            <div className="metric">
              <div className="muted">LLM 코드 총수익률</div>
              <div className="metric-value">{fmtPct(result.base.total_return)}</div>
            </div>
          </div>
          <div style={{ height: 280 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={result.series} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="date" tick={{ fill: MUTED, fontSize: 11 }} tickFormatter={(d: string) => d.slice(0, 7)} minTickGap={48} />
                <YAxis
                  tick={{ fill: MUTED, fontSize: 11 }}
                  tickFormatter={(v: number) => `${Math.round(v / 10_000).toLocaleString("ko-KR")}만`}
                  width={60}
                  domain={["auto", "auto"]}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip formatter={(v: number) => fmtWon(v)} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line type="monotone" dataKey="ours" name="우리 방식" stroke={ACCENT} dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="base" name="LLM 코드" stroke="#8b5cf6" dot={false} strokeWidth={1.6} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          {result.base.ignored > 0 && (
            <p className="muted">기간 밖이거나 겹쳐서 체결되지 않은 LLM 주문 {result.base.ignored}건 · 같은 비용 · 세금으로 다음 날 시가에 체결</p>
          )}
        </>
      )}

      <div className="two-col">
        <div>
          <p className="chart-title">우리 주문 ({result.ours.orders.length}건)</p>
          <Orders rows={result.ours.orders} />
        </div>
        <div>
          <p className="chart-title">LLM 코드 주문 ({result.base ? `${result.base.orders.length}건` : "—"})</p>
          {result.base ? <Orders rows={result.base.orders} /> : <p className="muted">실행하지 못해 주문이 없습니다</p>}
        </div>
      </div>

      {result.code ? (
        <details className="expander">
          <summary>생성된 코드</summary>
          <pre className="json">{result.code}</pre>
        </details>
      ) : result.raw ? (
        <details className="expander">
          <summary>LLM 응답 원문</summary>
          <pre className="json">{result.raw}</pre>
        </details>
      ) : null}
      <p className="small">
        비교 대상에 넘긴 종목: {result.tickers.join(", ") || "없음"} · 기간 {result.period[0]} ~ {result.period[1]} · 실행: 별도
        프로세스, pandas · numpy 만, 파일 · 네트워크 금지, 30초 제한
      </p>
    </div>
  );
}

function Orders({ rows }: { rows: OrderRow[] }) {
  if (!rows.length) return <p className="muted">주문 없음</p>;
  return (
    <div className="trades-wrap" style={{ maxHeight: 260 }}>
      <table className="trades">
        <thead>
          <tr>
            <th>체결일</th>
            <th>종목</th>
            <th>방향</th>
            <th className="r">목표 비중</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>{r.date}</td>
              <td>
                {r.name}
                <span className="code">{r.ticker}</span>
              </td>
              <td className={r.side === "buy" ? "up" : "down"}>{r.side === "buy" ? "사기" : "팔기"}</td>
              <td className="r">{(r.weight * 100).toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
