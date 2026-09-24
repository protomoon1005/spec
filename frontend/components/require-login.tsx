"use client";

// 로그인이 필요한 화면을 감싼다. 보관된 토큰이 없으면 로그인 화면으로 보낸다.
// 토큰이 살아 있는지는 여기서 보지 않는다 — 만료됐으면 다음 API 호출이 401 을
// 그대로 보여 주고, 사용자는 다시 로그인한다(토큰 자동 재발급은 범위 밖).

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { loadSession, type Session } from "@/lib/api";

export default function RequireLogin({ children }: { children: (session: Session) => React.ReactNode }) {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);

  useEffect(() => {
    const s = loadSession();
    if (s) setSession(s);
    else router.replace("/login");
  }, [router]);

  return session ? children(session) : <p>로그인 확인 중…</p>;
}
