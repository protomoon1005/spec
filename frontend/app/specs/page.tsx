"use client";

// 전략 관리 — 내가 만든 전략서 목록(GET /specs). 이름을 누르면 내용을 본다.

import Link from "next/link";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api } from "@/lib/api";
import { formatDate, type SpecListItem } from "@/lib/specs";

export default function SpecsPage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>전략 관리</h1>
        <p>이 화면에서 하는 일: 내가 만든 전략서 목록을 보고, 하나를 골라 내용을 본다.</p>
        <RequireLogin>{() => <SpecList />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function SpecList() {
  const [list, setList] = useState<SpecListItem[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api<SpecListItem[]>("/specs")
      .then(setList)
      .catch((err) => setMessage(`목록을 받지 못했습니다: ${err instanceof Error ? err.message : String(err)}`));
  }, []);

  if (!list) return <p>{message ?? "불러오는 중…"}</p>;
  if (list.length === 0) {
    return (
      <p>
        아직 만든 전략서가 없습니다. <Link href="/compile">전략 만들기</Link>
      </p>
    );
  }

  return (
    <table>
      <thead>
        <tr>
          <th>이름</th>
          <th>상태</th>
          <th>종목 수</th>
          <th>만든 때</th>
          <th>전략서 번호</th>
        </tr>
      </thead>
      <tbody>
        {list.map((s) => (
          <tr key={s.spec_id}>
            <td>
              <Link href={`/specs/view?id=${encodeURIComponent(s.spec_id)}`}>{s.name}</Link>
            </td>
            <td>{s.status}</td>
            <td>{s.universe_size}</td>
            <td>{formatDate(s.created_at)}</td>
            <td>{s.spec_id}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
