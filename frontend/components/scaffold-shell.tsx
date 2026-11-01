// 흐름 화면(가입 → 로그인 → 성향 → 전략서 → 하드캡 → 백테스트 → 최종 확인 → 관점 브리핑)과
// /health 의 공통 틀. 머리줄(제품 이름 · 바로가기 · 단계 레일)과 본문 폭을 정한다.
//
// 원래 루트 레이아웃의 body 인라인 스타일이었는데, 인라인 스타일은 스타일시트를 이겨서
// /report 같은 전면 레이아웃 라우트가 여백을 되돌릴 수 없었다. 그래서 여기 둔다.
//
// 색·글꼴은 /report 와 같은 어두운 바탕(components/flow.css 가 tokens.ts 를 옮겨 둔 것).
// 배경과 글자색을 명시해야 브라우저 테마와 상관없이 읽힌다. color-scheme 도 dark 로
// 고정해 폼 컨트롤이 바탕과 맞게 그려지게 한다.
//
// wide: 차트가 있는 화면(관점 브리핑)은 본문 폭을 넓힌다.

import "./flow.css";

import FlowNav from "./flow-nav";

export default function ScaffoldShell({ children, wide = false }: { children: React.ReactNode; wide?: boolean }) {
  return (
    <div className="flow-root">
      <FlowNav wide={wide} />
      <div className={wide ? "flow-main wide" : "flow-main"}>{children}</div>
    </div>
  );
}
