"use client";

// 회원가입 — docs/frontend_milestone.md 1단계.
//
// 중복확인 버튼이 곧 가입 요청이다. 서버에 "확인만 하는" 주소가 없어서, 409 면
// 중복이고 성공이면 그 자리에서 가입과 토큰 발급이 끝난다.
// 토큰은 여기서 보관하지 않는다 — 로그인 화면에서 붙여 넣어 인증하는 흐름이다.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import ScaffoldShell from "@/components/scaffold-shell";
import { api, ApiError } from "@/lib/api";

type TokenResponse = { access_token: string; refresh_token: string };

export default function SignupPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  async function check() {
    setBusy(true);
    setMessage(null);
    try {
      const res = await api<TokenResponse>("/auth/signup", { body: { username }, token: null });
      setToken(res.access_token);
    } catch (err) {
      setMessage(
        err instanceof ApiError && err.status === 409
          ? `이미 있는 아이디입니다. (${err.message})`
          : String(err instanceof Error ? err.message : err),
      );
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
    } catch (err) {
      setMessage(`복사 실패: ${err instanceof Error ? err.message : String(err)} — 직접 선택해 복사하세요`);
    }
  }

  return (
    <ScaffoldShell>
      <main>
        <h1>회원가입</h1>
        <p>이 화면에서 하는 일: 아이디를 정하고, 로그인에 쓸 토큰을 받는다.</p>

        {token === null ? (
          <section className="flow-card">
            <div className="flow-field">
              <label>
                아이디
                <input value={username} onChange={(e) => setUsername(e.target.value)} disabled={busy} />
              </label>
              <button className="primary" onClick={check} disabled={busy || !username.trim()}>
                중복확인
              </button>
            </div>
            <p className="flow-hint" style={{ marginTop: 10 }}>
              형식 제한은 없습니다. 중복이 아니면 확인과 동시에 바로 가입됩니다.
            </p>
            {busy && <p className="flow-state">확인 중…</p>}
          </section>
        ) : (
          <section className="flow-card">
            <p className="flow-msg ok" style={{ marginTop: 0 }}>
              가입이 완료되었습니다. 아이디: <b className="mono">{username.trim()}</b>
            </p>
            <p style={{ marginTop: 14 }}>아래 토큰을 복사해 두세요. 로그인할 때 아이디와 함께 입력합니다.</p>
            <textarea readOnly value={token} rows={4} cols={60} aria-label="발급된 토큰" />
            <div className="flow-actions" style={{ marginTop: 12 }}>
              <button onClick={copy}>토큰 복사</button>
              {copied && <span className="flow-hint">복사되었습니다.</span>}
            </div>
            <div className="flow-next">
              <button className="primary" onClick={() => router.push("/login")}>
                로그인으로 돌아가기
              </button>
            </div>
          </section>
        )}

        {message && <p className="flow-msg err" role="alert">{message}</p>}

        <p className="flow-hint" style={{ marginTop: 24 }}>
          이미 계정이 있으면 <Link href="/login">로그인</Link>
        </p>
      </main>
    </ScaffoldShell>
  );
}
