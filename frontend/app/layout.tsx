export const metadata = {
  title: "spec",
  description: "국내 ETF 모의운용 졸업작품 — 공통 인프라 프론트엔드 스캐폴드",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: 0, padding: "2rem" }}>
        {children}
      </body>
    </html>
  );
}
