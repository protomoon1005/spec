"use client";

// 채팅형 전략 시험 화면. 현서 님 demo/app.py(Streamlit) 화면 방식을 Next.js 로 옮겼다(docs/v0.2-plan.md 10단계).
//
// 왼쪽: 새 전략, 지난 대화, AI 연결 상태. 가운데: 대화. 아래: 입력창.
// 문장을 보내면 POST /v2/strategies/compile 로 접수하고 작업 스트림으로 결과(ok · ask · reject)를 받는다.
// 되묻기 답은 바로 결과가 온다. "이대로 백테스트" 는 백테스트를 접수하고 끝날 때까지 2초마다 조회한다.
// 대화 기록은 브라우저 저장소에 사용자별로 둔다(lib/chat.ts).

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, clearSession, isExpired, loadSession, streamJob, type Session, type StreamEvent } from "@/lib/api";
import {
  EXAMPLES,
  loadConversations,
  newConversation,
  saveConversation,
  stamp,
  type Conversation,
  type Message,
} from "@/lib/chat";
import type { BacktestV2, Outcome } from "@/lib/v2";

import "./chat.css";
import { AskCard, RejectCard, ResultCard, UnderstoodCard, UserBubble } from "./parts";

const POLL_MS = 2000;

export default function ChatPage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);

  useEffect(() => {
    const s = loadSession();
    if (!s || isExpired(s.access)) router.replace("/login");
    else setSession(s);
  }, [router]);

  if (!session) return <div className="chat-root" />;
  return <Chat session={session} />;
}

function Chat({ session }: { session: Session }) {
  const router = useRouter();
  const [history, setHistory] = useState<Conversation[]>([]);
  const [conv, setConv] = useState<Conversation>(newConversation);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [llm, setLlm] = useState<"ok" | "down" | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => setHistory(loadConversations(session.username)), [session.username]);

  useEffect(() => {
    api<{ llm_backend?: string }>("/health")
      .then((h) => setLlm(h.llm_backend === "ok" ? "ok" : "down"))
      .catch(() => setLlm("down"));
  }, []);

  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    setElapsed(0);
    const t = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(t);
  }, [busy]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [conv.messages.length, busy]);

  // 대화를 바꾸고 저장한다. 함수형으로 받아 비동기 응답이 늦게 와도 최신 대화에 붙는다.
  const update = useCallback(
    (fn: (c: Conversation) => Conversation) => {
      setConv((prev) => {
        const next = fn(prev);
        setHistory(saveConversation(session.username, next));
        return next;
      });
    },
    [session.username],
  );

  const push = useCallback((m: Message) => update((c) => ({ ...c, messages: [...c.messages, m] })), [update]);

  const lastRequest = (c: Conversation) =>
    [...c.messages].reverse().find((m): m is Extract<Message, { role: "user" }> => m.role === "user")?.text ?? "";

  function outcomeMessage(o: Outcome, request: string): Message {
    if (o.status === "ok") return { role: "ai", kind: "ok", outcome: o, request };
    if (o.status === "ask") return { role: "ai", kind: "ask", outcome: o };
    return { role: "ai", kind: "reject", outcome: o };
  }

  async function submit(text: string) {
    const t = text.trim();
    if (!t || busy) return;
    setInput("");
    update((c) => ({
      ...c,
      title: c.messages.length ? c.title : t.length > 24 ? `${t.slice(0, 24)}…` : t,
      messages: [...c.messages, { role: "user", text: t }],
    }));
    setBusy("전략서를 만들고 있어요");
    try {
      const { job_id } = await api<{ job_id: string }>("/v2/strategies/compile", { body: { text: t } });
      const result = await waitJob(job_id);
      push(outcomeMessage(result, t));
    } catch (err) {
      push({ role: "ai", kind: "error", text: `처리 중 오류: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setBusy(null);
    }
  }

  async function answer(
    index: number,
    answers: { question_id: string; choice_id?: string; text?: string }[],
    labels: Record<string, string>,
  ) {
    const m = conv.messages[index];
    if (m.role !== "ai" || m.kind !== "ask" || busy) return;
    const request = lastRequest(conv);
    update((c) => ({
      ...c,
      messages: c.messages.map((x, i) => (i === index && x.role === "ai" && x.kind === "ask" ? { ...x, reply: labels } : x)),
    }));
    setBusy("답을 반영하고 있어요");
    try {
      const next = await api<Outcome>(`/v2/strategies/compile/${encodeURIComponent(m.outcome.session_id)}/answer`, {
        body: { answers },
      });
      push(outcomeMessage(next, request));
    } catch (err) {
      push({ role: "ai", kind: "error", text: `답을 보내지 못했어요: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setBusy(null);
    }
  }

  async function backtest(index: number) {
    const m = conv.messages[index];
    if (m.role !== "ai" || m.kind !== "ok" || busy) return;
    update((c) => ({
      ...c,
      messages: c.messages.map((x, i) => (i === index && x.role === "ai" && x.kind === "ok" ? { ...x, used: true } : x)),
    }));
    setBusy("백테스트를 돌리고 있어요");
    try {
      const { run_id } = await api<{ run_id: number }>(
        `/v2/strategies/${encodeURIComponent(m.outcome.strategy_id)}/backtest`,
        { method: "POST" },
      );
      let run: BacktestV2;
      for (;;) {
        run = await api<BacktestV2>(`/v2/backtests/${run_id}`);
        if (run.status === "done" || run.status === "failed") break;
        await new Promise((r) => setTimeout(r, POLL_MS));
      }
      if (run.status === "failed") {
        push({ role: "ai", kind: "error", text: `백테스트를 하지 못했어요: ${run.reason ?? "이유를 받지 못했어요"}` });
      } else {
        push({ role: "ai", kind: "result", run, request: m.request, cardIndex: index });
      }
    } catch (err) {
      push({ role: "ai", kind: "error", text: `백테스트 오류: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setBusy(null);
    }
  }

  function refill(text: string) {
    setInput(text);
    setTimeout(() => inputRef.current?.focus(), 0);
  }

  function revise(index: number) {
    const m = conv.messages[index];
    if (m.role !== "ai" || m.kind !== "ok") return;
    update((c) => ({
      ...c,
      messages: c.messages.map((x, i) => (i === index && x.role === "ai" && x.kind === "ok" ? { ...x, used: true } : x)),
    }));
    refill(m.request);
  }

  function startNew() {
    if (busy) return;
    setConv(newConversation());
    setInput("");
  }

  function open(id: string) {
    if (busy) return;
    const c = history.find((h) => h.id === id);
    if (c) setConv(c);
  }

  function logout() {
    clearSession();
    router.replace("/login");
  }

  const lastAsk = conv.messages.reduce((acc, m, i) => (m.role === "ai" && m.kind === "ask" ? i : acc), -1);

  return (
    <div className="chat-root">
      <aside className="sidebar">
        <button className="btn primary wide" onClick={startNew} disabled={!!busy}>
          ＋ 새 전략
        </button>
        <p className="caption">지난 대화</p>
        <div className="history">
          {history.length === 0 && <span className="muted">아직 없습니다.</span>}
          {history.map((h) => (
            <button key={h.id} className={`history-item${h.id === conv.id ? " on" : ""}`} onClick={() => open(h.id)} disabled={!!busy}>
              <span>{h.title}</span>
              <small>{stamp(h.createdAt)}</small>
            </button>
          ))}
        </div>
        <div className="sidebar-foot">
          <div>
            <span className={`dot ${llm ?? ""}`} /> AI {llm === "ok" ? "연결됨" : llm === "down" ? "꺼짐" : "확인 중"} ·{" "}
            <code>qwen3:8b</code>
          </div>
          <div className="muted" style={{ marginTop: 8 }}>
            {session.username} · <Link href="/specs">전략 관리</Link> ·{" "}
            <button className="link" onClick={logout}>
              로그아웃
            </button>
          </div>
        </div>
      </aside>

      <main className="main">
        <div className="thread">
          {conv.messages.length === 0 && !busy ? (
            <>
              <div className="hero">어떤 전략을 시험해 볼까요?</div>
              <div className="examples">
                {EXAMPLES.map((ex) => (
                  <button key={ex.text} className="example" onClick={() => submit(ex.text)}>
                    <small>{ex.kind}</small>
                    <span>{ex.text}</span>
                  </button>
                ))}
              </div>
            </>
          ) : (
            conv.messages.map((m, i) => (
              <div key={i} className="msg">
                {m.role === "user" ? (
                  <UserBubble text={m.text} />
                ) : m.kind === "ask" ? (
                  <AskCard
                    outcome={m.outcome}
                    reply={m.reply}
                    live={i === lastAsk}
                    busy={!!busy}
                    onAnswer={(a, l) => answer(i, a, l)}
                  />
                ) : m.kind === "reject" ? (
                  <RejectCard outcome={m.outcome} request={requestBefore(conv.messages, i)} />
                ) : m.kind === "ok" ? (
                  <UnderstoodCard
                    outcome={m.outcome}
                    used={m.used}
                    busy={!!busy}
                    anchorId={`card-${conv.id}-${i}`}
                    onBacktest={() => backtest(i)}
                    onRevise={() => revise(i)}
                  />
                ) : m.kind === "result" ? (
                  <ResultCard
                    run={m.run}
                    onRetry={() => refill(m.request)}
                    onToCard={() =>
                      document.getElementById(`card-${conv.id}-${m.cardIndex}`)?.scrollIntoView({ behavior: "smooth", block: "start" })
                    }
                    onHome={startNew}
                  />
                ) : (
                  <div className="error-box">{m.text}</div>
                )}
              </div>
            ))
          )}
          {busy && (
            <div className="status">
              <div className="status-head">
                <span className="spinner" aria-hidden="true" /> {busy}… <span className="muted">{elapsed}초</span>
              </div>
              <div className="muted">보통 10~30초 걸려요.</div>
            </div>
          )}
          <div ref={bottom} />
        </div>

        <div className="composer">
          <textarea
            ref={inputRef}
            rows={1}
            value={input}
            placeholder="전략을 한국어로 말해 주세요"
            disabled={!!busy}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit(input);
              }
            }}
          />
          <button className="send" disabled={!!busy || !input.trim()} onClick={() => submit(input)} aria-label="보내기">
            ↑
          </button>
        </div>
      </main>
    </div>
  );
}

// 거절 카드가 근거를 칠할 원문: 그 카드 바로 앞의 사용자 문장
function requestBefore(messages: Message[], index: number): string {
  for (let i = index - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "user") return m.text;
  }
  return "";
}

// 작업 스트림이 끝날 때까지 기다려 결과를 돌려준다.
function waitJob(jobId: string): Promise<Outcome> {
  return new Promise((resolve, reject) => {
    const abort = new AbortController();
    let settled = false;
    const onEvent = ({ event, data }: StreamEvent) => {
      const d = data as { state?: string; result?: Outcome; error?: string };
      if (event === "complete") {
        settled = true;
        abort.abort();
        if (d.state === "SUCCESS" && d.result) resolve(d.result);
        else reject(new Error(d.error ?? "작업이 실패했어요"));
      } else if (event === "timeout") {
        settled = true;
        abort.abort();
        reject(new Error("시간 초과로 연결이 끊겼어요"));
      }
    };
    streamJob(jobId, onEvent, abort.signal)
      .then(() => {
        if (!settled) reject(new Error("결과를 받기 전에 연결이 끊겼어요"));
      })
      .catch((err) => {
        if (!settled) reject(err);
      });
  });
}
