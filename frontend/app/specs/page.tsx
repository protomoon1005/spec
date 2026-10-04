"use client";

// 전략 관리 — 내가 만든 전략서 목록(GET /specs). 이름을 누르면 내용을 본다.

import Link from "next/link";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { StatusBadge } from "@/components/spec-contents";
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

  if (!list) {
    return message ? (
      <p className="flow-msg err" role="alert">
        {message}
      </p>
    ) : (
      <p className="flow-state">불러오는 중…</p>
    );
  }
  if (list.length === 0) {
    return (
      <div className="flow-empty">
        <strong>아직 만든 전략서가 없습니다.</strong>
        <Link href="/compile">전략 만들기</Link>
      </div>
    );
  }

  return (
    <div className="flow-table">
      <table style={{ minWidth: 640 }}>
        <thead>
          <tr>
            <th>이름</th>
            <th>상태</th>
            <th className="num">종목 수</th>
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
              <td>
                <StatusBadge status={s.status} />
              </td>
              <td className="num">{s.universe_size}</td>
              <td className="mono" style={{ color: "var(--c-muted)" }}>
                {formatDate(s.created_at)}
              </td>
              <td className="mono" style={{ color: "var(--c-muted)" }}>
                {s.spec_id}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
