import Link from "next/link";

export default function HomePage() {
  return (
    <main>
      <h1>spec</h1>
      <p>
        국내 ETF 모의운용 졸업작품 공통 인프라. 지금은 <Link href="/health">/health</Link>{" "}
        페이지만 있는 스캐폴드다 — 나머지 화면은 M4 담당이다.
      </p>
    </main>
  );
}
