// 채팅형 화면(/chat)의 대화 기록. 현서 님 demo(Streamlit) 화면 방식을 따른다(docs/v0.2-plan.md 10단계).
//
// 대화는 브라우저 저장소(localStorage)에 사용자별로 둔다. 서버에는 전략서와 백테스트 결과만 남는다.
// 저장소가 막힌 브라우저에서는 기록만 남지 않고 화면은 그대로 돈다.

import type { BacktestV2, Outcome } from "@/lib/v2";

export type Ask = Extract<Outcome, { status: "ask" }>;
export type Ok = Extract<Outcome, { status: "ok" }>;
export type Reject = Extract<Outcome, { status: "reject" }>;

export type Message =
  | { role: "user"; text: string }
  // reply: 질문 id 별로 고른 답의 글자. 답한 뒤에는 카드가 잠긴다
  | { role: "ai"; kind: "ask"; outcome: Ask; reply?: Record<string, string> }
  | { role: "ai"; kind: "reject"; outcome: Reject }
  // used: 이 카드로 백테스트를 돌렸거나 고쳐 말하기를 눌렀다
  | { role: "ai"; kind: "ok"; outcome: Ok; request: string; used?: boolean }
  | { role: "ai"; kind: "result"; run: BacktestV2; request: string; cardIndex: number }
  | { role: "ai"; kind: "error"; text: string };

export type Conversation = { id: string; title: string; createdAt: string; messages: Message[] };

const KEY = (username: string) => `spec.chat.${username}`;
const LIMIT = 30;

export function loadConversations(username: string): Conversation[] {
  try {
    const raw = window.localStorage.getItem(KEY(username));
    return raw ? (JSON.parse(raw) as Conversation[]) : [];
  } catch {
    return [];
  }
}

// 최근 것이 앞. 메시지가 없는 대화는 남기지 않는다.
export function saveConversation(username: string, conv: Conversation): Conversation[] {
  const rest = loadConversations(username).filter((c) => c.id !== conv.id);
  const list = conv.messages.length ? [conv, ...rest].slice(0, LIMIT) : rest;
  try {
    window.localStorage.setItem(KEY(username), JSON.stringify(list));
  } catch {
    // 저장소가 막혔거나 꽉 찼다. 지금 대화는 화면에 그대로 있다.
  }
  return list;
}

export function newConversation(): Conversation {
  return { id: Math.random().toString(36).slice(2, 10), title: "새 전략", createdAt: new Date().toISOString(), messages: [] };
}

export function stamp(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// 빈 화면의 예시. 평가 데이터셋(eval/dataset.jsonl) 문장은 쓰지 않는다. 판정은 2026-11-02 에 확인했다.
export const EXAMPLES = [
  { kind: "진행 · 조건형", text: "삼성전자 RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘" },
  { kind: "진행 · 자산 배분", text: "삼성전자 50% KODEX 200 30% KODEX 국고채3년 20%, 매달 첫 거래일에 리밸런싱" },
  { kind: "되묻기", text: "삼성전자 RSI 30 아래면 사줘" },
  { kind: "거절", text: "내일 오를 종목 사줘" },
];

// 거절 사유 코드의 쉬운 설명(demo/ui/state.py PLAIN 을 옮겼다)
export const REJECT_PLAIN: Record<string, string> = {
  FUTURE_REF: "그날까지 알 수 있는 정보로만 판단할 수 있어요. 앞으로 오를지 내릴지는 미리 알 수 없어서 규칙으로 만들 수 없습니다.",
  UNKNOWN_INDICATOR: "사전에 없는 지표나 숫자로 정할 수 없는 말이 들어 있어요. RSI · 이동평균 같은 사전 속 지표로 말해 주세요.",
  UNSUPPORTED_ASSET: "한국에 상장된 주식과 ETF 만 다뤄요.",
  UNSUPPORTED_ACTION: "사기 · 팔기만 다룹니다. 공매도 · 신용 · 분할매수 같은 매매 방법은 다루지 않아요.",
  INTRADAY: "하루에 한 번, 그날 종가로 판단하고 다음 날 시가에 사고팝니다. 장중 매매는 다루지 않아요.",
  NO_DATA: "이 기간의 가격 데이터가 없어 시험할 수 없어요.",
  OUT_OF_DOMAIN: "투자 전략 이야기가 아니라서 도와드릴 수 없어요.",
  UNRESOLVED: "되물어도 뜻을 정하지 못했어요. 문장을 조금 더 구체적으로 써 주세요.",
  VALIDATION: "전략서 검사에서 말이 되지 않는 부분이 있어요.",
};

export const fmtPct = (v: number | null | undefined, signed = true) =>
  v == null ? "—" : `${signed && v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
export const fmtWon = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v).toLocaleString("ko-KR")}원`);
