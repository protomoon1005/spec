export const metadata = {
  title: "spec",
  description: "국내 ETF 모의운용 졸업작품 — 공통 인프라 프론트엔드 스캐폴드",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      {/* 스캐폴드용 여백/폰트는 body 인라인 스타일이 아니라 ScaffoldShell 로
          옮겼다 — 인라인 스타일은 스타일시트를 이겨서, /report 처럼 전면
          레이아웃이 필요한 라우트가 여백을 되돌릴 수 없기 때문이다. */}
      <body style={{ margin: 0 }}>{children}</body>
    </html>
  );
}
