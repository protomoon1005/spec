"use client";

// 백테스트 — docs/frontend_milestone.md 6단계, 확정 결정 3·4번.
//
// 이번 탭에서 완료한 전략서(spec_id)가 있으면 그 전략서로 백테스트를 돌린다.
// POST /backtest/runs 로 접수하고 3초마다 GET 으로 상태를 받아 한 줄씩 쌓는다.
// 전략서가 없으면 예전처럼 저장된 결과 파일(data/backtest-result.json)을 보여 준다.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, type BacktestRun, loadBacktestRun, loadCompileResult, saveBacktestRun } from "@/lib/api";
import { control, DATA_SOURCE, market, PERIOD_END, PERIOD_START, PROFILE_LABEL, strategy } from "@/lib/data";

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

// 데모 계획 1.4 의 평가 구간 끝. 시작일은 서버 기본값(2023-01-01)을 쓴다.
const REQUEST_PERIOD_END = "2025-12-31";
const POLL_MS = 3000;

// 저장된 결과와 서버 결과를 같은 세 계열, 같은 문장으로 적는다.
const SERIES = [
  { key: "strategy", label: "전략 (3관점 신호 사용)" },
  { key: "control", label: "대조군 (같은 제약, 신호 없음)" },
  { key: "market", label: "시장 (KODEX 200 매수 후 보유)" },
] as const;

type SeriesTotals = Record<(typeof SERIES)[number]["key"], { total: number; mdd: number }>;

export default function BacktestPage() {
  const router = useRouter();
  const [specId, setSpecId] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    const r = loadCompileResult();
    setSpecId(r?.status === "completed" ? r.spec_id : null);
  }, []);

  return (
    <ScaffoldShell>
      <main>
        <h1>백테스트</h1>
        <p>이 화면에서 하는 일: 전략을 과거 데이터로 돌려 본 결과를 확인한다.</p>
        <RequireLogin>
          {() => <Body specId={specId} />}
        </RequireLogin>

        <div className="flow-next">
          <button className="primary" onClick={() => router.push("/confirm")}>
            다음 — 최종 확인
          </button>
        </div>
      </main>
    </ScaffoldShell>
  );
}

function Body({ specId }: { specId: string | null | undefined }) {
  if (specId === undefined) return <p className="flow-state">불러오는 중…</p>;
  return specId ? <LiveRun specId={specId} /> : <Stored />;
}

function LiveRun({ specId }: { specId: string }) {
  const [log, setLog] = useState<string[]>([]);
  const [run, setRun] = useState<BacktestRun | null>(null);
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
          body: { spec_id: specId, period_end: REQUEST_PERIOD_END },
        });
        saveBacktestRun({ spec_id: specId, run_id: r.run_id });
        add(`접수됨: 실행 번호 ${r.run_id}, 상태 ${r.status}`);
        await poll(r.run_id);
      } catch (err) {
        add(`접수 실패 — ${err instanceof Error ? err.message : String(err)}`);
      }
    }

    start();
  }, [specId]);

  return (
    <>
      <p className="flow-hint" style={{ marginBottom: 10 }}>
        전략서 <span className="mono">{specId}</span> 로 백테스트를 돌립니다.
      </p>
      {run && run.status !== "done" && run.status !== "failed" && (
        <p className="flow-state">돌리는 중… 상태 {run.status}</p>
      )}
      <ul className="flow-log" aria-live="polite">
        {log.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
      {run?.status === "done" && <Result run={run} />}
      {run?.status === "failed" && (
        <p className="flow-msg err" role="alert">
          실패 이유: {run.reason}
        </p>
      )}
    </>
  );
}

// 관점 출처 배지. real 은 실제 모델, mock 은 고정 가짜 점수, neutral 은 항상 중립.
const SOURCE_BADGE: Record<string, { label: string; tone: string }> = {
  real: { label: "실모델", tone: "ok" },
  mock: { label: "목업", tone: "warn" },
  neutral: { label: "중립 고정", tone: "muted" },
};
const VIEW_LABEL: Record<string, string> = { market: "시장분석", sentiment: "뉴스 감성", regime: "시장온도" };

function Result({ run }: { run: BacktestRun }) {
  return (
    <>
      <h2>결과 (실행 번호 {run.run_id})</h2>
      {/* 목업 관점이 하나라도 섞였으면 경고한다. 출처 기록이 없는 옛 실행은 전부 목업이었다. */}
      {(!run.scorer_sources || Object.values(run.scorer_sources).includes("mock")) && (
        <p className="flow-msg warn" style={{ marginTop: 0, marginBottom: 16 }}>
          목업 신호가 섞인 실행입니다(관점 출처 참고). 감성은 중립 고정입니다. 결과를 실력으로 읽으면 안 됩니다.
        </p>
      )}
      {run.summary && <SeriesTable totals={run.summary} />}
      <dl className="flow-kv" style={{ marginTop: 16 }}>
        <dt>기간</dt>
        <dd>
          {run.period_start} ~ {run.period_end} (실제로 쓴 마지막 거래일 {run.data_snapshot_asof})
        </dd>
        <dt>관점 출처</dt>
        <dd>
          {run.scorer_sources ? (
            <span className="flow-actions" style={{ gap: 8 }}>
              {Object.entries(run.scorer_sources).map(([view, source]) => {
                const b = SOURCE_BADGE[source] ?? { label: source, tone: "muted" };
                return (
                  <span key={view} style={{ fontFamily: "var(--f-sans)" }}>
                    {VIEW_LABEL[view] ?? view} <span className={`flow-badge ${b.tone}`}>{b.label}</span>
                  </span>
                );
              })}
            </span>
          ) : (
            "기록 없음"
          )}
        </dd>
        <dt>리밸런싱 일정</dt>
        <dd className="prose">{run.schedule?.note}</dd>
        <dt>비중 계산</dt>
        <dd>{run.weight_path}</dd>
        <dt>서버 지표</dt>
        <dd style={{ wordBreak: "break-all" }}>
          <span className="flow-hint">전략 기준, benchmark_cagr 은 시장 </span>
          {JSON.stringify(run.metrics)}
        </dd>
      </dl>
      {/* 리포트는 이 실행 번호로 같은 결과를 받아 그린다. 예전에는 /report 가 늘 데모 결과를
          보여 줘서 "이번 전략서 결과가 아닙니다" 라고 적어 둬야 했다. */}
      {run.status === "done" && (
        <nav className="flow-menu" style={{ marginTop: 20 }} aria-label="결과 자세히 보기">
          <Link href={`/report?run_id=${run.run_id}`}>
            <b>이 실행의 자세한 리포트 보기</b>
            <span>차트, 종목별 비중, 그룹캡 적용, 전략서 내용</span>
          </Link>
          <Link href={`/views?run_id=${run.run_id}`}>
            <b>관점 브리핑 보기</b>
            <span>세 관점의 가중치가 리밸런싱마다 어떻게 바뀌었는지, 종목별 확률</span>
          </Link>
        </nav>
      )}
    </>
  );
}

function Stored() {
  return (
    <>
      <p className="flow-msg" style={{ marginTop: 0 }}>
        이번 탭에서 완료한 전략서가 없어, 미리 저장된 백테스트 결과를 보여 줍니다.
      </p>
      <dl className="flow-kv" style={{ margin: "16px 0" }}>
        <dt>출처</dt>
        <dd>
          data/backtest-result.json ({DATA_SOURCE} 실제 종가, 성향 {PROFILE_LABEL} 기준, 데모 Spec)
        </dd>
        <dt>기간</dt>
        <dd>
          {PERIOD_START} ~ {PERIOD_END}
        </dd>
      </dl>
      <SeriesTable totals={{ strategy, control, market }} />
      <p style={{ marginTop: 16 }}>
        <Link href="/report">데모 리포트 보기</Link> <span className="flow-hint">위와 같은 데모 결과입니다</span>
      </p>
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
