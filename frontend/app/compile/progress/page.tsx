"use client";

// 전략 진행 상황 — docs/frontend_milestone.md 4단계.
//
// 작업 번호로 스트림을 열어 상태가 바뀔 때마다 한 줄씩 쌓는다. 2분 넘게 걸려서
// 줄이 쌓이는 게 보여야 멈춘 게 아니란 걸 안다.
// 끝나면 결과에 따라 완료 / 되묻기 화면으로 넘기고, 실패와 시간 초과는 여기서 보여 준다.

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { saveCompileResult, streamJob, type CompileResult, type StreamEvent } from "@/lib/api";

export default function ProgressPage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>전략 진행 상황</h1>
        <p>이 화면에서 하는 일: 전략서 생성이 어디까지 됐는지 지켜본다.</p>
        <RequireLogin>
          {() => (
            // useSearchParams 는 Suspense 안에서만 쓸 수 있다 (Next.js 규칙).
            <Suspense fallback={<p className="flow-state">불러오는 중…</p>}>
              <Progress />
            </Suspense>
          )}
        </RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Progress() {
  const router = useRouter();
  const jobId = useSearchParams().get("job");
  const [lines, setLines] = useState<string[]>([]);
  const [ended, setEnded] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!jobId) return;
    const abort = new AbortController();
    const started = Date.now();
    // 개발 모드는 effect 를 두 번 돌린다. 첫 번째 실행이 남긴 줄을 지우고 시작한다.
    setLines([]);
    // 서버는 작업이 도는 동안 PENDING 한 줄만 보낸다(시작 상태를 따로 알리지 않는다).
    // 초가 올라가야 멈춘 게 아니란 걸 안다.
    const tick = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    const log = (line: string) => {
      const sec = Math.round((Date.now() - started) / 1000);
      setLines((prev) => [...prev, `${sec}초  ${line}`]);
    };

    function onEvent({ event, data }: StreamEvent) {
      const d = data as { state?: string; result?: CompileResult; error?: string };
      if (event === "status") {
        log(`상태: ${d.state}`);
      } else if (event === "complete") {
        log(`끝: ${d.state}`);
        if (d.state !== "SUCCESS" || !d.result) {
          // 작업 자체가 죽은 경우. 사용자에게 보여 줄 실패(failed)와 다르다.
          setEnded(`작업 오류: ${d.error ?? JSON.stringify(data)}`);
          return;
        }
        const result = d.result;
        if (result.status === "failed") {
          setEnded(`전략서를 만들 수 없습니다: ${result.reason}`);
          return;
        }
        saveCompileResult(result);
        router.push(result.status === "completed" ? "/compile/done" : "/compile/answer");
      } else if (event === "timeout") {
        log("시간 초과");
        setEnded("시간 초과로 연결이 끊겼습니다. 서버에서는 계속 돌고 있을 수 있습니다.");
      }
    }

    log(`작업 번호 ${jobId} 연결`);
    streamJob(jobId, onEvent, abort.signal).catch((err) => {
      if (!abort.signal.aborted) setEnded(`연결 실패: ${err instanceof Error ? err.message : String(err)}`);
    });
    return () => {
      abort.abort();
      clearInterval(tick);
    };
  }, [jobId, router]);

  if (!jobId) {
    return (
      <div className="flow-empty">
        <strong>작업 번호가 없습니다.</strong>
        <Link href="/compile">다시 요청</Link>
      </div>
    );
  }

  return (
    <>
      {!ended && (
        <div className="flow-card" style={{ display: "flex", alignItems: "baseline", gap: 16, flexWrap: "wrap", marginBottom: 12 }}>
          <span className="mono" style={{ fontSize: 32, fontWeight: 600, color: "var(--c-bright)", lineHeight: 1 }}>
            {elapsed}
            <span style={{ fontSize: 14, color: "var(--c-muted)", marginLeft: 4 }}>초</span>
          </span>
          <span className="flow-state" style={{ padding: 0 }}>
            진행 중… 2분 이상 걸립니다. 이 화면을 닫지 마세요.
          </span>
        </div>
      )}
      <ul className="flow-log" aria-live="polite">
        {lines.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
      {ended && (
        <>
          <p className="flow-msg err" role="alert">
            {ended}
          </p>
          <div className="flow-next">
            <Link href="/compile">다시 요청</Link>
          </div>
        </>
      )}
    </>
  );
}
