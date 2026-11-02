"use client";

// 서버 API 가 필요한 화면을 감싼다. 로그인 화면은 없다(2026-11-02).
// 보관된 토큰이 없거나 만료됐으면 공용 시연 계정으로 알아서 들어간다(lib/api.ts ensureSession).

import { useEffect, useState } from "react";

import { ensureSession, type Session } from "@/lib/api";

export default function RequireLogin({ children }: { children: (session: Session) => React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    ensureSession()
      .then(setSession)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  if (error) return <p className="flow-msg err">서버에 연결하지 못했어요: {error}</p>;
  if (!session) return <p className="flow-state">준비 중…</p>;
  return <>{children(session)}</>;
}
