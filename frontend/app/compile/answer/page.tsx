"use client";

// 되묻기 (전략서 v0.2).
//
// 서버가 한 차례에 3개까지, 2차례까지 묻는다(설계 문서 "되묻는 순서").
// 질문마다 선택지 하나를 고르거나, 직접 적는다. 다 답하면 한 번에 보낸다.
// 답은 AI 를 다시 부르지 않아 바로 결과가 온다(POST /v2/strategies/compile/{session_id}/answer).
//   ok      완료 화면으로
//   ask     다음 차례 질문을 이 화면에 다시 그린다
//   reject  여기서 이유를 보여 준다
// 선택지의 실제 값은 서버 대화 상태에만 있다. 화면은 선택지 id 만 보낸다.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { RejectBox } from "@/components/v2/outcome-parts";
import { api, loadCompileRequest } from "@/lib/api";
import { loadOutcome, nextPath, saveOutcome, type Outcome, type Question } from "@/lib/v2";

type Ask = Extract<Outcome, { status: "ask" }>;
type Draft = Record<string, { choice?: string; text?: string }>;

export default function AnswerPage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>되묻기</h1>
        <p>이 화면에서 하는 일: 전략서를 만드는 데 모자란 것을 골라 알려 준다.</p>
        <RequireLogin>{() => <Answer />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Answer() {
  const router = useRouter();
  const [outcome, setOutcome] = useState<Outcome | null | undefined>(undefined);
  const [draft, setDraft] = useState<Draft>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [request, setRequest] = useState<string | null>(null);

  useEffect(() => {
    setOutcome(loadOutcome());
    setRequest(loadCompileRequest());
  }, []);

  if (outcome === undefined) return <p className="flow-state">불러오는 중…</p>;
  if (outcome?.status === "reject") return <RejectBox outcome={outcome} />;
  if (!outcome || outcome.status !== "ask") {
    return (
      <div className="flow-empty">
        <strong>답할 질문이 없습니다.</strong>
        <Link href="/compile">전략 요청으로</Link>
      </div>
    );
  }
  const ask: Ask = outcome;
  const ready = ask.questions.every((q) => draft[q.id]?.choice || draft[q.id]?.text?.trim());

  async function send() {
    if (busy || !ready) return;
    setBusy(true);
    setMessage(null);
    const answers = ask.questions.map((q) =>
      draft[q.id]?.choice
        ? { question_id: q.id, choice_id: draft[q.id].choice }
        : { question_id: q.id, text: draft[q.id]?.text?.trim() },
    );
    try {
      const next = await api<Outcome>(`/v2/strategies/compile/${encodeURIComponent(ask.session_id)}/answer`, {
        body: { answers },
      });
      saveOutcome(next);
      const path = nextPath(next);
      if (path && next.status === "ok") {
        router.push(path);
        return;
      }
      setDraft({});
      setOutcome(next);
    } catch (err) {
      setMessage(`답을 보내지 못했어요: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {request && (
        <p className="flow-msg" style={{ marginTop: 0, marginBottom: 16 }}>
          <span className="flow-hint" style={{ marginRight: 8 }}>
            요청
          </span>
          &ldquo;{request}&rdquo;
        </p>
      )}
      <p className="flow-hint" style={{ marginBottom: 12 }}>
        {ask.round}번째 질문이에요(최대 2번). 정할 것이 모두 {ask.remaining_total}개 남았어요.
      </p>

      {ask.questions.map((q, i) => (
        <QuestionCard
          key={`${ask.round}-${q.id}`}
          index={i + 1}
          question={q}
          value={draft[q.id] ?? {}}
          disabled={busy}
          onChange={(v) => setDraft((prev) => ({ ...prev, [q.id]: v }))}
        />
      ))}

      <div className="flow-next" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <span className="flow-hint">30분 안에 답하지 않으면 처음부터 다시 요청해야 해요.</span>
        <button className="primary" onClick={send} disabled={busy || !ready}>
          {busy ? "보내는 중…" : "답 보내기"}
        </button>
      </div>
      {message && (
        <p className="flow-msg err" role="alert">
          {message} <Link href="/compile?retry=1">처음부터 다시 요청 — 처음 쓴 문장으로</Link>
        </p>
      )}
    </>
  );
}

function QuestionCard({
  index,
  question,
  value,
  disabled,
  onChange,
}: {
  index: number;
  question: Question;
  value: { choice?: string; text?: string };
  disabled: boolean;
  onChange: (v: { choice?: string; text?: string }) => void;
}) {
  return (
    <section className="flow-card" style={{ marginBottom: 12 }}>
      <p
        style={{
          fontSize: 16,
          color: "var(--c-bright)",
          fontWeight: 500,
          borderLeft: "3px solid var(--c-warn)",
          paddingLeft: 14,
          margin: 0,
        }}
      >
        <span className="flow-hint" style={{ marginRight: 8 }}>
          {index}
        </span>
        {question.text}
      </p>
      {question.choices.length > 0 && (
        <div className="flow-choices" style={{ marginTop: 14 }}>
          {question.choices.map((c) => (
            <button
              key={c.id}
              className="flow-choice"
              aria-pressed={value.choice === c.id}
              onClick={() => onChange({ choice: value.choice === c.id ? undefined : c.id })}
              disabled={disabled}
            >
              {c.label}
            </button>
          ))}
        </div>
      )}
      {question.allow_text && (
        <div className="flow-field" style={{ marginTop: 12 }}>
          <label>
            {question.choices.length > 0 ? "또는 직접 적기" : "답 적기"}
            <input
              value={value.text ?? ""}
              onChange={(e) => onChange({ text: e.target.value })}
              disabled={disabled}
              placeholder={placeholder(question)}
              style={{ width: "100%" }}
            />
          </label>
        </div>
      )}
    </section>
  );
}

function placeholder(q: Question): string {
  if (q.kind === "universe" || q.kind === "ambiguous_quote") return "예: 삼성전자";
  if (q.kind === "ma_window" || q.kind === "window_not_allowed") return "예: 20일";
  if (q.kind === "threshold") return "예: 30";
  return "";
}
