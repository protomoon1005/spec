"use client";

// 투자 성향 설문 — docs/frontend_milestone.md 3단계.
//
// 문항은 서버에서 받는다(문항·배점은 백엔드 상수가 정본). 제출은 "답 보내기 → 성향 확정"
// 두 요청을 차례로 부른다.
//
// 한 화면에 한 문항만 둔다. 선택지를 누르면 고른 줄을 잠깐 보여 준 뒤 다음 문항으로
// 넘어가고, 마지막 문항 뒤에는 답을 모아 보는 확인 단계가 있다 — 성향이 이 답으로
// 확정되므로 한 번 훑어보고 제출하게 한다. 확인 단계에서 문항을 눌러 고칠 수 있다.
//
// 선택지는 라디오가 아니라 버튼이다. 라디오는 방향키로 옮겨도 change 가 일어나서,
// "고르면 넘어간다" 를 붙이면 키보드로 훑기만 해도 문항이 넘어간다.
// 답 코드(A1 …)는 서버로 보내는 값이라 화면에는 쓰지 않는다.

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api } from "@/lib/api";

type SurveyForm = {
  survey_version: string;
  questions: { question_code: string; text: string; choices: { answer_code: string; text: string }[] }[];
};

// 고른 줄이 강조되는 것을 볼 수 있을 만큼만 기다렸다 넘긴다.
const ADVANCE_MS = 260;

export default function SurveyPage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>투자 성향 설문</h1>
        <p>이 화면에서 하는 일: 7문항에 답해 투자 성향(1~5)을 확정한다.</p>
        <RequireLogin>{() => <Survey />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Survey() {
  const router = useRouter();
  const [form, setForm] = useState<SurveyForm | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  // 0 … 문항 수-1 은 문항, 문항 수는 확인 단계.
  const [step, setStep] = useState(0);
  // 확인 단계를 한 번 본 뒤로는 문항을 고치면 곧장 확인 단계로 돌아간다.
  const [reviewed, setReviewed] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const advance = useRef<number | undefined>(undefined);

  useEffect(() => {
    api<SurveyForm>("/profile/survey/questions")
      .then(setForm)
      .catch((err) => setMessage(`문항을 받지 못했습니다: ${err instanceof Error ? err.message : String(err)}`));
    return () => window.clearTimeout(advance.current);
  }, []);

  useEffect(() => {
    if (form && step >= form.questions.length) setReviewed(true);
  }, [form, step]);

  function goTo(next: number) {
    window.clearTimeout(advance.current);
    setStep(next);
  }

  // 확인 단계에서 고치러 온 문항이면 다시 확인 단계로, 아니면 다음 문항으로.
  function pick(questionCode: string, answerCode: string) {
    if (!form) return;
    const filled = { ...answers, [questionCode]: answerCode };
    setAnswers(filled);
    const allDone = form.questions.every((q) => filled[q.question_code]);
    const next = allDone && reviewed ? form.questions.length : step + 1;
    window.clearTimeout(advance.current);
    advance.current = window.setTimeout(() => setStep(next), ADVANCE_MS);
  }

  async function submit() {
    if (!form) return;
    setBusy(true);
    setMessage(null);
    try {
      await api("/profile/survey", {
        body: form.questions.map((q) => ({ question_code: q.question_code, answer_code: answers[q.question_code] })),
      });
      await api("/profile", { method: "POST" });
      router.push("/profile");
    } catch (err) {
      setMessage(`제출 실패: ${err instanceof Error ? err.message : String(err)}`);
      setBusy(false);
    }
  }

  if (!form) {
    return message ? (
      <p className="flow-msg err" role="alert">
        {message}
      </p>
    ) : (
      <p className="flow-state">문항을 불러오는 중…</p>
    );
  }

  const total = form.questions.length;
  const answered = form.questions.filter((q) => answers[q.question_code]).length;
  const onReview = step >= total;
  const current = onReview ? null : form.questions[step];

  return (
    <>
      <div className="flow-progress" aria-hidden="true">
        <div style={{ width: `${(answered / total) * 100}%` }} />
      </div>
      <p className="flow-step-count">{onReview ? "답변 확인" : `질문 ${step + 1} / ${total}`}</p>

      {current ? (
        <section key={step} className="flow-card flow-step" role="group" aria-label={current.text}>
          <p className="flow-question-text">{current.text}</p>
          <div className="flow-choices">
            {current.choices.map((c) => (
              <button
                key={c.answer_code}
                className="flow-choice"
                aria-pressed={answers[current.question_code] === c.answer_code}
                onClick={() => pick(current.question_code, c.answer_code)}
              >
                {c.text}
              </button>
            ))}
          </div>
        </section>
      ) : (
        <section key="review" className="flow-step">
          <p className="flow-hint" style={{ marginBottom: 10 }}>
            성향은 이 답으로 확정됩니다. 고칠 문항이 있으면 눌러서 다시 고르세요.
          </p>
          <div className="flow-review">
            {form.questions.map((q, i) => {
              const choice = q.choices.find((c) => c.answer_code === answers[q.question_code]);
              return (
                <button key={q.question_code} onClick={() => goTo(i)} disabled={busy}>
                  <span className="flow-review-q">
                    {i + 1}. {q.text}
                  </span>
                  <span className="flow-review-a">{choice?.text ?? "답하지 않음"}</span>
                </button>
              );
            })}
          </div>
        </section>
      )}

      <div className="flow-next" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <button className="ghost" onClick={() => goTo((onReview ? total : step) - 1)} disabled={step === 0 || busy}>
          ← 이전
        </button>
        {onReview ? (
          <button className="primary" onClick={submit} disabled={busy || answered < total}>
            {busy ? "제출 중…" : "제출하고 성향 확정"}
          </button>
        ) : (
          current &&
          answers[current.question_code] && (
            <button className="ghost" onClick={() => goTo(reviewed ? total : step + 1)}>
              {reviewed ? "답변 확인으로 →" : "다음 →"}
            </button>
          )
        )}
      </div>

      {message && (
        <p className="flow-msg err" role="alert">
          {message}
        </p>
      )}
      <p className="flow-hint" style={{ marginTop: 24, fontSize: 11 }}>
        설문 버전 <span className="mono">{form.survey_version}</span>
      </p>
    </>
  );
}
