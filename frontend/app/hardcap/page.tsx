"use client";

// 하드캡 — docs/frontend_milestone.md 5단계. 검사 본체(Validator)가 아직 없어 안내만 한다.
// 전략 완료 화면이 남긴 전략서 번호를 보여 주고 백테스트로 넘긴다(탭 저장소, lib/api.ts 4절).

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import ScaffoldShell from "@/components/scaffold-shell";
import { loadCompileResult } from "@/lib/api";

export default function HardcapPage() {
  const router = useRouter();
  const [specId, setSpecId] = useState<string | null>(null);

  useEffect(() => {
    const r = loadCompileResult();
    setSpecId(r?.status === "completed" ? r.spec_id : null);
  }, []);

  return (
    <ScaffoldShell>
      <main>
        <h1>하드캡</h1>
        <p>이 화면에서 하는 일: 전략서가 시스템 상한선(하드캡)을 넘지 않는지 검사한다.</p>
        <dl className="flow-kv">
          <dt>전략서 번호</dt>
          <dd>{specId ?? "없음 (이번 탭에서 완료한 전략서가 없습니다)"}</dd>
          <dt>검사 결과</dt>
          <dd>
            <span className="flow-badge muted">구현 예정</span>
          </dd>
        </dl>
        <p className="flow-msg warn">
          시스템 상한선(하드캡) 검사는 구현 예정입니다.
          <br />
          지금은 상한선을 넘는 전략서도 그대로 저장됩니다.
        </p>
        <div className="flow-next">
          <button className="primary" onClick={() => router.push("/backtest")}>
            다음 — 백테스트
          </button>
        </div>
      </main>
    </ScaffoldShell>
  );
}
