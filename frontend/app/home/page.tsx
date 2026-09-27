"use client";

// 홈 — 로그인하면 여기로 온다.
//
// 전략 만들기 · 전략 관리 · 전략 삭제 · 성향 설문으로 가는 입구. 로그아웃은 화면 위 줄에 있다.
// 성향이 아직 없으면(GET /profile/me 404) 전략을 만들 수 없으므로 설문으로 먼저 보낸다.

import Link from "next/link";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, ApiError } from "@/lib/api";

export default function HomePage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>홈</h1>
        <p>이 화면에서 하는 일: 전략을 만들거나, 만든 전략을 보고 지운다.</p>
        <RequireLogin>{() => <Menu />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Menu() {
  // undefined: 확인 중, true/false: 성향 확정 여부
  const [hasProfile, setHasProfile] = useState<boolean | undefined>(undefined);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api("/profile/me")
      .then(() => setHasProfile(true))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) setHasProfile(false);
        else setMessage(`성향 확인 실패: ${err instanceof Error ? err.message : String(err)}`);
      });
  }, []);

  return (
    <>
      {hasProfile === false && (
        <p>
          성향이 아직 없습니다. 전략을 만들려면 먼저 <Link href="/survey">성향 설문</Link>을 마치세요.
        </p>
      )}
      {message && <p>{message}</p>}
      <ul>
        <li>
          <Link href={hasProfile === false ? "/survey" : "/compile"}>전략 만들기</Link> — 문장으로 새 전략서를 만든다
        </li>
        <li>
          <Link href="/specs">전략 관리</Link> — 내가 만든 전략서 목록과 내용을 본다
        </li>
        <li>
          <Link href="/specs/delete">전략 삭제</Link> — 승인 전(draft) 전략서를 지운다
        </li>
        <li>
          <Link href="/survey">성향 설문 {hasProfile === false ? "하기" : "다시 하기"}</Link> — 투자 성향을 다시
          정한다. 이미 만든 전략서는 만들 때의 성향을 그대로 따른다
        </li>
      </ul>
    </>
  );
}
