"use client";

// 하드캡 — docs/frontend_milestone.md 5단계. 검사 본체(Validator)가 아직 없어 안내만 한다.

import { useRouter } from "next/navigation";

import ScaffoldShell from "@/components/scaffold-shell";

export default function HardcapPage() {
  const router = useRouter();
  return (
    <ScaffoldShell>
      <main>
        <h1>하드캡</h1>
        <p>이 화면에서 하는 일: 전략서가 시스템 상한선(하드캡)을 넘지 않는지 검사한다.</p>
        <p>시스템 상한선(하드캡) 검사는 구현 예정입니다.</p>
        <p>지금은 상한선을 넘는 전략서도 그대로 저장됩니다.</p>
        <p>
          <button onClick={() => router.push("/backtest")}>다음 — 백테스트</button>
        </p>
      </main>
    </ScaffoldShell>
  );
}
