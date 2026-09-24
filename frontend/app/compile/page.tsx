"use client";

// 전략 요청 — docs/frontend_milestone.md 4단계.
//
// 문장을 보내면 서버가 작업 번호만 바로 주고, 실제 컴파일은 뒤에서 돈다.
// 결과는 진행 상황 화면이 스트림으로 받는다.

import { useRouter } from "next/navigation";
import { useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api } from "@/lib/api";

export default function CompilePage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>전략 요청</h1>
        <p>이 화면에서 하는 일: 원하는 투자 방식을 문장으로 적어 전략서 생성을 요청한다.</p>
        <RequireLogin>{() => <Request />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Request() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function send() {
    setBusy(true);
    setMessage(null);
    try {
      const { job_id } = await api<{ job_id: string }>("/specs/compile", { body: { input_prompt: text.trim() } });
      router.push(`/compile/progress?job=${encodeURIComponent(job_id)}`);
    } catch (err) {
      setMessage(`요청 실패: ${err instanceof Error ? err.message : String(err)}`);
      setBusy(false);
    }
  }

  return (
    <>
      <p>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={4}
          cols={60}
          placeholder="예: 안전하게 채권 위주로 굴리고 매달 정리해줘"
          disabled={busy}
        />
      </p>
      <p>
        <button onClick={send} disabled={busy || !text.trim()}>
          보내기
        </button>{" "}
        전략서 생성은 2분 이상 걸립니다.
      </p>
      {message && <p>{message}</p>}
    </>
  );
}
