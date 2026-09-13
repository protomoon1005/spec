import Link from "next/link";

import ScaffoldShell from "@/components/scaffold-shell";

export default function HomePage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>spec</h1>
        <p>국내 ETF 모의운용 졸업작품 공통 인프라.</p>
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
