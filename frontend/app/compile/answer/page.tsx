"use client";

// 되묻기 — docs/frontend_milestone.md 4단계.
//
// 서버가 준 질문과 선택지를 그대로 보여 준다. 선택지를 누르거나 직접 적어 답하면
// session_id 와 함께 다시 요청하고, 진행 상황 화면으로 돌아간다.
// 직접 입력칸은 선택지가 있어도 둔다 — "담을 종목이 없다" 질문의 선택지는 답이
// 아니라 거절 사유 목록이라, 사용자가 다른 조건을 적어야 한다.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, loadCompileResult } from "@/lib/api";

type Pending = { session_id: string; question: string; choices: string[] };

export default function AnswerPage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>되묻기</h1>
        <p>이 화면에서 하는 일: 전략서를 만들기에 정보가 모자라 시스템이 묻는 질문에 답한다.</p>
        <RequireLogin>{() => <Answer />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Answer() {
  const router = useRouter();
  const [pending, setPending] = useState<Pending | null | undefined>(undefined);
  const [text, setText] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const r = loadCompileResult();
    setPending(r?.status === "need_answer" ? r : null);
  }, []);

  async function send(answer: string) {
    if (!pending) return;
    setBusy(true);
    setMessage(null);
    try {
      const { job_id } = await api<{ job_id: string }>("/specs/compile", {
        body: { session_id: pending.session_id, answer },
      });
      router.push(`/compile/progress?job=${encodeURIComponent(job_id)}`);
    } catch (err) {
      setMessage(`답 보내기 실패: ${err instanceof Error ? err.message : String(err)}`);
      setBusy(false);
    }
  }

  if (pending === undefined) return <p>불러오는 중…</p>;
  if (pending === null) {
    return (
      <p>
        답할 질문이 없습니다. <Link href="/compile">전략 요청으로</Link>
      </p>
    );
  }

  return (
    <>
      <p>질문: {pending.question}</p>
      {pending.choices.length > 0 && (
        <>
          <p>선택지 (누르면 그대로 답으로 보냅니다)</p>
          <ul>
            {pending.choices.map((c) => (
              <li key={c}>
                <button onClick={() => send(c)} disabled={busy}>
                  {c}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      <p>
        <label>
          직접 입력{" "}
          <input value={text} onChange={(e) => setText(e.target.value)} size={50} disabled={busy} />
        </label>{" "}
        <button onClick={() => send(text.trim())} disabled={busy || !text.trim()}>
          답 보내기
        </button>
      </p>
      <p>30분이 지나면 대화가 만료되어 처음부터 다시 요청해야 합니다.</p>
      {busy && <p>보내는 중…</p>}
      {message && (
        <p>
          {message} <Link href="/compile">처음부터 다시 요청</Link>
        </p>
      )}
    </>
  );
}
