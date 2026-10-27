// 관점 브리핑 (U09) — docs/frontend_milestone.md 8단계.
//
//   /views             흐름의 마지막 화면. 이번 탭에서 돌린 백테스트가 있으면 그 실행으로 안내한다.
//   /views?run_id=N    실행 N 의 관점 기록(리밸런싱마다 관점 가중치와 종목별 확률)을 그린다.
//
// /report 와 같은 방식이다: run_id 는 서버에서 읽고, 토큰이 브라우저에 있으므로 실행 기록은
// 브라우저(components/views-briefing.tsx)가 받는다.

import ScaffoldShell from "@/components/scaffold-shell";
import ViewsBriefing from "@/components/views-briefing";

export default async function ViewsPage({ searchParams }: { searchParams: Promise<{ run_id?: string }> }) {
  const { run_id: runId } = await searchParams;
  return (
    <ScaffoldShell wide>
      <main>
        <h1>관점 브리핑</h1>
        <p>이 화면에서 하는 일: 세 관점의 가중치가 실적에 따라 어떻게 바뀌었고, 마지막 판단에서 종목마다 어떤 확률을 냈는지 본다.</p>
        <ViewsBriefing runId={runId} />
      </main>
    </ScaffoldShell>
  );
}
