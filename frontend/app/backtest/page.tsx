"use client";

// 백테스트 (전략서 v0.2).
//
// 주소의 strategy 로 POST /v2/strategies/{id}/backtest 를 접수하고, 2초마다 GET /v2/backtests/{run_id} 로
// 상태를 받는다. 같은 탭에서 같은 전략서로 이미 접수했으면 새로 돌리지 않고 그 실행을 다시 조회한다.
//
// 결과(설계 문서 "데모 범위와 조건형 백테스트")
//   - 핵심 지표: 최종 수익률, 연환산 수익률, 최대 낙폭, 거래 수와 이긴 거래 비율
//   - 비교 기준 두 가지: 같은 종목을 시작일에 사서 들고 있기, KODEX 200 사서 들고 있기(코스피 지수 대신)
//   - 평가액 곡선 세 줄, 거래 기록, 체결하지 못한 주문, 끝까지 들고 있는 종목, 서버가 붙인 안내
// 실패하면 서버가 남긴 이유를 그대로 보여 준다(예: 지수 · VIX 조건은 데모 범위 밖).

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { C, MONO } from "@/components/tokens";
import { api, loadBacktestRun, saveBacktestRun } from "@/lib/api";
import { pct, won, type BacktestV2, type Summary } from "@/lib/v2";

const POLL_MS = 2000;

export default function BacktestPage() {
  return (
    <ScaffoldShell wide>
      <main>
        <h1>백테스트</h1>
        <p>이 화면에서 하는 일: 전략서를 과거 가격으로 돌려 본 결과를 확인한다.</p>
        <RequireLogin>
          {() => (
            <Suspense fallback={<p className="flow-state">불러오는 중…</p>}>
              <Body />
            </Suspense>
          )}
        </RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Body() {
  const specId = useSearchParams().get("strategy");
  if (!specId) {
    return (
      <p className="flow-msg" style={{ marginTop: 0 }}>
        백테스트할 전략서가 없어요. <Link href="/compile">전략 요청</Link>에서 전략서를 먼저 만들어 주세요.
      </p>
    );
  }
  return <LiveRun specId={specId} />;
}

function LiveRun({ specId }: { specId: string }) {
  const [run, setRun] = useState<BacktestV2 | null>(null);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);
  const alive = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      clearTimeout(timer.current);
    };
  }, []);

  useEffect(() => {
    // 개발 모드는 effect 를 두 번 돌린다. 접수가 두 번 되지 않게 막는다.
    if (started.current) return;
    started.current = true;

    async function poll(runId: number) {
      try {
        const r = await api<BacktestV2>(`/v2/backtests/${runId}`);
        setRun(r);
        if (alive.current && r.status !== "done" && r.status !== "failed") {
          timer.current = setTimeout(() => poll(runId), POLL_MS);
        }
      } catch (err) {
        setError(`결과를 받지 못했어요: ${err instanceof Error ? err.message : String(err)}`);
      }
    }

    async function start() {
      const saved = loadBacktestRun(specId);
      if (saved) {
        await poll(saved.run_id);
        return;
      }
      try {
        const r = await api<{ run_id: number }>(`/v2/strategies/${encodeURIComponent(specId)}/backtest`, {
          method: "POST",
        });
        saveBacktestRun({ strategy_id: specId, run_id: r.run_id });
        await poll(r.run_id);
      } catch (err) {
        setError(`백테스트를 접수하지 못했어요: ${err instanceof Error ? err.message : String(err)}`);
      }
    }

    start();
  }, [specId]);

  if (error) return <p className="flow-msg err">{error}</p>;
  if (!run || (run.status !== "done" && run.status !== "failed")) {
    return (
      <>
        <div className="flow-run-head">
          <b>백테스트를 돌리고 있어요{run?.status ? ` · ${run.status}` : ""}</b>
        </div>
        <div className="flow-indeterminate" aria-hidden="true" />
      </>
    );
  }
  if (run.status === "failed") {
    return (
      <div className="flow-msg err" role="alert">
        <p style={{ color: "var(--c-bright)" }}>백테스트를 하지 못했어요: {run.reason ?? "이유를 받지 못했어요"}</p>
        <p style={{ marginTop: 8 }}>
          <Link href="/compile?retry=1">전략 다시 요청 — 방금 쓴 문장으로</Link>
        </p>
      </div>
    );
  }
  return <Result run={run} specId={specId} />;
}

function Result({ run, specId }: { run: BacktestV2; specId: string }) {
  const s = run.summary!;
  const names = run.names ?? {};
  const name = (t: string) => names[t] ?? t;
  const trades = run.trades ?? [];
  const wins = trades.filter((t) => t.pnl > 0).length;

  return (
    <>
      <p className="flow-hint" style={{ marginBottom: 12 }}>
        {run.period_start} ~ {run.period_end} · 시작 {won(run.seed_money)} ·{" "}
        <Link href={`/compile/done?strategy=${encodeURIComponent(specId)}`}>전략서 보기</Link>
      </p>

      <div className="flow-tiles">
        <Tile label="최종 수익률" value={pct(s.strategy.total_return)} tone={s.strategy.total_return}
              sub={`최종 평가액 ${won(s.strategy.final_value)}`} />
        <Tile label="연환산 수익률" value={pct(s.strategy.cagr)} tone={s.strategy.cagr ?? 0}
              sub={`KODEX 200 보유 ${pct(s.market_proxy.cagr)}`} />
        <Tile label="최대 낙폭" value={pct(s.strategy.mdd)} tone={-1} sub="평가액 고점 대비 가장 크게 빠진 폭" />
        <Tile label="거래" value={`${trades.length}회`}
              sub={trades.length ? `이긴 거래 ${wins}회 (${pct(wins / trades.length, 0)})` : "사고판 적이 없어요"} />
      </div>

      <h2>비교</h2>
      <div className="flow-table">
        <table style={{ minWidth: 520 }}>
          <thead>
            <tr>
              <th>무엇</th>
              <th className="num">최종 수익률</th>
              <th className="num">연환산</th>
              <th className="num">최대 낙폭</th>
              <th className="num">최종 평가액</th>
            </tr>
          </thead>
          <tbody>
            <Row label="이 전략" s={s.strategy} strong />
            <Row label="같은 종목을 시작일에 사서 들고 있기" s={s.hold_same} />
            <Row label="KODEX 200 사서 들고 있기 (코스피 대신)" s={s.market_proxy} />
          </tbody>
        </table>
      </div>

      <h2>평가액</h2>
      <section className="flow-card" style={{ height: 320 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={run.series ?? []} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
            <XAxis dataKey="date" tick={{ fill: C.muted, fontSize: 11 }} tickLine={false} minTickGap={48} />
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
            <Line type="monotone" dataKey="strategy" name="이 전략" stroke={C.accent} dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="hold_same" name="같은 종목 보유" stroke={C.warn} dot={false} strokeWidth={1.5} />
            <Line type="monotone" dataKey="market_proxy" name="KODEX 200 보유" stroke={C.muted} dot={false} strokeWidth={1.5} />
          </LineChart>
        </ResponsiveContainer>
      </section>

      <h2>거래 기록</h2>
      {trades.length === 0 ? (
        <p className="flow-hint">이 기간에 사고판 거래가 없어요.</p>
      ) : (
        <div className="flow-table">
          <table style={{ minWidth: 640 }}>
            <thead>
              <tr>
                <th>종목</th>
                <th>산 날</th>
                <th className="num">산 값</th>
                <th>판 날</th>
                <th className="num">판 값</th>
                <th className="num">수량</th>
                <th className="num">보유 일수</th>
                <th className="num">손익(비용 · 세금 뺀)</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr key={i}>
                  <td>{name(t.ticker)}</td>
                  <td className="mono">{t.buy_date}</td>
                  <td className="num">{won(t.buy_price)}</td>
                  <td className="mono">{t.sell_date}</td>
                  <td className="num">{won(t.sell_price)}</td>
                  <td className="num">{t.shares.toLocaleString("ko-KR")}주</td>
                  <td className="num">{t.holding_days}일</td>
                  <td className="num" style={{ color: t.pnl >= 0 ? "var(--c-profit)" : "var(--c-loss)" }}>
                    {won(t.pnl)} ({pct(t.return)})
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <dl className="flow-kv" style={{ marginTop: 16 }}>
        <dt>끝까지 들고 있는 종목</dt>
        <dd>
          {Object.keys(run.holdings_at_end ?? {}).length === 0
            ? "없음"
            : Object.entries(run.holdings_at_end ?? {})
                .map(([t, n]) => `${name(t)} ${n.toLocaleString("ko-KR")}주`)
                .join(", ")}
        </dd>
        <dt>체결하지 못한 주문</dt>
        <dd>
          {(run.unfilled ?? []).length === 0
            ? "없음"
            : (run.unfilled ?? []).map((u) => `${name(u.ticker)} ${u.side === "buy" ? "사기" : "팔기"}(${u.decided_on}): ${u.reason}`).join(" · ")}
        </dd>
        <dt>가격 데이터</dt>
        <dd>{run.data_snapshot_asof} 까지의 스냅샷 · 수수료 0.015% · 체결 오차 0.05% · 세금 포함</dd>
      </dl>

      <h2>알아 둘 점</h2>
      <ul>
        {(run.notes ?? []).map((n) => (
          <li key={n} className="flow-hint">
            {n}
          </li>
        ))}
      </ul>
    </>
  );
}

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: number }) {
  const color = tone === undefined ? undefined : tone >= 0 ? "var(--c-profit)" : "var(--c-loss)";
  return (
    <div>
      <span>{label}</span>
      <b style={color ? { color } : undefined}>{value}</b>
      {sub && <small>{sub}</small>}
    </div>
  );
}

function Row({ label, s, strong = false }: { label: string; s: Summary; strong?: boolean }) {
  const tone = (v: number | null) => (v == null ? undefined : v >= 0 ? "var(--c-profit)" : "var(--c-loss)");
  return (
    <tr>
      <td style={strong ? { color: "var(--c-bright)", fontWeight: 600 } : undefined}>{label}</td>
      <td className="num" style={{ color: tone(s.total_return) }}>{pct(s.total_return)}</td>
      <td className="num" style={{ color: tone(s.cagr) }}>{pct(s.cagr)}</td>
      <td className="num" style={{ color: "var(--c-loss)" }}>{pct(s.mdd)}</td>
      <td className="num">{won(s.final_value)}</td>
    </tr>
  );
}
