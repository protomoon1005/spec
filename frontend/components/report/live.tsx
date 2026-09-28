"use client";

// /report?run_id= — 사용자가 돌린 실행 한 건을 리포트로 그린다.
//
// 토큰이 브라우저 저장소에 있어서(lib/api.ts) 서버가 대신 받아 올 수 없다. 그래서
// 여기서 받는다. 받기 전에는 로딩 화면만 보인다 — 정적 데모 결과를 먼저 그렸다가
// 바꾸면, 잠깐이라도 사용자가 만들지 않은 전략의 수치가 보인다.
//
// 로그인 확인은 components/require-login 을 쓰지 않는다. 그 컴포넌트는 기본 HTML 막대를
// 화면 위에 그리고 곧바로 /login 으로 보내는데, 리포트는 자기 디자인 안에서 이유를 적고
// 사용자가 고르게 한다.

import Link from "next/link";
import { useEffect, useState } from "react";

import Report from "@/components/report";
import { api, ApiError, loadSession } from "@/lib/api";
import { buildReport, type Report as ReportData } from "@/lib/data";
import { metaFromSpec, ReportUnavailable, resultFromRun, type RunDetail, type SpecDetail } from "@/lib/report-from-run";
import { C, MONO, R, SANS } from "./tokens";

type State =
  | { kind: "loading" }
  | { kind: "login"; message: string }
  | { kind: "error"; message: string }
  | { kind: "ok"; report: ReportData };

export default function LiveReport({ runId }: { runId: string }) {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const id = Number(runId);
    if (!Number.isInteger(id) || id <= 0) {
      setState({ kind: "error", message: `실행 번호가 올바르지 않습니다: ${runId}` });
      return;
    }
    if (!loadSession()) {
      setState({ kind: "login", message: "실행 결과는 그 실행을 돌린 사람만 볼 수 있습니다. 로그인해 주세요." });
      return;
    }

    // 개발 모드는 effect 를 두 번 돌린다. 먼저 돈 쪽이 늦게 끝나 결과를 덮어쓰지 않게 한다.
    let alive = true;
    (async () => {
      try {
        const run = await api<RunDetail>(`/backtest/runs/${id}`);
        const result = resultFromRun(run); // 끝나지 않았거나 실패했으면 여기서 멈춘다
        const spec = await api<SpecDetail>(`/specs/${run.spec_id}`);
        const report = buildReport(result, metaFromSpec(spec, run));
        if (alive) setState({ kind: "ok", report });
      } catch (err) {
        if (alive) setState(describe(err));
      }
    })();
    return () => {
      alive = false;
    };
  }, [runId]);

  if (state.kind === "ok") return <Report report={state.report} />;
  return <Notice state={state} runId={runId} />;
}

function describe(err: unknown): State {
  if (err instanceof ReportUnavailable) return { kind: "error", message: err.message };
  if (err instanceof ApiError) {
    if (err.status === 401) return { kind: "login", message: "로그인이 만료되었습니다. 다시 로그인해 주세요." };
    if (err.status === 404) {
      return { kind: "error", message: "실행 기록을 찾을 수 없습니다. 없는 번호이거나 다른 사람이 돌린 실행입니다." };
    }
    return { kind: "error", message: `서버가 응답을 거절했습니다 — ${err.message}` };
  }
  return { kind: "error", message: `결과를 불러오지 못했습니다 — ${String(err)}` };
}

// 리포트와 같은 바탕·글꼴로 그린다. 로딩에서 리포트로 넘어갈 때 화면이 튀지 않게.
function Notice({ state, runId }: { state: Exclude<State, { kind: "ok" }>; runId: string }) {
  const title = state.kind === "loading" ? "결과를 불러오는 중" : state.kind === "login" ? "로그인 필요" : "리포트를 그릴 수 없습니다";
  const tone = state.kind === "error" ? C.loss : state.kind === "login" ? C.warn : C.accent;
  return (
    <div style={{ background: C.bg, fontFamily: SANS, color: C.text, minHeight: "100vh" }} className="flex flex-col">
      <main className="max-w-[720px] mx-auto w-full px-6 py-16">
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted, letterSpacing: "0.03em" }}>
          백테스트 리포트 · 실행 #{runId}
        </div>
        <h1 style={{ fontFamily: MONO, fontSize: 18, fontWeight: 600, color: tone, margin: "10px 0 14px" }}>{title}</h1>
        {state.kind === "loading" ? (
          <p style={{ fontSize: 13, color: C.muted, lineHeight: 1.7 }}>실행 기록과 전략서를 받아 오고 있습니다.</p>
        ) : (
          <p
            role="alert"
            style={{
              fontSize: 13,
              color: C.text,
              lineHeight: 1.7,
              background: C.surface,
              border: `1px solid ${C.border}`,
              borderRadius: R.card,
              padding: "14px 16px",
            }}
          >
            {state.message}
          </p>
        )}
        {state.kind !== "loading" && (
          <div className="flex gap-4 flex-wrap" style={{ marginTop: 20, fontFamily: MONO, fontSize: 12 }}>
            {state.kind === "login" && <NavLink href="/login">로그인</NavLink>}
            <NavLink href="/backtest">백테스트 화면으로</NavLink>
            <NavLink href="/report">데모 결과 보기</NavLink>
          </div>
        )}
      </main>
    </div>
  );
}

function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link href={href} style={{ color: C.accent, textDecoration: "none", borderBottom: `1px solid ${C.accent}55` }}>
      {children}
    </Link>
  );
}
