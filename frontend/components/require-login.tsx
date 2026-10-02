"use client";

// 로그인이 필요한 화면을 감싼다. 보관된 토큰이 없으면 로그인 화면으로 보낸다.
// 로그인한 아이디와 홈 링크, 로그아웃 버튼을 화면 위에 한 줄로 둔다. 로그아웃은 보관된 토큰을
// 지우고 로그인 화면으로 보낸다.
//
// 토큰 만료(30분)도 여기서 본다. 예전에는 보지 않고 "다음 API 호출이 401 을 보여 주면
// 사용자가 다시 로그인한다" 였는데, 실제로는 화면이 문항·결과를 못 받아 그냥 비어 보였고
// 401 이 만료 때문이라는 걸 사용자가 알 수 없었다. 자동 재발급은 여전히 하지 않는다 —
// 다시 로그인하게 안내할 뿐이다.
//   - 화면을 열 때 이미 만료됐으면 로그인 화면으로 보낸다. 잃을 입력이 없다.
//   - 화면을 보는 도중 만료되면 쫓아내지 않는다. 설문·전략 요청을 쓰던 중일 수 있어서
//     로그인 막대에만 만료를 띄운다.
// 만료된 로그인 정보는 지우지 않는다. 로그인 화면이 거기서 아이디를 읽어 채운다.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { clearSession, isExpired, loadSession, tokenExpiry, type Session } from "@/lib/api";

const EXPIRED_LOGIN = "/login?expired=1";

export default function RequireLogin({ children }: { children: (session: Session) => React.ReactNode }) {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [expiredNow, setExpiredNow] = useState(false);

  useEffect(() => {
    const s = loadSession();
    if (!s) router.replace("/login");
    else if (isExpired(s.access)) router.replace(EXPIRED_LOGIN);
    else setSession(s);
  }, [router]);

  // 보는 도중 만료되는 시점에 맞춰 막대에 알린다.
  useEffect(() => {
    if (!session) return;
    const exp = tokenExpiry(session.access);
    if (exp === null) return;
    const timer = window.setTimeout(() => setExpiredNow(true), Math.max(0, exp - Date.now()));
    return () => window.clearTimeout(timer);
  }, [session]);

  function logout() {
    clearSession();
    router.replace("/login");
  }

  if (!session) return <p>로그인 확인 중…</p>;
  return (
    <>
      {/* 모양은 app/flow.css 의 .flow-session. */}
      <div className="flow-session">
        <span>
          로그인 <strong>{session.username}</strong>
        </span>
        {expiredNow && (
          <span className="flow-expired" role="alert">
            로그인 만료 — <Link href={EXPIRED_LOGIN}>다시 로그인</Link>
          </span>
        )}
        <span className="flow-session-spacer" />
        <Link href="/home">홈</Link>
        <button onClick={logout}>로그아웃</button>
      </div>
      {children(session)}
    </>
  );
}
