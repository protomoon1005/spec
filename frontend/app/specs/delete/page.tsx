"use client";

// 전략 삭제 — DELETE /specs/{spec_id}.
//
// 승인 전(draft) 전략서만 지울 수 있다. 승인한 전략서는 고칠 수도 지울 수도 없고,
// 백테스트·승인 기록이 붙은 것도 서버가 409 로 거부한다 — 그 응답을 그대로 보여 준다.
// 브라우저 확인창 대신 "정말 삭제" 버튼을 한 번 더 누르게 한다.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api } from "@/lib/api";
import { formatDate, type SpecListItem } from "@/lib/specs";

export default function SpecDeletePage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>전략 삭제</h1>
        <p>이 화면에서 하는 일: 승인 전(draft) 전략서를 지운다. 지운 전략서는 되살릴 수 없다.</p>
        <RequireLogin>{() => <DeleteList />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function DeleteList() {
  const [list, setList] = useState<SpecListItem[] | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    api<SpecListItem[]>("/specs")
      .then(setList)
      .catch((err) => setMessage(`목록을 받지 못했습니다: ${err instanceof Error ? err.message : String(err)}`));
  }, []);

  useEffect(reload, [reload]);

  async function remove(spec: SpecListItem) {
    setBusy(true);
    setMessage(null);
    try {
      await api(`/specs/${encodeURIComponent(spec.spec_id)}`, { method: "DELETE" });
      setMessage(`'${spec.name}' (${spec.spec_id}) 을 지웠습니다.`);
      reload();
    } catch (err) {
      setMessage(`삭제 실패: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
      setBusy(false);
    }
  }

  if (!list) return <p>{message ?? "불러오는 중…"}</p>;

  return (
    <>
      {message && <p>{message}</p>}
      {list.length === 0 ? (
        <p>
          지울 전략서가 없습니다. <Link href="/specs">전략 관리로</Link>
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>이름</th>
              <th>상태</th>
              <th>만든 때</th>
              <th>전략서 번호</th>
              <th>삭제</th>
            </tr>
          </thead>
          <tbody>
            {list.map((s) => (
              <tr key={s.spec_id}>
                <td>{s.name}</td>
                <td>{s.status}</td>
                <td>{formatDate(s.created_at)}</td>
                <td>{s.spec_id}</td>
                <td>
                  {s.status !== "draft" ? (
                    "승인 후라 지울 수 없음"
                  ) : pending === s.spec_id ? (
                    <>
                      <button onClick={() => remove(s)} disabled={busy}>
                        정말 삭제
                      </button>{" "}
                      <button onClick={() => setPending(null)} disabled={busy}>
                        취소
                      </button>
                    </>
                  ) : (
                    <button onClick={() => setPending(s.spec_id)} disabled={busy}>
                      삭제
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
