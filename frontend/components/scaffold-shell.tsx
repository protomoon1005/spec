// 스캐폴드 페이지(/ 와 /health)의 여백과 기본 폰트. 원래 루트 레이아웃의
// body 인라인 스타일이었는데, 인라인 스타일은 스타일시트를 이겨서 /report
// 같은 전면 레이아웃 라우트가 여백을 되돌릴 수 없었다.
//
// 배경과 글자색을 명시하는 이유: 지정하지 않으면 body 가 투명이고 글자색이
// 검정이라, 브라우저가 다크 모드일 때 검정 배경 위 검정 글자가 되어 읽을 수
// 없다. color-scheme 을 light 로 고정해 폼 컨트롤도 같이 밝게 맞춘다.
export default function ScaffoldShell({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontFamily: "system-ui, sans-serif",
        padding: "2rem",
        minHeight: "100vh",
        colorScheme: "light",
        background: "#ffffff",
        color: "#111827",
      }}
    >
      {children}
    </div>
  );
}
