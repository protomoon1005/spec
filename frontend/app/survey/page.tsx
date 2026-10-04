"use client";

// 투자 성향 설문 — docs/frontend_milestone.md 3단계.
//
// 문항은 서버에서 받는다(문항·배점은 백엔드 상수가 정본). 7문항을 다 골라야
// 제출되고, 제출은 "답 보내기 → 성향 확정" 두 요청을 차례로 부른다.

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api } from "@/lib/api";

type SurveyForm = {
  survey_version: string;
  questions: { question_code: string; text: string; choices: { answer_code: string; text: string }[] }[];
};

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
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<SurveyForm>("/profile/survey/questions")
      .then(setForm)
      .catch((err) => setMessage(`문항을 받지 못했습니다: ${err instanceof Error ? err.message : String(err)}`));
  }, []);

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

  const answered = form.questions.filter((q) => answers[q.question_code]).length;

  return (
    <>
      <p className="flow-hint" style={{ marginBottom: 14 }}>
        설문 버전 <span className="mono">{form.survey_version}</span>
      </p>
      <ol>
        {form.questions.map((q) => (
          <li key={q.question_code}>
            <p>{q.text}</p>
            {q.choices.map((c) => (
              <div key={c.answer_code}>
                <label>
                  <input
                    type="radio"
                    name={q.question_code}
                    value={c.answer_code}
                    checked={answers[q.question_code] === c.answer_code}
                    onChange={() => setAnswers({ ...answers, [q.question_code]: c.answer_code })}
                    disabled={busy}
                  />{" "}
                  <span className="mono" style={{ color: "var(--c-muted)", marginRight: 8 }}>
                    {c.answer_code}
                  </span>
                  {c.text}
                </label>
              </div>
            ))}
          </li>
        ))}
      </ol>
      <div className="flow-next" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <span className="flow-hint">
          <b className="mono" style={{ color: "var(--c-bright)" }}>
            {answered}/{form.questions.length}
          </b>{" "}
          문항 답함. 전부 답해야 제출할 수 있습니다.
        </span>
        <button className="primary" onClick={submit} disabled={busy || answered < form.questions.length}>
          제출
        </button>
      </div>
      {busy && <p className="flow-state">제출 중…</p>}
      {message && (
        <p className="flow-msg err" role="alert">
          {message}
        </p>
      )}
    </>
  );
}
