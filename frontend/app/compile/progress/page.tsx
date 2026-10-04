"use client";

// 전략 진행 상황 — docs/frontend_milestone.md 4단계.
//
// 작업 번호로 스트림을 열어 상태가 바뀔 때마다 한 줄씩 쌓는다. 2분 넘게 걸려서
// 줄이 쌓이는 게 보여야 멈춘 게 아니란 걸 안다.
// 끝나면 결과에 따라 완료 / 되묻기 화면으로 넘기고, 실패와 시간 초과는 여기서 보여 준다.
//
// 서버는 도는 동안 PENDING 한 줄과 끝 결과만 보낸다 — 지금 몇 번째 단계인지는 모른다.
// 그래서 만드는 순서(docs/human_manual.md 2.3)는 설명으로만 보여 주고 현재 단계에 불을
// 켜지 않는다. 지어낸 진행률보다 흐르는 막대와 경과 시간이 정직하다. 단계를 켜려면
// m1/pipeline.py 가 단계마다 작업 상태를 남겨야 한다.
// 서버가 보낸 줄(작업 번호 · PENDING …)은 개발용이라 "연결 기록" 으로 접어 둔다.

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import {
  loadCompileRequest,
  saveCompileResult,
  streamJob,
  type CompileResult,
  type StreamEvent,
} from "@/lib/api";

export default function ProgressPage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>전략 진행 상황</h1>
        <p>이 화면에서 하는 일: 전략서 생성이 어디까지 됐는지 지켜본다.</p>
        <RequireLogin>
          {() => (
            // useSearchParams 는 Suspense 안에서만 쓸 수 있다 (Next.js 규칙).
            <Suspense fallback={<p>불러오는 중…</p>}>
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
  const [request, setRequest] = useState<string | null>(null);

  useEffect(() => setRequest(loadCompileRequest()), []);

  useEffect(() => {
    if (!jobId) return;
    const abort = new AbortController();
    const started = Date.now();
    // 개발 모드는 effect 를 두 번 돌린다. 첫 번째 실행이 남긴 줄을 지우고 시작한다.
    setLines([]);
    // 서버는 작업이 도는 동안 PENDING 한 줄만 보낸다(시작 상태를 따로 알리지 않는다).
    // 초가 올라가야 멈춘 게 아니란 걸 안다.
    const tick = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    // 끝나면 시계를 멈춘다. 실패 화면에서 초가 계속 올라가면 아직 도는 것처럼 보인다.
    const end = (reason: string) => {
      clearInterval(tick);
      setEnded(reason);
    };
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
          end(`작업 오류: ${d.error ?? JSON.stringify(data)}`);
          return;
        }
        const result = d.result;
        if (result.status === "failed") {
          end(`전략서를 만들 수 없습니다: ${result.reason}`);
          return;
        }
        saveCompileResult(result);
        router.push(result.status === "completed" ? "/compile/done" : "/compile/answer");
      } else if (event === "timeout") {
        log("시간 초과");
        end("시간 초과로 연결이 끊겼습니다. 서버에서는 계속 돌고 있을 수 있습니다.");
      }
    }

    log(`작업 번호 ${jobId} 연결`);
    streamJob(jobId, onEvent, abort.signal).catch((err) => {
      if (!abort.signal.aborted) end(`연결 실패: ${err instanceof Error ? err.message : String(err)}`);
    });
    return () => {
      abort.abort();
      clearInterval(tick);
    };
  }, [jobId, router]);

  if (!jobId) {
    return (
      <p>
        작업 번호가 없습니다. <Link href="/compile">다시 요청</Link>
      </p>
    );
  }

  return (
    <>
      <div className="flow-run-head">
        <p className="flow-run-title">{ended ? "전략서를 만들지 못했어요" : "전략서를 만들고 있어요"}</p>
        <span className="flow-run-clock">{clock(elapsed)}</span>
      </div>
      {!ended && <div className="flow-indeterminate" aria-hidden="true" />}

      {request && (
        <p className="flow-run-request">
          <span>요청</span> &ldquo;{request}&rdquo;
        </p>
      )}

      {ended ? (
        <div className="flow-alert" role="alert">
          <p>{ended}</p>
          <Link href="/compile?retry=1">다시 요청 — 방금 쓴 문장으로</Link>
        </div>
      ) : (
        <>
          <p className="flow-steps-title">이 순서로 만들어요</p>
          <div className="flow-steps">
            {STEPS.map((step, i) => (
              <div key={step.name}>
                <span className="flow-steps-num">{i + 1}</span>
                <span>{step.name}</span>
                {step.ai && <span className="flow-tag">AI</span>}
              </div>
            ))}
          </div>
          <p className="flow-hint">보통 2분 넘게 걸려요. 이 화면을 닫지 마세요.</p>
        </>
      )}

      <details className="flow-log">
        <summary>연결 기록</summary>
        <ul>
          {lines.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      </details>
    </>
  );
}

// docs/human_manual.md 2.3 "처리 순서". AI 가 쓰이는 단계에 표시한다.
const STEPS = [
  { name: "성향 확인", ai: false },
  { name: "요청 이해", ai: true },
  { name: "되물을 게 있는지 판단", ai: false },
  { name: "담을 수 있는 종목 고르기", ai: false },
  { name: "전략서 작성", ai: true },
  { name: "규칙 검사 후 저장", ai: false },
];

// 87 → 01:27
function clock(sec: number): string {
  return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
}
