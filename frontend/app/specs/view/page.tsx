"use client";

// 전략서 보기 — 전략 관리 목록에서 고른 전략서 하나의 내용.
// 전략서 v0.2 확인 화면(/compile/done)이 같은 내용을 보여 주므로 그리로 보낸다.

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";

export default function SpecViewPage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>전략서 보기</h1>
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
  const router = useRouter();
  const specId = useSearchParams().get("id");

  useEffect(() => {
    if (specId) router.replace(`/compile/done?strategy=${encodeURIComponent(specId)}`);
  }, [specId, router]);

  if (!specId) {
    return (
      <div className="flow-empty">
        <strong>전략서 번호가 없습니다.</strong>
        <Link href="/specs">목록에서 고르기</Link>
      </div>
    );
  }
  return <p className="flow-state">불러오는 중…</p>;
}
