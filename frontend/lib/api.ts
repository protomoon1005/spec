// 흐름 화면(가입 → 로그인 → 설문 → 전략 만들기 …)이 공통으로 쓰는 바닥.
// docs/frontend_milestone.md 0단계.
//
// 네 가지만 한다.
//   1) 토큰 보관       브라우저 저장소에 access · refresh · username
//   2) API 호출        주소 · 토큰 헤더 · 오류 메시지 꺼내기
//   3) 진행 상황 스트림 전략서 컴파일 결과를 받는 SSE 읽기
//   4) 흐름 상태       전략 만들기 결과를 다음 화면으로 넘기기
//
// 서버 오류는 가공하지 않고 "HTTP 상태: 서버가 준 detail" 로 그대로 올린다.
// 의도 파악이 우선인 화면이라, 무엇이 왜 실패했는지 그대로 보여야 한다.

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// --- 1) 토큰 보관 ----------------------------------------------------------

export type Session = { access: string; refresh: string; username: string };

const SESSION_KEY = "spec.session";

export function loadSession(): Session | null {
  try {
    const raw = window.localStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    // 서버 렌더링 중이거나 저장소가 막힌 브라우저 — 로그인 안 된 것으로 본다.
    return null;
  }
}

export function saveSession(session: Session): void {
  window.localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  rememberToken(session.username, session.access);
}

// 로그아웃은 로그인 상태(SESSION_KEY)만 지운다. 인증받은 토큰은 아이디별로 따로
// 기억해 두어, 유효기간 안이면 로그인 화면에서 토큰 없이 다시 들어올 수 있게 한다.
const TOKENS_KEY = "spec.tokens";

function loadTokens(): Record<string, string> {
  try {
    const raw = window.localStorage.getItem(TOKENS_KEY);
    return raw ? (JSON.parse(raw) as Record<string, string>) : {};
  } catch {
    return {};
  }
}

function rememberToken(username: string, access: string): void {
  window.localStorage.setItem(TOKENS_KEY, JSON.stringify({ ...loadTokens(), [username]: access }));
}

export function loadRememberedToken(username: string): string | null {
  return loadTokens()[username] ?? null;
}

// JWT 가운데 조각의 exp(초)를 읽어 밀리초로 낸다. 서명은 서버가 본다 — 여기선 만료
// 안내용일 뿐이다. 모양이 JWT 가 아니면 null(만료 판단은 서버에 맡긴다).
// 로그인 화면에만 있던 것을 RequireLogin 도 쓰도록 여기로 옮겼다.
export function tokenExpiry(token: string): number | null {
  try {
    const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const { exp } = JSON.parse(atob(part.padEnd(Math.ceil(part.length / 4) * 4, "="))) as { exp?: number };
    return typeof exp === "number" ? exp * 1000 : null;
  } catch {
    return null;
  }
}

export function isExpired(token: string): boolean {
  const exp = tokenExpiry(token);
  return exp !== null && exp <= Date.now();
}

export function clearSession(): void {
  try {
    window.localStorage.removeItem(SESSION_KEY);
  } catch {
    // 지울 게 없으면 그만이다.
  }
}

// --- 2) API 호출 -----------------------------------------------------------

// 생성자 매개변수 속성(readonly status)을 쓰지 않는다 — node 의 타입 제거 실행
// (scripts/check-api.ts)이 그 문법을 못 읽는다.
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(`HTTP ${status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
    this.status = status;
    this.detail = detail;
  }
}

type ApiOptions = {
  method?: "GET" | "POST" | "DELETE";
  body?: unknown;
  // 생략하면 보관된 토큰을 쓴다. 로그인 화면처럼 입력한 토큰으로 확인할 때만 넘긴다.
  token?: string | null;
};

export async function api<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: options.method ?? (options.body === undefined ? "GET" : "POST"),
    headers: headers(options.token, options.body !== undefined),
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const text = await res.text();
  const data: unknown = text ? safeJson(text) : null;
  if (!res.ok) {
    // FastAPI 오류는 { detail: ... } 모양이다. 아니면 본문을 통째로 보여 준다.
    const detail = data && typeof data === "object" && "detail" in data ? data.detail : data;
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

function headers(token: string | null | undefined, json: boolean): HeadersInit {
  const h: Record<string, string> = {};
  if (json) h["Content-Type"] = "application/json";
  const access = token === undefined ? loadSession()?.access : token;
  if (access) h.Authorization = `Bearer ${access}`;
  return h;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

// --- 3) 진행 상황 스트림 ---------------------------------------------------
//
// 브라우저 기본 EventSource 는 Authorization 헤더를 못 싣는다. 서버의 스트림
// 주소는 인증이 필요하므로 fetch 로 받아 직접 끊어 읽는다.
// 서버는 event: status(상태 바뀔 때마다) → complete(끝) 또는 timeout 을 보낸다.

export type StreamEvent = { event: string; data: unknown };

// signal 로 끊을 수 있다. 개발 모드(reactStrictMode)는 effect 를 두 번 돌리므로
// 화면이 정리될 때 끊지 않으면 스트림이 두 개 열려 이벤트가 두 번 온다.
export async function streamJob(
  jobId: string,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/jobs/${jobId}/stream`, { headers: headers(undefined, false), signal });
  if (!res.ok || !res.body) {
    throw new ApiError(res.status, safeJson(await res.text()));
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSse(buffer);
    buffer = parsed.rest;
    parsed.events.forEach(onEvent);
  }
}

// 이벤트는 빈 줄로 끝난다. 아직 빈 줄이 안 온 끝 조각은 rest 로 남겨 다음 읽기에 붙인다.
export function parseSse(buffer: string): { events: StreamEvent[]; rest: string } {
  const blocks = buffer.replace(/\r\n/g, "\n").split("\n\n");
  const rest = blocks.pop() ?? "";
  const events = blocks
    .filter((block) => block.trim())
    .map((block) => {
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      return { event, data: safeJson(data.join("\n")) };
    });
  return { events, rest };
}

// --- 4) 화면 사이에 넘기는 전략 만들기 결과 --------------------------------
//
// 진행 상황 화면이 받은 결과(되묻기 질문, 완료된 spec_id)를 다음 화면이 읽는다.
// 질문 문장과 선택지는 주소에 싣기엔 길어서 탭 저장소(sessionStorage)에 둔다.
// 탭을 닫으면 사라지는 게 맞다 — 되묻기 대화도 서버에서 30분이면 만료된다.

export type CompileResult =
  | { status: "completed"; spec_id: string; universe_size: number }
  | { status: "need_answer"; session_id: string; question: string; choices: string[] }
  | { status: "failed"; reason: string };

const COMPILE_KEY = "spec.compile";

export function saveCompileResult(result: CompileResult): void {
  window.sessionStorage.setItem(COMPILE_KEY, JSON.stringify(result));
}

export function loadCompileResult(): CompileResult | null {
  try {
    const raw = window.sessionStorage.getItem(COMPILE_KEY);
    return raw ? (JSON.parse(raw) as CompileResult) : null;
  } catch {
    return null;
  }
}

// 전략 요청 화면이 보낸 문장. 진행 상황 화면이 "무엇을 만들고 있는지" 보여 주고,
// 실패해서 다시 요청할 때 입력칸에 되채운다. 같은 탭 안에서만 쓰므로 탭 저장소에 둔다.

const REQUEST_KEY = "spec.compileRequest";

export function saveCompileRequest(text: string): void {
  try {
    window.sessionStorage.setItem(REQUEST_KEY, text);
  } catch {
    // 저장소가 막힌 브라우저 — 화면에 문장이 안 보일 뿐 흐름은 그대로다.
  }
}

export function loadCompileRequest(): string | null {
  try {
    return window.sessionStorage.getItem(REQUEST_KEY);
  } catch {
    return null;
  }
}

// 백테스트 화면이 만든 실행 번호를 최종 확인 화면이 읽는다. 어느 전략서로 돌린 것인지
// 함께 둔다 — 탭에서 새 전략서를 만들면 옛 실행 결과를 그 전략서 것으로 보이면 안 된다.
// 새로고침해도 같은 전략서면 새로 돌리지 않고 이 번호를 다시 조회한다.

export type SavedBacktest = { spec_id: string; run_id: number };

const BACKTEST_KEY = "spec.backtest";

export function saveBacktestRun(saved: SavedBacktest): void {
  window.sessionStorage.setItem(BACKTEST_KEY, JSON.stringify(saved));
}

export function loadBacktestRun(specId: string): SavedBacktest | null {
  try {
    const raw = window.sessionStorage.getItem(BACKTEST_KEY);
    const saved = raw ? (JSON.parse(raw) as SavedBacktest) : null;
    return saved?.spec_id === specId ? saved : null;
  } catch {
    return null;
  }
}

// 하드캡 화면의 검사 결과. 최종 확인 화면이 체크리스트에 쓴다. 검사 API 는 전략서를
// 저장하는 요청이라 확인 화면에서 다시 부르지 않고 이것만 읽는다.

export type SavedValidation = { spec_id: string; passed: boolean; blocked_at: number | null };

const VALIDATION_KEY = "spec.validation";

export function saveValidation(saved: SavedValidation): void {
  try {
    window.sessionStorage.setItem(VALIDATION_KEY, JSON.stringify(saved));
  } catch {
    // 저장소가 막힌 브라우저 — 확인 화면에 "아직 확인 안 함" 으로 나올 뿐이다.
  }
}

export function loadValidation(specId: string): SavedValidation | null {
  try {
    const raw = window.sessionStorage.getItem(VALIDATION_KEY);
    const saved = raw ? (JSON.parse(raw) as SavedValidation) : null;
    return saved?.spec_id === specId ? saved : null;
  } catch {
    return null;
  }
}

// GET /backtest/runs/{run_id} 응답 (backend/app/routers/backtest.py BacktestRunDetail).
export type SeriesSummary = { total: number; cagr: number; mdd: number; sharpe: number; sortino: number };

export type BacktestRun = {
  run_id: number;
  spec_id: string;
  period_start: string | null;
  period_end: string | null;
  status: "queued" | "running" | "done" | "failed" | null;
  data_snapshot_asof: string | null;
  metrics: Record<"cagr" | "mdd" | "sharpe" | "sortino" | "win_rate" | "benchmark_cagr", number | null> | null;
  summary: { strategy: SeriesSummary; control: SeriesSummary; market: SeriesSummary } | null;
  scorer_sources: Record<string, string> | null;
  schedule: { note: string; skipped_rebalance_dates: string[] } | null;
  weight_path: string | null;
  // 비중 범위 · 현금 · 간격을 검증 확정값으로 돌렸는지(passed) 아닌지(failed · none). 옛 실행에는 없다
  validation?: { status: "passed" | "failed" | "none"; cash_target?: number; min_interval_days?: number; blocked_at?: number } | null;
  reason: string | null;
};
