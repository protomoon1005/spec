"use client";

// 되묻기 — docs/frontend_milestone.md 4단계.
//
// 서버가 준 질문과 선택지를 그대로 보여 준다. 선택지를 누르거나 직접 적어 답하면
// session_id 와 함께 다시 요청하고, 진행 상황 화면으로 돌아간다.
// 직접 입력칸은 선택지가 있어도 둔다 — "담을 종목이 없다" 질문의 선택지는 답이
// 아니라 거절 사유 목록이라, 사용자가 다른 조건을 적어야 한다.
//
// 되묻는 경우는 세 가지다(backend/app/m1/questions.py).
//   방향이 없음          선택지 = 고를 방향 넷          → 누르면 바로 답
//   종목 이름이 모호함    선택지 = "114260 KODEX …"     → 누르면 바로 답. 이름과 코드를 나눠 쓴다
//   담을 종목이 없음      선택지 = 거절 사유 목록        → 답이 아니다. 누를 수 없게 보여 주고
//                                                        직접 적는 칸과 예시 문장을 앞에 둔다
// 서버는 셋 중 무엇인지(kind)를 보내 주지 않는다. 그래서 세 번째는 질문 문구의 첫머리로
// 가른다(NOTHING_HOLDABLE_HEAD). questions.py 의 for_empty_candidates 문구가 바뀌면 여기도
// 맞춰야 한다 — 어긋나면 거절 사유가 다시 누를 수 있는 버튼으로 나온다. 튼튼하게 하려면
// workers/tasks.py 가 kind 를 함께 보내면 된다.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, loadCompileRequest, loadCompileResult } from "@/lib/api";
import { COMPILE_EXAMPLES } from "@/lib/compile-hints";

type Pending = { session_id: string; question: string; choices: string[] };

const NOTHING_HOLDABLE_HEAD = "요청하신 조건으로 담을 수 있는 종목이 없습니다";

// "114260 KODEX 국고채3년" → 코드와 이름. 종목 후보가 아닌 선택지는 null.
const TICKER_CHOICE = /^([0-9A-Z]{6}) (.+)$/;

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
  const [request, setRequest] = useState<string | null>(null);

  useEffect(() => {
    const r = loadCompileResult();
    setPending(r?.status === "need_answer" ? r : null);
    setRequest(loadCompileRequest());
  }, []);

  async function send(answer: string) {
    if (!pending || busy || !answer) return;
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

  if (pending === undefined) return <p className="flow-state">불러오는 중…</p>;
  if (pending === null) {
    return (
      <div className="flow-empty">
        <strong>답할 질문이 없습니다.</strong>
        <Link href="/compile">전략 요청으로</Link>
      </div>
    );
  }

  const nothingHoldable = pending.question.startsWith(NOTHING_HOLDABLE_HEAD);

  return (
    <>
      {request && (
        <p className="flow-msg" style={{ marginTop: 0, marginBottom: 16 }}>
          <span className="flow-hint" style={{ marginRight: 8 }}>
            내 요청
          </span>
          &ldquo;{request}&rdquo;
        </p>
      )}
      <section className="flow-card">
        <p style={{ fontSize: 16, color: "var(--c-bright)", fontWeight: 500, borderLeft: "3px solid var(--c-warn)", paddingLeft: 14 }}>
          {pending.question}
        </p>

        {nothingHoldable
          ? pending.choices.length > 0 && (
              <>
                <p className="flow-hint" style={{ marginTop: 18, marginBottom: 6, color: "var(--c-warn)" }}>
                  담을 수 없는 이유
                </p>
                <ul>
                  {pending.choices.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              </>
            )
          : pending.choices.length > 0 && (
              <>
                <p className="flow-hint" style={{ marginTop: 18, marginBottom: 8 }}>
                  누르면 그대로 답으로 보냅니다
                </p>
                <div className="flow-choices">
                  {pending.choices.map((c) => {
                    const m = TICKER_CHOICE.exec(c);
                    return (
                      <button key={c} className="flow-choice flow-choice-go" onClick={() => send(c)} disabled={busy}>
                        {m ? (
                          <span>
                            {m[2]} <span className="mono flow-hint">{m[1]}</span>
                          </span>
                        ) : (
                          <span>{c}</span>
                        )}
                        <span aria-hidden="true">→</span>
                      </button>
                    );
                  })}
                </div>
              </>
            )}

        <div className="flow-field" style={{ marginTop: 18 }}>
          <label>
            {nothingHoldable ? "다른 조건을 적어 주세요" : pending.choices.length > 0 ? "또는 직접 적기" : "답 적기"}
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) send(text.trim());
              }}
              size={50}
              disabled={busy}
            />
          </label>
          <button className="primary" onClick={() => send(text.trim())} disabled={busy || !text.trim()}>
            {busy ? "보내는 중…" : "답 보내기"}
          </button>
        </div>
        {nothingHoldable && (
          <div className="flow-examples">
            <span className="flow-hint">예시</span>
            {COMPILE_EXAMPLES.map((ex) => (
              <button key={ex} className="flow-chip" onClick={() => setText(ex)} disabled={busy}>
                {ex}
              </button>
            ))}
          </div>
        )}
        <p className="flow-hint" style={{ marginTop: 12 }}>
          30분 안에 답하지 않으면 처음부터 다시 요청해야 해요.
        </p>
        {message && (
          <p className="flow-msg err" role="alert">
            {message} <Link href="/compile?retry=1">처음부터 다시 요청 — 처음 쓴 문장으로</Link>
          </p>
        )}
      </section>
    </>
  );
}
