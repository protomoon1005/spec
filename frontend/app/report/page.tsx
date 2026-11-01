import Link from "next/link";

import LiveReport from "@/components/report/live";

// /report?run_id=N — 사용자가 돌린 실행 N 을 그린다. 토큰이 브라우저에 있어 서버가 대신
// 받아 올 수 없으므로 LiveReport 가 브라우저에서 받는다. 실행 번호가 없으면 안내만 한다.
export default async function ReportPage({ searchParams }: { searchParams: Promise<{ run_id?: string }> }) {
  const { run_id: runId } = await searchParams;
  if (runId !== undefined) return <LiveReport runId={runId} />;
  return (
    <main style={{ padding: 40 }}>
      <p>
        볼 실행 번호가 없습니다. <Link href="/backtest">백테스트 화면</Link>에서 결과를 연 뒤 리포트로 오세요.
      </p>
    </main>
  );
}
