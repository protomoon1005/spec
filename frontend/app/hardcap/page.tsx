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
//
// 리밸런싱 규칙이 백테스트에서 돌 수 있는지는 검증기가 보지 않는다. 그 경고는 전략 완료
// 화면(components/spec-contents.tsx)에 있어 여기서 되풀이하지 않는다.

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

  if (specId === undefined) return <p>불러오는 중…</p>;
  if (specId === null) {
    return (
      <p>
        이번 탭에서 완료한 전략서가 없습니다. <Link href="/compile">전략 요청으로</Link>
      </p>
    );
  }
  if (!result) return <p>{message ?? "검사하는 중…"}</p>;

  const logic = result.stages.find((s) => s.name === "logic");
  const hardcap = result.stages.find((s) => s.name === "hardcap");
  const clamped = hardcap?.clamped_fields ?? [];
  const adjusted = result.universe.filter((u) => u.was_adjusted);
  const cashRaised =
    logic?.adjusted_bounds && result.cash_target !== null && result.cash_target > logic.adjusted_bounds.cash_min + 1e-9;
  const nameOf = (ticker: string) => names[ticker] ?? ticker;

  return (
    <>
      <div className="flow-run-head">
        <p className={`flow-run-title ${result.passed ? "flow-done-title" : "flow-fail-title"}`}>
          <span aria-hidden="true">{result.passed ? "✓" : "✕"}</span>{" "}
          {result.passed ? "4단계 검사를 모두 통과했어요" : `${result.blocked_at}단계에서 막혔어요`}
        </p>
        <span className="flow-hint">
          기준일 {result.as_of} · 하드캡 {result.hardcap_version}
        </span>
      </div>

      <div className="flow-checks">
        {result.stages.map((s) => (
          <div key={s.stage} className={`flow-check flow-check-${s.status}`}>
            <span className="flow-steps-num">{s.stage}</span>
            <span className="flow-check-name">{STAGE_LABEL[s.name] ?? s.name}</span>
            <span className="flow-check-status">
              {s.status === "passed" ? "✓ 통과" : s.status === "failed" ? "✕ 막힘" : "검사 안 함"}
            </span>
            <span className="flow-check-detail">{stageDetail(s)}</span>
            {s.violations.length > 0 && (
              <ul className="flow-violations">
                {s.violations.map((v, i) => (
                  <li key={i}>
                    {v.ticker && <span className="flow-choice-code">{nameOf(v.ticker)}</span>} {v.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>

      {result.regeneration.required && (
        <div className="flow-alert" role="alert">
          <p>전략서를 다시 만들어야 해요. 이 전략서는 고쳐서 쓸 수 없는 문제가 있습니다.</p>
          <Link href="/compile?retry=1">다시 요청 — 방금 쓴 문장으로</Link>
        </div>
      )}

      {/* 막히면 범위가 확정된 게 아니다(2·3단에서 막히면 원래 범위가 그대로 온다). */}
      {result.passed && result.universe.length > 0 && (
        <section className="flow-confirmed">
          <p className="flow-section-title">확정 비중 범위</p>
          {adjusted.length === 0 ? (
            <p className="flow-confirmed-note">AI가 낸 범위 그대로 확정됐어요.</p>
          ) : (
            <div className="flow-changes">
              {adjusted.map((u) => (
                <div key={u.ticker}>
                  <span>
                    {nameOf(u.ticker)} <span className="flow-choice-code">{u.ticker}</span>
                  </span>
                  <span className="flow-change">
                    <s>
                      {pct(u.weight_min_raw)} ~ {pct(u.weight_max_raw)}
                    </s>{" "}
                    → {pct(u.weight_min)} ~ {pct(u.weight_max)}
                  </span>
                </div>
              ))}
            </div>
          )}
          {result.cash_target !== null && (
            <p className="flow-confirmed-note">
              현금 목표 {pct(result.cash_target)}
              {cashRaised && " — 상한을 다 늘려도 모자라 현금을 늘렸어요"}
            </p>
          )}
        </section>
      )}

      {clamped.length > 0 && (
        <section className="flow-confirmed">
          <p className="flow-section-title">하드캡으로 바뀐 값</p>
          <div className="flow-changes">
            {clamped.map((c) => (
              <div key={c.field}>
                <span>{fieldLabel(c.field, nameOf)}</span>
                <span className="flow-change">
                  <s>{formatValue(c.field, c.requested)}</s> → {formatValue(c.field, c.applied)}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="flow-actions">
        <button onClick={() => router.push("/backtest")} disabled={!result.passed}>
          다음 — 백테스트
        </button>
        {!result.passed && <span className="flow-hint">검사를 통과해야 백테스트로 갈 수 있어요.</span>}
      </div>
    </>
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
