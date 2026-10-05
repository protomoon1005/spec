"use client";

// 전략서 보기 — 전략 관리 목록에서 고른 전략서 하나의 내용(GET /specs/{spec_id}).

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import SpecContents from "@/components/spec-contents";

export default function SpecViewPage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>전략서 보기</h1>
        <p>이 화면에서 하는 일: 전략서 하나의 종목, 비중 범위, 규칙을 본다.</p>
        <RequireLogin>
          {() => (
            // useSearchParams 는 Suspense 안에서만 쓸 수 있다 (Next.js 규칙).
            <Suspense fallback={<p className="flow-state">불러오는 중…</p>}>
              <View />
            </Suspense>
          )}
        </RequireLogin>
        <div className="flow-next" style={{ justifyContent: "flex-start" }}>
          <Link href="/specs">목록으로</Link>
        </div>
      </main>
    </ScaffoldShell>
  );
}

function View() {
  const specId = useSearchParams().get("id");
  if (!specId) {
    return (
      <div className="flow-empty">
        <strong>전략서 번호가 없습니다.</strong>
        <Link href="/specs">목록에서 고르기</Link>
      </div>
    );
  }
  // 번호는 SpecContents 머리에 있다.
  return <SpecContents specId={specId} />;
}
