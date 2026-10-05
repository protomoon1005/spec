"use client";

// 전략서 하나의 내용 — GET /specs/{spec_id}. 전략 완료 화면과 전략 관리 상세 화면이 같이 쓴다.

import { useEffect, useState } from "react";

import { api } from "@/lib/api";

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

export default function SpecContents({ specId }: { specId: string }) {
  const [spec, setSpec] = useState<SpecDetail | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api<SpecDetail>(`/specs/${specId}`)
      .then(setSpec)
      .catch((err) => setMessage(`전략서를 받지 못했습니다: ${err instanceof Error ? err.message : String(err)}`));
  }, [specId]);

  if (!spec) {
    return message ? (
      <p className="flow-msg err" role="alert">
        {message}
      </p>
    ) : (
      <p className="flow-state">불러오는 중…</p>
    );
  }

  return (
    <>
      <dl className="flow-kv">
        <dt>이름</dt>
        <dd className="prose">{spec.name}</dd>
        <dt>상태</dt>
        <dd>
          <StatusBadge status={spec.status} />
        </dd>
        <dt>요청 문장</dt>
        <dd className="prose">{spec.input_prompt ?? "-"}</dd>
      </dl>

      <h3>담은 종목과 비중 범위</h3>
      <div className="flow-table">
        <table style={{ minWidth: 420 }}>
          <thead>
            <tr>
              <th>종목코드</th>
              <th>종목명</th>
              <th className="num">최소 비중</th>
              <th className="num">최대 비중</th>
            </tr>
          </thead>
          <tbody>
            {spec.universe.map((u) => (
              <tr key={u.ticker}>
                <td className="mono">{u.ticker}</td>
                <td>{u.name}</td>
                <td className="num">{pct(u.weight_min)}</td>
                <td className="num">{pct(u.weight_max)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>리밸런싱 방식</h3>
      <RuleList rules={spec.rebalance} />
      <h3>신호 규칙</h3>
      <RuleList rules={spec.signal_rules} />
      <h3>제약</h3>
      <RuleList rules={spec.constraint_user} />
    </>
  );
}

// 전략서 상태 표시. draft 는 아직 고칠 수 있는 상태라 주의색, 승인은 확정색.
export function StatusBadge({ status }: { status: string }) {
  const tone = status === "approved" ? "ok" : status === "draft" ? "warn" : "muted";
  return <span className={`flow-badge ${tone}`}>{status}</span>;
}

// 규칙 칸은 블록마다 모양이 달라서 키: 값 그대로 보여 준다. null 은 쓰지 않는 규칙이다.
function RuleList({ rules }: { rules: Rules }) {
  if (!rules) return <p className="flow-hint">없음</p>;
  return (
    <dl className="flow-kv">
      {Object.entries(rules).map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt className="mono">{k}</dt>
          <dd className={v === null ? "prose" : "mono"} style={{ color: v === null ? "var(--c-muted)" : undefined, wordBreak: "break-all" }}>
            {v === null ? "사용 안 함" : typeof v === "object" ? JSON.stringify(v) : String(v)}
          </dd>
        </div>
      ))}
    </dl>
  );
}
