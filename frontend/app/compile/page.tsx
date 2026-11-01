"use client";

// 전략 요청.
//
// 문장을 보내면 서버가 작업 번호만 바로 주고, 실제 컴파일은 뒤에서 돈다.
// 결과는 진행 상황 화면이 스트림으로 받는다.
//
// 무엇을 쓰면 되는지 화면에서 알 수 있게 두 가지를 둔다.
//   1) 예시 문장 — 누르면 입력칸에 채워진다(lib/compile-hints.ts).
//   2) 알아듣는 말 — 서버는 정해진 목록(전략 요소 사전의 kw: 태그 · 업종 · 자산군) 안에서만 뽑는다.
//      서버 목록이 바뀌면 이 목록도 손으로 맞춘다.

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, loadCompileRequest, saveCompileRequest } from "@/lib/api";
import { COMPILE_EXAMPLES } from "@/lib/compile-hints";

const VOCAB: { name: string; words: string[] }[] = [
  { name: "업종", words: ["반도체", "2차전지", "바이오·헬스케어", "금융", "인터넷·플랫폼", "소비재"] },
  { name: "자산", words: ["주식", "채권", "원자재"] },
  { name: "ETF 성격", words: ["배당", "월배당", "국채", "시장대표", "환헤지", "액티브"] },
  { name: "투자 태도", words: ["안전하게", "보수적", "공격적"] },
  { name: "리밸런싱", words: ["매주", "매달 초", "매달 중순", "분기마다", "비중이 5%p 벗어나면"] },
  { name: "종목", words: ["ETF 이름을 그대로 (예: KODEX 200)"] },
];

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

  // 진행 상황 화면에서 실패해 "다시 요청" 으로 돌아온 경우(?retry=1) 방금 쓴 문장을 되채운다.
  // useSearchParams 대신 window.location 을 읽는다: 앞의 것은 Suspense 경계가 필요하다.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("retry") !== "1") return;
    const last = loadCompileRequest();
    if (last) setText(last);
  }, []);

  async function send() {
    if (busy || !text.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      const { job_id } = await api<{ job_id: string }>("/specs/compile", { body: { input_prompt: text.trim() } });
      saveCompileRequest(text.trim());
      router.push(`/compile/progress?job=${encodeURIComponent(job_id)}`);
    } catch (err) {
      setMessage(`요청 실패: ${err instanceof Error ? err.message : String(err)}`);
      setBusy(false);
    }
  }

  return (
    <>
      <section className="flow-card">
        <label>
          원하는 투자 방식
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                send();
              }
            }}
            rows={4}
            cols={60}
            placeholder="예: 안전하게 채권 위주로 굴리고 매주 비중을 맞춰줘"
            disabled={busy}
            style={{ fontSize: 16 }}
          />
        </label>
        <div className="flow-examples">
          <span className="flow-hint">예시로 시작</span>
          {COMPILE_EXAMPLES.map((ex) => (
            <button key={ex} className="flow-chip" onClick={() => setText(ex)} disabled={busy}>
              {ex}
            </button>
          ))}
        </div>
        <div className="flow-next" style={{ justifyContent: "space-between", alignItems: "center", marginTop: 16 }}>
          <span className="flow-hint">보통 2분 넘게 걸려요 · Ctrl+Enter로 보내기</span>
          <button className="primary" onClick={send} disabled={busy || !text.trim()}>
            {busy ? "요청 중…" : "전략서 만들기"}
          </button>
        </div>
        {message && (
          <p className="flow-msg err" role="alert">
            {message}
          </p>
        )}
      </section>

      <h2>이런 말을 알아들어요</h2>
      <dl className="flow-kv">
        {VOCAB.map((v) => (
          <div key={v.name} style={{ display: "contents" }}>
            <dt>{v.name}</dt>
            <dd>{v.words.join(" · ")}</dd>
          </div>
        ))}
      </dl>
      <p className="flow-hint" style={{ marginTop: 10 }}>
        언제 비중을 맞출지 말하지 않으면 매달 초로 만들어요.
      </p>
    </>
  );
}
