"use client";

// 전략 완료 — docs/frontend_milestone.md 4단계, 확정 결정 2번.
//
// 컴파일 결과로는 spec_id 와 종목 수, 담지 못한 종목(excluded)이 온다. 내용은 GET /specs/{spec_id} 로 따로 받는다.
// 결과는 진행 화면이 completed 이벤트를 받아 탭 저장소에 통째로 둔 것을 읽는다(lib/api.ts saveCompileResult).

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import ExcludedList from "@/components/excluded-list";
import SpecContents from "@/components/spec-contents";
import { loadCompileResult } from "@/lib/api";
import { readExcluded, type ExcludedItem } from "@/lib/excluded";

type Done = { spec_id: string; universe_size: number; excluded: ExcludedItem[] };

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
    setDone(r?.status === "completed" ? { ...r, excluded: readExcluded(r.excluded) } : null);
  }, []);

  if (done === undefined) return <p className="flow-state">불러오는 중…</p>;
  if (done === null) {
    return (
      <div className="flow-empty">
        <strong>완료된 전략서가 없습니다.</strong>
        <Link href="/compile">전략 요청으로</Link>
      </div>
    );
  }

  return (
    <>
      <p className="flow-msg ok" style={{ marginTop: 0, marginBottom: 16 }}>
        전략서를 만들었어요. 초안으로 저장됐고, 하드캡 확인과 백테스트를 거쳐 승인합니다.
      </p>
      {/* 번호 · 종목 수는 SpecContents 가 보여 준다. */}
      <SpecContents specId={done.spec_id} />

      {done.excluded.length > 0 && (
        <>
          <h2>요청했지만 담지 못한 종목</h2>
          <div className="flow-card">
            <ExcludedList items={done.excluded} />
          </div>
        </>
      )}

      <div className="flow-next">
        <button className="primary" onClick={() => router.push("/hardcap")}>
          다음 — 하드캡 확인
        </button>
      </div>
    </>
  );
}
