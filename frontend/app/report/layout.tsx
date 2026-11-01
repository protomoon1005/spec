// report.css 는 여기서만 import 한다. App Router 는 라우트 단위로 CSS 청크를
// 나누므로, Tailwind preflight 가 /report 밖(스캐폴드 페이지 / 와 /health)에
// 영향을 주지 않는다.
import "./report.css";

export const metadata = {
  title: "백테스트 결과 리포트",
  description: "전략 Spec 기반 ETF 자동운용 백테스트 리포트 (M4)",
};

export default function ReportLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
