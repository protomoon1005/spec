"use client";

// 하드캡 확인 — docs/frontend_milestone.md 5단계.
//
// 화면을 열면 M2 검증기(POST /specs/{spec_id}/validate, docs/m2-validator-api.md)를 돌려
// 4단계(형식 → 참조 → 논리·범위 보정 → 하드캡) 결과를 보여 준다. 전략서 번호는 전략 완료
// 화면이 남긴 것(탭 저장소, lib/api.ts 4절)을 쓴다.
//
// 검증기는 보정한 범위를 전략서에 저장하고(원래 값은 _raw 로 남는다) 여러 번 불러도 결과가
// 같다. 그래서 화면에 들어올 때마다 불러도 된다. 이 화면을 지나면 백테스트는 확정 범위로 돈다.
// 막히는 것은 오류가 아니라 200 에 passed: false 로 온다 — 통과하지 못하면 다음으로 못 간다.
// 결과는 최종 확인 화면이 체크리스트에 쓰도록 탭 저장소에 남긴다(saveValidation).
//
// 리밸런싱 규칙이 백테스트에서 돌 수 있는지는 검증기가 보지 않는다. 그 경고는 전략서 내용
// (components/spec-contents.tsx)에 있어 여기서 되풀이하지 않는다.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, loadCompileResult, saveValidation } from "@/lib/api";

type Violation = { code: string; message: string; ticker: string | null; field: string | null };

type Stage = {
  stage: number;
  name: "schema" | "reference" | "logic" | "hardcap";
  status: "passed" | "failed" | "not_run";
  violations: Violation[];
  adjusted_bounds: { case: "A" | "B" | null; cash_min: number; cash_target: number } | null;
  clamped_fields: { field: string; requested: number; applied: number; limit: string }[] | null;
};

type Validation = {
  spec_id: string;
  hardcap_version: string;
  as_of: string;
  passed: boolean;
  blocked_at: number | null;
  regeneration: { required: boolean };
  cash_target: number | null;
  stages: Stage[];
  universe: {
    ticker: string;
    weight_min_raw: number;
    weight_max_raw: number;
    weight_min: number;
    weight_max: number;
    was_adjusted: boolean;
  }[];
};

const STAGE_LABEL: Record<Stage["name"], string> = {
  schema: "형식",
  reference: "참조",
  logic: "논리·범위",
  hardcap: "하드캡",
};

// 응답 숫자는 0.10000000000000009 처럼 온다(문서 안내). 소수 첫째 자리까지만.
const pct = (v: number) => `${Math.round(v * 1000) / 10}%`;

export default function HardcapPage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>하드캡 확인</h1>
        <p>이 화면에서 하는 일: 전략서가 형식 · 참조 · 논리 · 시스템 상한선(하드캡) 검사를 통과하는지 본다.</p>
        <RequireLogin>{() => <Check />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Check() {
  const router = useRouter();
  const [specId, setSpecId] = useState<string | null | undefined>(undefined);
  const [result, setResult] = useState<Validation | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const r = loadCompileResult();
    setSpecId(r?.status === "completed" ? r.spec_id : null);
  }, []);

  useEffect(() => {
    if (!specId) return;
    api<Validation>(`/specs/${specId}/validate`, { method: "POST" })
      .then((r) => {
        setResult(r);
        saveValidation({ spec_id: r.spec_id, passed: r.passed, blocked_at: r.blocked_at });
      })
      .catch((err) => setMessage(`검사를 돌리지 못했습니다: ${err instanceof Error ? err.message : String(err)}`));
    // 검증 응답에는 종목명이 없다. 보정 · 클램프 줄에 이름을 붙이려고 전략서에서 읽는다.
    api<{ universe: { ticker: string; name: string }[] }>(`/specs/${specId}`)
      .then((s) => setNames(Object.fromEntries(s.universe.map((u) => [u.ticker, u.name]))))
      .catch(() => {});
  }, [specId]);

  if (specId === undefined) return <p className="flow-state">불러오는 중…</p>;
  if (specId === null) {
    return (
      <div className="flow-empty">
        <strong>이번 탭에서 완료한 전략서가 없습니다.</strong>
        <Link href="/compile">전략 요청으로</Link>
      </div>
    );
  }
  if (!result) {
    return message ? (
      <p className="flow-msg err" role="alert">
        {message}
      </p>
    ) : (
      <p className="flow-state">검사하는 중…</p>
    );
  }

  const logic = result.stages.find((s) => s.name === "logic");
  const hardcap = result.stages.find((s) => s.name === "hardcap");
  const clamped = hardcap?.clamped_fields ?? [];
  const adjusted = result.universe.filter((u) => u.was_adjusted);
  const cashRaised =
    logic?.adjusted_bounds && result.cash_target !== null && result.cash_target > logic.adjusted_bounds.cash_min + 1e-9;
  const nameOf = (ticker: string) => names[ticker] ?? ticker;

  return (
    <>
      <p className={result.passed ? "flow-msg ok" : "flow-msg err"} role="status" style={{ marginTop: 0 }}>
        <b style={{ color: "var(--c-bright)" }}>
          {result.passed ? "4단계 검사를 모두 통과했어요" : `${result.blocked_at}단계에서 막혔어요`}
        </b>
        <span className="flow-hint" style={{ marginLeft: 10 }}>
          기준일 {result.as_of} · 하드캡 {result.hardcap_version} · 전략서 <span className="mono">{result.spec_id}</span>
        </span>
      </p>

      <h2>단계별 결과</h2>
      <dl className="flow-kv">
        {result.stages.map((s) => (
          <div key={s.stage} style={{ display: "contents" }}>
            <dt>
              <span className="mono" style={{ marginRight: 8 }}>
                {s.stage}
              </span>
              {STAGE_LABEL[s.name] ?? s.name}
            </dt>
            <dd>
              <span className={`flow-badge ${s.status === "passed" ? "ok" : s.status === "failed" ? "err" : "muted"}`}>
                {s.status === "passed" ? "통과" : s.status === "failed" ? "막힘" : "검사 안 함"}
              </span>
              <span className="flow-hint" style={{ marginLeft: 10 }}>
                {stageDetail(s)}
              </span>
              {s.violations.map((v, i) => (
                <span key={i} style={{ display: "block", marginTop: 6 }}>
                  {v.ticker && <span className="mono flow-hint">{nameOf(v.ticker)} </span>}
                  {v.message}
                </span>
              ))}
            </dd>
          </div>
        ))}
      </dl>

      {result.regeneration.required && (
        <p className="flow-msg warn" role="alert">
          전략서를 다시 만들어야 해요. 이 전략서는 고쳐서 쓸 수 없는 문제가 있습니다.{" "}
          <Link href="/compile?retry=1">다시 요청 — 방금 쓴 문장으로</Link>
        </p>
      )}

      {/* 막히면 범위가 확정된 게 아니다(2·3단에서 막히면 원래 범위가 그대로 온다). */}
      {result.passed && result.universe.length > 0 && (
        <>
          <h2>확정 비중 범위</h2>
          {adjusted.length === 0 ? (
            <p className="flow-hint">AI가 낸 범위 그대로 확정됐어요.</p>
          ) : (
            <Changes
              rows={adjusted.map((u) => ({
                key: u.ticker,
                label: `${nameOf(u.ticker)} (${u.ticker})`,
                before: `${pct(u.weight_min_raw)} ~ ${pct(u.weight_max_raw)}`,
                after: `${pct(u.weight_min)} ~ ${pct(u.weight_max)}`,
              }))}
            />
          )}
          {result.cash_target !== null && (
            <p className="flow-hint" style={{ marginTop: 8 }}>
              현금 목표 {pct(result.cash_target)}
              {cashRaised && " — 상한을 다 늘려도 모자라 현금을 늘렸어요"}
            </p>
          )}
        </>
      )}

      {clamped.length > 0 && (
        <>
          <h2>하드캡으로 바뀐 값</h2>
          <Changes
            rows={clamped.map((c) => ({
              key: c.field,
              label: fieldLabel(c.field, nameOf),
              before: formatValue(c.field, c.requested),
              after: formatValue(c.field, c.applied),
            }))}
          />
        </>
      )}

      <div className="flow-next" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <span className="flow-hint">{result.passed ? "" : "검사를 통과해야 백테스트로 갈 수 있어요."}</span>
        <button className="primary" onClick={() => router.push("/backtest")} disabled={!result.passed}>
          다음 — 백테스트
        </button>
      </div>
    </>
  );
}

// 전후 값 표. 원래 값은 취소선.
function Changes({ rows }: { rows: { key: string; label: string; before: string; after: string }[] }) {
  return (
    <div className="flow-table">
      <table>
        <thead>
          <tr>
            <th>항목</th>
            <th className="num">원래</th>
            <th className="num">적용</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <td>{r.label}</td>
              <td className="num">
                <s>{r.before}</s>
              </td>
              <td className="num" style={{ color: "var(--c-bright)" }}>
                {r.after}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function stageDetail(s: Stage): string {
  if (s.status === "not_run") return "앞 단계에서 막혀 돌지 않았어요";
  if (s.status === "failed") return "";
  if (s.name === "reference") return "종목 원장 · 상장 여부 · 가격 데이터";
  if (s.name === "logic") {
    const c = s.adjusted_bounds?.case;
    return c === "A" ? "하한 합이 넘쳐 줄였어요" : c === "B" ? "상한 합이 모자라 늘렸어요" : "보정 없음";
  }
  if (s.name === "hardcap") {
    const n = s.clamped_fields?.length ?? 0;
    return n ? `${n}개 값을 상한에 맞췄어요` : "깎인 값 없음";
  }
  return "";
}

const CONSTRAINT_LABEL: Record<string, string> = {
  cash_min: "현금 최소",
  max_drawdown: "최대 낙폭",
  max_loss_per_trade: "한 번 손실 최대",
  max_weight_per_asset: "종목당 최대",
  min_weight_per_asset: "종목당 최소",
};

// universe.069500.weight_max · constraint.max_drawdown · rebalance.min_interval_days
function fieldLabel(field: string, nameOf: (t: string) => string): string {
  const [head, a, b] = field.split(".");
  if (head === "universe" && a) return `${nameOf(a)} ${b === "weight_min" ? "최소" : "최대"} 비중`;
  if (head === "constraint" && a) return CONSTRAINT_LABEL[a] ?? a;
  if (field === "rebalance.min_interval_days") return "리밸런싱 최소 간격";
  return field;
}

function formatValue(field: string, v: number): string {
  return field === "rebalance.min_interval_days" ? `${Math.round(v)}일` : pct(v);
}
