export const dynamic = "force-dynamic"; // 항상 최신 상태를 본다 — 캐시 금지

type HealthResponse = {
  status: string;
  database: string;
  redis: string;
  minio: string;
  llm_backend: string;
};

// 서버 컴포넌트에서 API를 직접 호출한다 (curl로 HTML만 받아도 상태가 그대로
// 보이도록 — 클라이언트 컴포넌트였다면 useEffect가 브라우저에서만 돌아서
// curl에는 "불러오는 중"만 찍힌다). 컨테이너 안에서 돌 때는 docker-compose
// 네트워크 별칭 api:8000을, 컨테이너 밖(호스트에서 npm run dev)에서는
// localhost:8000을 써야 해서, 브라우저용 NEXT_PUBLIC_API_BASE_URL과 별도로
// 서버 전용 API_INTERNAL_BASE_URL을 둔다.
import ScaffoldShell from "@/components/scaffold-shell";

const API_INTERNAL_BASE_URL = process.env.API_INTERNAL_BASE_URL ?? "http://localhost:8000";

export default async function HealthPage() {
  let body: HealthResponse | null = null;
  let error: string | null = null;

  try {
    const res = await fetch(`${API_INTERNAL_BASE_URL}/health`, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    body = (await res.json()) as HealthResponse;
  } catch (err) {
    error = err instanceof Error ? err.message : String(err);
  }

  return (
    <ScaffoldShell>
      <main>
        <h1>상태 확인</h1>
        <p>
          <code>{API_INTERNAL_BASE_URL}/health</code> 를 서버에서 호출한 결과다.
        </p>

        {error && <p style={{ color: "#dc2626" }}>API 연결 실패: {error}</p>}

        {body && (
          <ul style={{ listStyle: "none", padding: 0 }}>
            <li>전체: {body.status}</li>
            <li>데이터베이스: {body.database}</li>
            <li>Redis: {body.redis}</li>
            <li>MinIO: {body.minio}</li>
            <li>LLM 백엔드: {body.llm_backend}</li>
          </ul>
        )}
      </main>
    </ScaffoldShell>
  );
}
