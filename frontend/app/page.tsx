"use client";

// 시작 — docs/frontend_milestone.md 9단계.
//
// 로그인 여부에 따라 보낸다.
//   보관된 토큰 없음       → /login
//   토큰 유효(GET /auth/me) → /home
//   토큰 만료·무효(401)    → /login (로그인 화면에서 재인증)
// 그 밖의 오류는 이동하지 않고 서버 응답을 그대로 보여 준다.
// 개발용 링크(/health, /report)는 맨 아래에 남긴다.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import ScaffoldShell from "@/components/scaffold-shell";
import { api, ApiError, loadSession } from "@/lib/api";

export default function HomePage() {
  const router = useRouter();
  const [message, setMessage] = useState("로그인 상태 확인 중…");

  useEffect(() => {
    if (!loadSession()) {
      router.replace("/login");
      return;
    }
    let cancelled = false;
    api("/auth/me")
      .then(() => {
        if (!cancelled) router.replace("/home");
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) router.replace("/login");
        else setMessage(`로그인 확인 실패: ${err instanceof Error ? err.message : String(err)}`);
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <ScaffoldShell>
      <main>
        <h1>spec</h1>
        <p>이 화면에서 하는 일: 로그인 여부에 따라 로그인 화면이나 홈으로 보낸다.</p>
        <p>{message}</p>

        <h2>개발용</h2>
        <ul>
          <li>
            <Link href="/health">/health</Link> — 인프라 상태 확인
          </li>
          <li>
            <Link href="/report">/report</Link> — 백테스트 결과 리포트 (M4)
          </li>
        </ul>
      </main>
    </ScaffoldShell>
  );
}
