"use client";

// 홈 — 로그인하면 여기로 온다. 전략 만들기 · 전략 관리 · 전략 삭제로 가는 입구.
// 로그아웃은 화면 위 줄에 있다.

import Link from "next/link";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";

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
  return (
    <nav className="flow-menu" aria-label="홈 메뉴">
      <Link href="/compile">
        <b>전략 만들기</b>
        <span>문장으로 새 전략서를 만든다</span>
      </Link>
      <Link href="/specs">
        <b>전략 관리</b>
        <span>내가 만든 전략서 목록과 내용을 본다</span>
      </Link>
      <Link href="/specs/delete">
        <b>전략 삭제</b>
        <span>draft 전략서를 지운다</span>
      </Link>
    </nav>
  );
}
