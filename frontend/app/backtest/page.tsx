"use client";

// 백테스트.
//
// 이번 탭에서 완료한 전략서(strategy_id)가 있으면 그 전략서로 백테스트를 돌린다.
// POST /backtest/runs 로 접수하고 3초마다 GET 으로 상태를 받아 한 줄씩 쌓는다.
// 전략서가 없으면 전략 요청 화면으로 안내한다.
//
// 도는 동안은 경과 시계와 흐르는 막대, 상태 로그는 "실행 기록" 으로 접는다.
// 실패 중 "러너가 지원하지 않는 리밸런싱 규칙" 은 풀어 쓰고 원문은 작게 남긴다.

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, type BacktestRun, loadBacktestRun, loadCompileResult, saveBacktestRun } from "@/lib/api";

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

// 데모 계획 1.4 의 평가 구간 끝. 시작일은 서버 기본값(2023-01-01)을 쓴다.
const REQUEST_PERIOD_END = "2025-12-31";
const POLL_MS = 3000;

const SERIES = [
  { key: "strategy", label: "전략" },
  { key: "market", label: "시장 (KODEX 200 매수 후 보유)" },
] as const;

type SeriesTotals = Record<(typeof SERIES)[number]["key"], { total: number; mdd: number }>;

export default function BacktestPage() {
  const [specId, setSpecId] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    const r = loadCompileResult();
    setSpecId(r?.status === "completed" ? r.strategy_id : null);
  }, []);

  return (
    <ScaffoldShell>
      <main>
        <h1>백테스트</h1>
        <p>이 화면에서 하는 일: 전략을 과거 데이터로 돌려 본 결과를 확인한다.</p>
        <RequireLogin>
          {() => <Body specId={specId} />}
        </RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Body({ specId }: { specId: string | null | undefined }) {
  if (specId === undefined) return <p className="flow-state">불러오는 중…</p>;
  if (specId) return <LiveRun specId={specId} />;
  return (
    <p className="flow-msg" style={{ marginTop: 0 }}>
      이번 탭에서 완료한 전략서가 없어요. <Link href="/compile">전략 요청</Link>에서 전략서를 먼저 만들어 주세요.
    </p>
  );
}

function LiveRun({ specId }: { specId: string }) {
  const [log, setLog] = useState<string[]>([]);
  const [run, setRun] = useState<BacktestRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const finished = run?.status === "done" || run?.status === "failed" || error !== null;
  const started = useRef(false);
  const alive = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // 화면을 떠나면 폴링을 멈춘다. 접수 effect 와 떼어 두는 이유는 아래 주석.
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      clearTimeout(timer.current);
    };
  }, []);

  // 끝날 때까지 시계를 돌린다.
  useEffect(() => {
    if (finished) return;
    const t0 = Date.now();
    const tick = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(tick);
  }, [finished]);

  useEffect(() => {
    // 개발 모드는 effect 를 두 번 돌린다. 접수가 두 번 되지 않게 막는다 — 그래서
    // 이 effect 는 두 번째에 정리 함수를 돌려주지 못하고, 정리는 위 effect 가 맡는다.
    if (started.current) return;
    started.current = true;

    const add = (line: string) => setLog((prev) => [...prev, `${new Date().toLocaleTimeString()} ${line}`]);

    async function poll(runId: number) {
      try {
        const r = await api<BacktestRun>(`/backtest/runs/${runId}`);
        add(`상태: ${r.status}`);
        setRun(r);
        if (alive.current && r.status !== "done" && r.status !== "failed") {
          timer.current = setTimeout(() => poll(runId), POLL_MS);
        }
      } catch (err) {
        add(`조회 실패 — ${err instanceof Error ? err.message : String(err)}`);
        setError(`결과를 받지 못했어요: ${err instanceof Error ? err.message : String(err)}`);
      }
    }

    async function start() {
      const saved = loadBacktestRun(specId);
      if (saved) {
        add(`이 전략서로 접수한 실행 ${saved.run_id} 를 다시 조회합니다`);
        await poll(saved.run_id);
        return;
      }
      try {
        const r = await api<BacktestRun>("/backtest/runs", {
          body: { strategy_id: specId, period_end: REQUEST_PERIOD_END },
        });
        saveBacktestRun({ strategy_id: specId, run_id: r.run_id });
        add(`접수됨: 실행 번호 ${r.run_id}, 상태 ${r.status}`);
        await poll(r.run_id);
      } catch (err) {
        add(`접수 실패 — ${err instanceof Error ? err.message : String(err)}`);
        setError(`백테스트를 접수하지 못했어요: ${err instanceof Error ? err.message : String(err)}`);
      }
    }

    start();
  }, [specId]);

  const done = run?.status === "done";
  const failed = run?.status === "failed" || error !== null;

  return (
    <>
      <p className="flow-hint" style={{ marginBottom: 10 }}>
        전략서 <span className="mono">{specId}</span> 로 백테스트를 돌립니다.
      </p>
      {!finished && (
        <>
          <div className="flow-run-head">
            <b>백테스트를 돌리고 있어요{run?.status ? ` · ${run.status}` : ""}</b>
            <span className="mono flow-run-clock">{clock(elapsed)}</span>
          </div>
          <div className="flow-indeterminate" aria-hidden="true" />
        </>
      )}
      {done && <Result run={run} />}
      {failed && <Failure reason={run?.reason ?? error} />}

      <details className="flow-details">
        <summary>실행 기록</summary>
        <ul className="flow-log" aria-live="polite">
          {log.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      </details>
    </>
  );
}

// 서버 문장을 풀어 쓰고 원문은 작게 남긴다.
function Failure({ reason }: { reason: string | null | undefined }) {
  const raw = reason ?? "이유를 받지 못했어요";
  const rebalance = raw.startsWith("러너가 지원하지 않는 리밸런싱 규칙");
  return (
    <div className="flow-msg err" role="alert">
      <p style={{ color: "var(--c-bright)" }}>
        {rebalance
          ? "이 전략서의 리밸런싱 규칙은 아직 백테스트할 수 없어요. 지금은 리밸런싱 블록 5개(매달 첫 거래일 · 매달 15일 · 분기 · 매주 · 비중 이탈 5%)만 돌릴 수 있어요."
          : `실패 이유: ${raw}`}
      </p>
      {rebalance && (
        <p className="mono flow-hint" style={{ marginTop: 6, fontSize: 11 }}>
          {raw}
        </p>
      )}
      <p style={{ marginTop: 8 }}>
        <Link href="/compile?retry=1">전략 다시 요청 — 방금 쓴 문장으로</Link>
      </p>
    </div>
  );
}

// 87 → 01:27
function clock(sec: number): string {
  return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
}

function Result({ run }: { run: BacktestRun }) {
  return (
    <>
      <h2>결과 (실행 번호 {run.run_id})</h2>
      {run.summary && <SeriesTable totals={run.summary} />}
      <dl className="flow-kv" style={{ marginTop: 16 }}>
        <dt>기간</dt>
        <dd>
          {run.period_start} ~ {run.period_end} (실제로 쓴 마지막 거래일 {run.data_snapshot_asof})
        </dd>
        <dt>리밸런싱 일정</dt>
        <dd className="prose">{run.schedule?.note}</dd>
        <dt>서버 지표</dt>
        <dd>
          <ServerMetrics m={run.metrics} />
        </dd>
      </dl>
      {run.status === "done" && (
        <nav className="flow-menu" style={{ marginTop: 20 }} aria-label="결과 자세히 보기">
          <Link href={`/report?run_id=${run.run_id}`}>
            <b>이 실행의 자세한 리포트 보기</b>
            <span>수익 곡선, 핵심 지표, 리밸런싱 기록</span>
          </Link>
        </nav>
      )}
    </>
  );
}

const tone = (v: number) => (v >= 0 ? "var(--c-profit)" : "var(--c-loss)");

function SeriesTable({ totals }: { totals: SeriesTotals }) {
  return (
    <div className="flow-table">
      <table style={{ minWidth: 440 }}>
        <thead>
          <tr>
            <th>계열</th>
            <th className="num">최종 수익률</th>
            <th className="num">최대 낙폭</th>
          </tr>
        </thead>
        <tbody>
          {SERIES.map(({ key, label }) => (
            <tr key={key}>
              <td style={key === "strategy" ? { color: "var(--c-bright)", fontWeight: 600 } : undefined}>{label}</td>
              <td className="num" style={{ color: tone(totals[key].total) }}>
                {pct(totals[key].total)}
              </td>
              <td className="num" style={{ color: "var(--c-loss)" }}>
                {pct(totals[key].mdd)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// 서버가 계산한 연환산 지표(backtest_metrics). 전략 기준이고 시장 CAGR 만 시장(KODEX 200) 것이다.
// 승률은 정의가 문서에 없어 비어 있다(backend/app/backtest/service.py).
function ServerMetrics({ m }: { m: BacktestRun["metrics"] }) {
  if (!m) return <span className="flow-hint">없음</span>;
  const num = (v: number | null, f: (x: number) => string) => (v == null ? "—" : f(v));
  const ratio = (v: number) => v.toFixed(2);
  return (
    <span className="mono">
      CAGR {num(m.cagr, pct)} · 샤프 {num(m.sharpe, ratio)} · 소르티노 {num(m.sortino, ratio)} · MDD{" "}
      {num(m.mdd, pct)} <span className="flow-hint">· 시장 CAGR {num(m.benchmark_cagr, pct)}</span>
    </span>
  );
}
