// 흐름 화면(가입 · 로그인 · 성향 · 전략 · 백테스트 · 확인 …)의 바탕.
//
// 이 껍데기가 .flow-root 를 달고, app/flow.css 가 그 아래의 기본 HTML 요소를 칠한다.
// 화면들은 기본 요소만 쓰므로(docs/frontend_milestone.md) 화면을 고치지 않아도 같은
// 모양이 된다.
//
// 색·반경은 리포트와 같은 토큰(components/report/tokens.ts)을 CSS 변수로 내려 준다.
// 리포트와 흐름 화면이 값을 따로 적으면 언젠가 색이 갈라진다 — 사용자는 한 앱으로 본다.
//
// 예전에는 흰 바탕에 검정 글자를 고정했다. 그때 이유(다크 모드 브라우저에서 투명 바탕
// 위 검정 글자가 안 보임)는 지금도 같아서, 바탕과 글자색은 계속 명시한다.
import type { CSSProperties } from "react";
import { C, R } from "@/components/report/tokens";

const TOKEN_VARS = {
  ...Object.fromEntries(Object.entries(C).map(([k, v]) => [`--c-${k}`, v])),
  ...Object.fromEntries(Object.entries(R).map(([k, v]) => [`--r-${k}`, `${v}px`])),
} as CSSProperties;

export default function ScaffoldShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flow-root" style={TOKEN_VARS}>
      {children}
    </div>
  );
}
