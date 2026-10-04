"use client";

// 로그인 — docs/frontend_milestone.md 2단계.
//
// 아이디만 넣으면 로그인 버튼이 눌린다. 누르면 쓸 토큰을 이렇게 고른다.
//   1) 토큰 칸에 값이 있으면 그 토큰
//   2) 비어 있으면 이 브라우저가 그 아이디로 인증받아 기억해 둔 토큰(로그아웃해도 남는다)
// 고른 토큰은 GET /auth/me 로 "이 아이디의 것인가"를 확인한 뒤에 쓴다.
// 서버는 아이디만으로도 토큰을 내주므로 이건 보안 장치가 아니라 흐름 확인용이다.
//
// 서버는 만료와 위조를 둘 다 401 하나로 돌려준다. 그래서 만료는 토큰 안의 exp 를
// 읽어 따로 판단하고, 만료면 "토큰 재인증" 버튼으로 POST /auth/login 에서 새 토큰을 받는다.
// 로그인하면 홈(/home)으로 간다. 성향이 아직 없으면(GET /profile/me 404) 설문(/survey)으로
// 바로 간다 — 가입 뒤 첫 로그인이 이 경우다. 성향 없이는 전략을 만들 수 없어서 홈을 거칠
// 이유가 없다. "첫 로그인" 을 따로 기록하지 않고 성향 유무로 본다: 설문을 마치지 않고 나간
// 사람도 다음 로그인에 설문으로 간다.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import ScaffoldShell from "@/components/scaffold-shell";
import { api, ApiError, isExpired, loadRememberedToken, loadSession, saveSession } from "@/lib/api";

type MeResponse = { user_id: number; username: string; role: string };
type TokenResponse = { access_token: string; refresh_token: string };

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [token, setToken] = useState("");
  const [verified, setVerified] = useState<MeResponse | null>(null);
  const [expired, setExpired] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 로그인이 만료돼 다른 화면에서 넘어온 경우(?expired=1, components/require-login.tsx).
  // 아이디를 채우고 재인증 버튼을 바로 보여 준다 — 처음부터 다시 입력하게 하지 않는다.
  // 아이디는 주소에 싣지 않는다. 남아 있는 로그인 정보에서 읽는다.
  // useSearchParams 대신 window.location 을 읽는다: 앞의 것은 Suspense 경계가 필요하다.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("expired") !== "1") return;
    const stale = loadSession();
    if (stale?.username) setUsername(stale.username);
    setExpired(true);
    setMessage("로그인이 만료됐습니다. 토큰 재인증을 누른 뒤 로그인하세요.");
  }, []);

  // 아이디나 토큰을 고치면 앞의 인증·만료 안내는 무효다.
  function edit(set: (v: string) => void, value: string) {
    set(value);
    setVerified(null);
    setExpired(false);
    setMessage(null);
  }

  // 토큰이 이 아이디의 것인지 확인한다. 통과하면 주인 정보, 아니면 메시지를 띄우고 null.
  async function check(candidate: string): Promise<MeResponse | null> {
    if (isExpired(candidate)) {
      setExpired(true);
      setMessage("토큰의 유효기간이 지났습니다. 아래 토큰 재인증 버튼으로 새 토큰을 받으세요.");
      return null;
    }
    try {
      const me = await api<MeResponse>("/auth/me", { token: candidate });
      if (me.username === username.trim()) return me;
      setMessage(`인증 실패: 이 토큰은 '${me.username}' 의 것입니다. 입력한 아이디와 다릅니다.`);
    } catch (err) {
      setMessage(`인증 실패: ${err instanceof Error ? err.message : String(err)}`);
    }
    return null;
  }

  async function verify() {
    setBusy(true);
    setMessage(null);
    setVerified(await check(token.trim()));
    setBusy(false);
  }

  // 만료됐을 때 아이디로 새 토큰을 받아 토큰 칸에 넣고 곧바로 인증까지 한다.
  async function reissue() {
    setBusy(true);
    setMessage(null);
    try {
      const res = await api<TokenResponse>("/auth/login", { body: { username: username.trim() }, token: null });
      setToken(res.access_token);
      setExpired(false);
      const me = await check(res.access_token);
      setVerified(me);
      if (me) setMessage("새 토큰으로 재인증했습니다. 로그인 버튼을 누르세요.");
    } catch (err) {
      setMessage(`재인증 실패: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }

  async function login() {
    setBusy(true);
    setMessage(null);

    let access = token.trim();
    let me = verified;
    if (!me) {
      if (!access) {
        const remembered = loadRememberedToken(username.trim());
        if (!remembered) {
          setMessage("이 아이디로 인증된 토큰이 이 브라우저에 없습니다. 토큰을 입력해 인증하세요.");
          setBusy(false);
          return;
        }
        access = remembered;
      }
      me = await check(access);
      if (!me) {
        setBusy(false);
        return;
      }
    }

    // 로그인 화면에는 refresh 토큰이 없다. 만료되면 이 화면에서 재인증한다.
    saveSession({ access, refresh: "", username: me.username });
    router.push(await landing());
  }

  // 로그인 직후 갈 곳. 성향 확인이 404 가 아닌 이유로 실패하면 홈으로 보낸다 — 홈이 같은
  // 확인을 다시 하고 실패 사유를 화면에 보여 준다.
  async function landing(): Promise<string> {
    try {
      await api("/profile/me");
      return "/home";
    } catch (err) {
      return err instanceof ApiError && err.status === 404 ? "/survey" : "/home";
    }
  }

  return (
    <ScaffoldShell>
      <main>
        <h1>로그인</h1>
        <p>이 화면에서 하는 일: 아이디와 가입 때 받은 토큰으로 로그인한다.</p>

        <p>
          <label>
            아이디{" "}
            <input value={username} onChange={(e) => edit(setUsername, e.target.value)} disabled={busy} />
          </label>
        </p>
        <p>
          <label>
            토큰
            <br />
            <textarea
              className="flow-mono"
              value={token}
              onChange={(e) => edit(setToken, e.target.value)}
              rows={4}
              cols={60}
              disabled={busy}
            />
          </label>
        </p>
        <p>
          <button onClick={verify} disabled={busy || !username.trim() || !token.trim()}>
            인증
          </button>{" "}
          {verified && (
            <span>
              인증 통과 (아이디 {verified.username}, 번호 {verified.user_id})
            </span>
          )}
        </p>
        <p>
          <button onClick={login} disabled={busy || !username.trim()}>
            로그인
          </button>{" "}
          이 브라우저에서 이미 인증한 토큰이 유효기간 안이면 토큰 없이 로그인됩니다.
        </p>

        {busy && <p>확인 중…</p>}
        {message && <p>{message}</p>}
        {expired && (
          <p>
            <button onClick={reissue} disabled={busy || !username.trim()}>
              토큰 재인증
            </button>
          </p>
        )}

        <p>
          계정이 없으면 <Link href="/signup">회원가입</Link>
        </p>
      </main>
    </ScaffoldShell>
  );
}
