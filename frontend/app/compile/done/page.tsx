"use client";

// 전략 완료 — docs/frontend_milestone.md 4단계, 확정 결정 2번.
//
// 컴파일 결과로는 spec_id 와 종목 수만 온다. 내용은 GET /specs/{spec_id} 로 따로 받는다
// (components/spec-contents.tsx — 번호 · 종목 수도 거기서 보여 준다).

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import SpecContents from "@/components/spec-contents";
import { loadCompileResult } from "@/lib/api";

type Done = { spec_id: string; universe_size: number };

export default function DonePage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>전략 완료</h1>
        <p>이 화면에서 하는 일: 만들어진 전략서를 확인한다.</p>
        <RequireLogin>{() => <Result />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Result() {
  const router = useRouter();
  const [done, setDone] = useState<Done | null | undefined>(undefined);

  useEffect(() => {
    const r = loadCompileResult();
    setDone(r?.status === "completed" ? r : null);
  }, []);

  if (done === undefined) return <p>불러오는 중…</p>;
  if (done === null) {
    return (
      <p>
        완료된 전략서가 없습니다. <Link href="/compile">전략 요청으로</Link>
      </p>
    );
  }

  return (
    <>
      <p className="flow-run-title flow-done-title">
        <span aria-hidden="true">✓</span> 전략서를 만들었어요
      </p>
      <p className="flow-hint">초안으로 저장됐어요. 하드캡 확인과 백테스트를 거쳐 승인합니다.</p>

      <SpecContents specId={done.spec_id} />

      <div className="flow-actions">
        <button onClick={() => router.push("/hardcap")}>다음 — 하드캡 확인</button>
      </div>
    </>
  );
}
