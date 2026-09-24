"use client";

// 로그인 — docs/frontend_milestone.md 2단계.
//
// 아이디와 가입 때 받은 토큰을 넣고, 인증 버튼으로 "이 토큰이 이 아이디의 것인가"를
// 확인한 뒤에만 로그인 버튼이 눌린다. 확인은 GET /auth/me 가 돌려준 username 과
// 입력한 아이디를 비교한다.
// 서버는 아이디만으로도 토큰을 내주므로 이건 보안 장치가 아니라 흐름 확인용이다.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import ScaffoldShell from "@/components/scaffold-shell";
import { api, ApiError, saveSession } from "@/lib/api";

type MeResponse = { user_id: number; username: string; role: string };

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [token, setToken] = useState("");
  const [verified, setVerified] = useState<MeResponse | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 아이디나 토큰을 고치면 앞의 인증은 무효다.
  function edit(set: (v: string) => void, value: string) {
    set(value);
    setVerified(null);
    setMessage(null);
  }

  async function verify() {
    setBusy(true);
    setMessage(null);
    try {
      const me = await api<MeResponse>("/auth/me", { token: token.trim() });
      if (me.username === username.trim()) {
        setVerified(me);
      } else {
        setMessage(`인증 실패: 이 토큰은 '${me.username}' 의 것입니다. 입력한 아이디와 다릅니다.`);
      }
    } catch (err) {
      setMessage(`인증 실패: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }

  async function login() {
    if (!verified) return;
    // 로그인 화면에는 refresh 토큰이 없다. 만료되면 다시 로그인한다(자동 재발급은 범위 밖).
    saveSession({ access: token.trim(), refresh: "", username: verified.username });
    setBusy(true);
    try {
      // 성향이 확정됐는지로 다음 화면을 정한다. 없으면 404 가 온다.
      await api("/profile/me");
      router.push("/compile");
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        router.push("/survey");
      } else {
        setMessage(`다음 단계 확인 실패: ${err instanceof Error ? err.message : String(err)}`);
        setBusy(false);
      }
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
          <button onClick={login} disabled={busy || !verified}>
            로그인
          </button>{" "}
          인증이 통과해야 누를 수 있습니다.
        </p>

        {busy && <p>확인 중…</p>}
        {message && <p>{message}</p>}

        <p>
          계정이 없으면 <Link href="/signup">회원가입</Link>
        </p>
      </main>
    </ScaffoldShell>
  );
}
