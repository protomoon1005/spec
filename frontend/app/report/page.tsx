import Report from "@/components/report";
import LiveReport from "@/components/report/live";

// 서버 컴포넌트. run_id 가 있는지를 서버에서 먼저 보고 갈라진다.
//
//   /report            정적 데모 결과(data/backtest-result.json). 로그인 없이 볼 수 있다.
//                      서버가 완성된 화면을 그려 보낸다.
//   /report?run_id=N   사용자가 돌린 실행 N. 토큰이 브라우저에 있어 서버가 대신 받아 올 수
//                      없으므로 LiveReport 가 브라우저에서 받는다.
//
// 브라우저에서만 갈라지게 하면 run_id 가 있어도 서버가 데모 결과를 먼저 그려 보내고,
// 받아 온 뒤에 바뀐다 — 잠깐이지만 사용자가 만들지 않은 전략의 수치가 보인다.
export default async function ReportPage({ searchParams }: { searchParams: Promise<{ run_id?: string }> }) {
  const { run_id: runId } = await searchParams;
  if (runId !== undefined) return <LiveReport runId={runId} />;
  return <Report />;
}
