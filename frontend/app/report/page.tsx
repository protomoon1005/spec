import Report from "@/components/report";

// 서버 컴포넌트. 탭 상태를 쓰는 본문만 클라이언트 컴포넌트로 분리했다.
//
// 지금 화면의 수치는 lib/backtest-mock.ts 의 고정 시드 목데이터다. 백테스트
// 러너(M4)와 backtest 라우터(현재 501)가 붙으면, 이 페이지에서
// `${API_INTERNAL_BASE_URL}/backtest/runs/{run_id}` 를 서버에서 호출해
// 같은 형태의 값을 넘기면 된다 — app/health/page.tsx 와 같은 패턴이다.
export default function ReportPage() {
  return <Report />;
}
