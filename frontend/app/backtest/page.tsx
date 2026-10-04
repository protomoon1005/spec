"use client";

// 백테스트 — docs/frontend_milestone.md 6단계, 확정 결정 3·4번.
//
// 이번 탭에서 완료한 전략서(spec_id)가 있으면 그 전략서로 백테스트를 돌린다.
// POST /backtest/runs 로 접수하고 3초마다 GET 으로 상태를 받는다.
// 전략서가 없으면 예전처럼 저장된 결과 파일(data/backtest-result.json)을 데모로 보여 준다.
//
// 결과는 전략 · 대조군 · 시장 세 칸으로 나란히, 그리고 "신호를 쓴 효과"(전략 − 대조군)를
// 한 줄로 뽑는다 — 같은 제약에서 3관점 신호만 뺀 것이 대조군이라, 그 차이가 이 프로젝트가
// 보여 주려는 값이다. 관점 출처(목업 · 중립 · 실제)는 서버의 scorer_sources 로 문장을
// 만든다. 실물 스코어러가 붙으면 문장이 저절로 바뀐다.
// 상태 로그와 실행 세부(일정 · 비중 경로 · 원래 지표)는 개발용이라 접어 둔다.
// "다음 — 최종 확인" 은 결과가 나왔을 때만 누를 수 있다.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, type BacktestRun, loadBacktestRun, loadCompileResult, saveBacktestRun } from "@/lib/api";
import { control, DATA_SOURCE, market, PERIOD_END, PERIOD_START, PROFILE_LABEL, strategy } from "@/lib/data";

// 데모 계획 1.4 의 평가 구간 끝. 시작일은 서버 기본값(2023-01-01)을 쓴다.
const REQUEST_PERIOD_END = "2025-12-31";
const POLL_MS = 3000;

const SERIES = [
  { key: "strategy", label: "전략", sub: "3관점 신호 사용" },
  { key: "control", label: "대조군", sub: "같은 제약 · 신호 없음" },
  { key: "market", label: "시장", sub: "KODEX 200 매수 후 보유" },
] as const;

type Totals = Record<(typeof SERIES)[number]["key"], { total: number; cagr: number; mdd: number }>;

const signedPct = (v: number) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
const signedPp = (v: number) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%p`;

export default function BacktestPage() {
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
        <RequireLogin>{() => <Body specId={specId} />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Body({ specId }: { specId: string | null | undefined }) {
  if (specId === undefined) return <p>불러오는 중…</p>;
  return specId ? <LiveRun specId={specId} /> : <Stored />;
}

function LiveRun({ specId }: { specId: string }) {
  const router = useRouter();
  const [log, setLog] = useState<string[]>([]);
  const [run, setRun] = useState<BacktestRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [specName, setSpecName] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const started = useRef(false);
  const alive = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const finished = run?.status === "done" || run?.status === "failed" || error !== null;

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
    const t0 = Date.now() - elapsed * 1000;
    const tick = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(tick);
    // elapsed 는 다시 시작할 때 이어 세려고 읽기만 한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished]);

  useEffect(() => {
    api<{ name: string }>(`/specs/${specId}`)
      .then((s) => setSpecName(s.name))
      .catch(() => {});
  }, [specId]);

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
          body: { spec_id: specId, period_end: REQUEST_PERIOD_END },
        });
        saveBacktestRun({ spec_id: specId, run_id: r.run_id });
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
      <div className="flow-run-head">
        <p className={`flow-run-title ${done ? "flow-done-title" : failed ? "flow-fail-title" : ""}`}>
          {done && <span aria-hidden="true">✓ </span>}
          {failed && <span aria-hidden="true">✕ </span>}
          {done ? "백테스트를 마쳤어요" : failed ? "백테스트를 돌리지 못했어요" : "백테스트를 돌리고 있어요"}
        </p>
        {!finished && <span className="flow-run-clock">{clock(elapsed)}</span>}
      </div>
      {!finished && <div className="flow-indeterminate" aria-hidden="true" />}
      <p className="flow-hint">
        {run?.period_start ?? "2023-01-01"} ~ {run?.period_end ?? REQUEST_PERIOD_END}
        {specName && ` · ${specName}`}
      </p>

      {done && run.summary && (
        <>
          <Comparison totals={run.summary} />
          <SourceNote sources={run.scorer_sources} />
        </>
      )}

      {failed && <Failure reason={run?.reason ?? error} />}

      <div className="flow-actions">
        {done && (
          <Link className="flow-button" href={`/report?run_id=${run.run_id}`}>
            자세한 리포트 보기
          </Link>
        )}
        <button className={done ? "flow-link" : undefined} onClick={() => router.push("/confirm")} disabled={!done}>
          다음 — 최종 확인
        </button>
      </div>

      <details className="flow-log">
        <summary>실행 정보</summary>
        <ul>
          {run && <li>실행 번호 {run.run_id}</li>}
          {run?.data_snapshot_asof && <li>실제로 쓴 마지막 거래일 {run.data_snapshot_asof}</li>}
          {run?.schedule?.note && <li>리밸런싱 일정: {run.schedule.note}</li>}
          {run?.weight_path && <li>비중 계산: {run.weight_path}</li>}
          {run?.metrics && <li>서버 지표 (전략 기준, benchmark_cagr 은 시장): {JSON.stringify(run.metrics)}</li>}
          {log.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      </details>
    </>
  );
}

// 세 칸 비교 + 신호를 쓴 효과.
function Comparison({ totals }: { totals: Totals }) {
  const edge = totals.strategy.total - totals.control.total;
  return (
    <>
      <div className="flow-series">
        {SERIES.map(({ key, label, sub }) => {
          const t = totals[key];
          return (
            <div key={key} className={key === "strategy" ? "flow-series-main" : undefined}>
              <span className="flow-series-label">{label}</span>
              <span className="flow-series-sub">{sub}</span>
              <span className={`flow-series-total ${t.total >= 0 ? "up" : "down"}`}>{signedPct(t.total)}</span>
              <span className="flow-series-meta">
                연 {signedPct(t.cagr)} · 최대 낙폭 {signedPct(-Math.abs(t.mdd))}
              </span>
            </div>
          );
        })}
      </div>
      <p className="flow-edge">
        신호를 쓴 효과 <span>전략 − 대조군</span>{" "}
        <strong className={edge >= 0 ? "up" : "down"}>{signedPp(edge)}</strong>
      </p>
    </>
  );
}

const VIEW_LABEL: Record<string, string> = { market: "시장분석", regime: "시장온도", sentiment: "뉴스 감성" };
const SOURCE_LABEL: Record<string, string> = { mock: "시험용 점수", neutral: "중립", real: "실제 모델" };

// {"market":"mock","sentiment":"neutral","regime":"mock"}
//   → "시장분석·시장온도는 시험용 점수, 뉴스 감성은 중립으로 돌렸어요."
function SourceNote({ sources }: { sources: Record<string, string> | null }) {
  if (!sources) return null;
  const groups = new Map<string, string[]>();
  for (const [view, source] of Object.entries(sources)) {
    groups.set(source, [...(groups.get(source) ?? []), VIEW_LABEL[view] ?? view]);
  }
  if ([...groups.keys()].every((s) => s === "real")) return null;
  const parts = [...groups.entries()].map(
    ([source, views]) => `${views.join("·")}${views.length > 1 ? "는" : "은"} ${SOURCE_LABEL[source] ?? source}`,
  );
  return (
    <p className="flow-note">
      ⓘ {parts.join(", ")}으로 돌렸어요. 실제 모델이 아닌 관점이 있어 결과를 실력으로 읽으면 안 돼요.
    </p>
  );
}

// 서버 문장을 풀어 쓰고 원문은 작게 남긴다.
function Failure({ reason }: { reason: string | null | undefined }) {
  const raw = reason ?? "이유를 받지 못했어요";
  const rebalance = raw.startsWith("러너가 지원하지 않는 리밸런싱 규칙");
  return (
    <div className="flow-alert" role="alert">
      <p>
        {rebalance
          ? "이 전략서의 리밸런싱 규칙은 아직 백테스트할 수 없어요. 지금은 매주 · 매달 첫 거래일 규칙만 돌릴 수 있어요."
          : raw}
      </p>
      {rebalance && <p className="flow-alert-raw">{raw}</p>}
      <Link href="/compile?retry=1">전략 다시 요청 — 방금 쓴 문장으로</Link>
    </div>
  );
}

function Stored() {
  const router = useRouter();
  return (
    <>
      <div className="flow-run-head">
        <p className="flow-run-title">데모 결과</p>
        <span className="flow-badge">저장된 결과</span>
      </div>
      <p className="flow-hint">
        이번 탭에서 만든 전략서가 없어 미리 저장된 결과를 보여 줘요 · {PERIOD_START} ~ {PERIOD_END} ·{" "}
        {DATA_SOURCE} 실제 종가 · 성향 {PROFILE_LABEL} 기준 데모 전략서
      </p>
      <Comparison totals={{ strategy, control, market }} />
      <div className="flow-actions">
        <Link className="flow-button" href="/report">
          데모 리포트 보기
        </Link>
        <button className="flow-link" onClick={() => router.push("/confirm")}>
          다음 — 최종 확인
        </button>
      </div>
    </>
  );
}

// 87 → 01:27
function clock(sec: number): string {
  return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
}
