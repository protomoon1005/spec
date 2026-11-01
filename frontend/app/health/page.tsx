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

// 서버는 항목마다 "ok" 또는 "down" 을 준다. 그 밖의 값은 정상으로 치지 않는다.
const HEALTHY = new Set(["ok"]);

const API_INTERNAL_BASE_URL =process.env.API_INTERNAL_BASE_URL ?? "http://localhost:8000";

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

        {error && (
          <p className="flow-msg err" role="alert">
            API 연결 실패: {error}
          </p>
        )}

        {body && (
          <ul>
            {(
              [
                ["전체", body.status],
                ["데이터베이스", body.database],
                ["Redis", body.redis],
                ["MinIO", body.minio],
                ["LLM 백엔드", body.llm_backend],
              ] as const
            ).map(([label, value]) => (
              <li key={label} style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
                {/* curl 로 받아도 "항목: 값" 으로 읽히게 콜론을 남긴다 */}
                <span>{label}:</span>
                <span className={`flow-badge ${HEALTHY.has(value) ? "ok" : "err"}`}>{value}</span>
              </li>
            ))}
          </ul>
        )}
      </main>
    </ScaffoldShell>
  );
}
