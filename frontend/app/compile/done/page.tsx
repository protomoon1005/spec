"use client";

// 전략 완료 — docs/frontend_milestone.md 4단계, 확정 결정 2번.
//
// 컴파일 결과로는 spec_id 와 종목 수만 온다. 내용은 GET /specs/{spec_id} 로 따로 받는다.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, loadCompileResult } from "@/lib/api";

type Done = { spec_id: string; universe_size: number };

type Rules = Record<string, unknown> | null;

type SpecDetail = {
  spec_id: string;
  name: string;
  status: string;
  input_prompt: string | null;
  rebalance: Rules;
  signal_rules: Rules;
  constraint_user: Rules;
  universe: { ticker: string; name: string; weight_min: number | null; weight_max: number | null }[];
};

const pct = (v: number | null) => (v === null ? "-" : `${Math.round(v * 1000) / 10}%`);

export default function DonePage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>전략 완료</h1>
        <p>이 화면에서 하는 일: 만들어진 전략서를 확인한다.</p>
        <RequireLogin>{() => <Result />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Result() {
  const router = useRouter();
  const [done, setDone] = useState<Done | null | undefined>(undefined);

  useEffect(() => {
    const r = loadCompileResult();
    setDone(r?.status === "completed" ? r : null);
  }, []);

  if (done === undefined) return <p>불러오는 중…</p>;
  if (done === null) {
    return (
      <p>
        완료된 전략서가 없습니다. <Link href="/compile">전략 요청으로</Link>
      </p>
    );
  }

  return (
    <>
      <p>전략서가 초안(draft)으로 저장되었습니다.</p>
      <ul>
        <li>전략서 번호: {done.spec_id}</li>
        <li>종목 수: {done.universe_size}</li>
      </ul>

      <h2>전략서 내용</h2>
      <SpecContents specId={done.spec_id} />

      <p>
        <button onClick={() => router.push("/hardcap")}>다음 — 하드캡</button>
      </p>
    </>
  );
}

function SpecContents({ specId }: { specId: string }) {
  const [spec, setSpec] = useState<SpecDetail | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api<SpecDetail>(`/specs/${specId}`)
      .then(setSpec)
      .catch((err) => setMessage(`전략서를 받지 못했습니다: ${err instanceof Error ? err.message : String(err)}`));
  }, [specId]);

  if (!spec) return <p>{message ?? "불러오는 중…"}</p>;

  return (
    <>
      <ul>
        <li>이름: {spec.name}</li>
        <li>요청 문장: {spec.input_prompt ?? "-"}</li>
      </ul>

      <h3>담은 종목과 비중 범위</h3>
      <table>
        <thead>
          <tr>
            <th>종목코드</th>
            <th>종목명</th>
            <th>최소 비중</th>
            <th>최대 비중</th>
          </tr>
        </thead>
        <tbody>
          {spec.universe.map((u) => (
            <tr key={u.ticker}>
              <td>{u.ticker}</td>
              <td>{u.name}</td>
              <td>{pct(u.weight_min)}</td>
              <td>{pct(u.weight_max)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>리밸런싱 방식</h3>
      <RuleList rules={spec.rebalance} />
      <h3>신호 규칙</h3>
      <RuleList rules={spec.signal_rules} />
      <h3>제약</h3>
      <RuleList rules={spec.constraint_user} />
    </>
  );
}

// 규칙 칸은 블록마다 모양이 달라서 키: 값 그대로 보여 준다. null 은 쓰지 않는 규칙이다.
function RuleList({ rules }: { rules: Rules }) {
  if (!rules) return <p>없음</p>;
  return (
    <ul>
      {Object.entries(rules).map(([k, v]) => (
        <li key={k}>
          {k}: {v === null ? "사용 안 함" : typeof v === "object" ? JSON.stringify(v) : String(v)}
        </li>
      ))}
    </ul>
  );
}
