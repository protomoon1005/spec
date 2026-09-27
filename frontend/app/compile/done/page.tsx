"use client";

// 전략 완료 — docs/frontend_milestone.md 4단계, 확정 결정 2번.
//
// 컴파일 결과로는 spec_id 와 종목 수만 온다. 내용은 GET /specs/{spec_id} 로 따로 받는다.

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
      <p>전략서가 초안(draft)으로 저장되었습니다.</p>
      <ul>
        <li>전략서 번호: {done.spec_id}</li>
        <li>종목 수: {done.universe_size}</li>
      </ul>

      <h2>전략서 내용</h2>
      <SpecContents specId={done.spec_id} />

      <p>
        <button onClick={() => router.push("/hardcap")}>다음 — 하드캡</button>
      </p>
    </>
  );
}
