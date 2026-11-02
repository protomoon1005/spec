"use client";

// 전략 요청 (전략서 v0.2).
//
// 문장을 보내면 서버가 작업 번호만 바로 주고, 명세화는 뒤에서 돈다(POST /v2/strategies/compile).
// 결과는 진행 상황 화면이 스트림으로 받는다.
//
// 무엇을 쓰면 되는지 화면에서 알 수 있게 두 가지를 둔다.
//   1) 예시 문장 — 누르면 입력칸에 채워진다(lib/compile-hints.ts).
//   2) 알아듣는 말 — 서버의 표현 표(db/seeds/expressions.yaml)에서 데모 범위만 골랐다.
//      표현 표가 바뀌면 이 목록도 손으로 맞춘다.

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, loadCompileRequest, saveCompileRequest } from "@/lib/api";
import { COMPILE_EXAMPLES } from "@/lib/compile-hints";

const VOCAB: { name: string; words: string[] }[] = [
  { name: "종목", words: ["종목 이름 그대로 (예: 삼성전자, KODEX 200, 하이닉스)"] },
  { name: "지표", words: ["RSI", "5 · 10 · 20 · 60 · 120일 이평(5일선)", "N일 수익률", "거래량이 20일 평균의 N배", "종가"] },
  { name: "비교", words: ["밑이면 · 이하면 · 위면 · 이상이면 (~인 동안)", "떨어지면 · 깨지면 · 넘으면 · 뚫으면 (~하는 순간)"] },
  { name: "팔기", words: ["산 가격보다 N% 오르면 익절", "N% 빠지면 손절", "N거래일 지나면"] },
  { name: "비중 · 주기", words: ["반반 · 똑같이 · 40%", "매주 · 매달 초 · 매달 중순 · 분기마다"] },
  { name: "다루지 않는 것", words: ["MACD 같은 다른 지표", "공매도 · 분할매수", "장중 매매", "해외 직상장 주식 · 코인"] },
];

export default function CompilePage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>전략 요청</h1>
        <p>이 화면에서 하는 일: 원하는 매매 방식을 한국어 문장으로 적어 전략서를 만든다.</p>
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
      const { job_id } = await api<{ job_id: string }>("/v2/strategies/compile", { body: { text: text.trim() } });
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
            placeholder="예: 삼성전자 RSI 30 아래로 떨어지면 사고 5일선 깨지면 팔아줘"
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
          <span className="flow-hint">보통 10~30초 걸려요 · Ctrl+Enter로 보내기</span>
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
        말하지 않은 칸은 기본값으로 채우고 "기본값" 으로 표시해요(기간 2023~2025년, 1,000만 원, 비중 똑같이).
        사고파는 조건에 꼭 필요한 것(종목, 기준 숫자, 이평 기간)을 말하지 않으면 되물어요.
      </p>
    </>
  );
}
