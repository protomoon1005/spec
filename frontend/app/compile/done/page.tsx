"use client";

// 전략서 완료 (전략서 v0.2).
//
// 주소의 strategy 로 GET /v2/strategies/{id} 를 불러 보여 준다. 새로고침해도, 전략 관리에서 들어와도 같다.
//   - "AI가 이해한 전략" 설명: 서버가 정해진 틀에 값을 끼운 것이다(AI 가 쓰지 않는다). 기본값은 표시한다
//   - 검사 1~4단계 결과와 경고
//   - 전략서 원본(JSON)은 접어 둔다
// 맞으면 백테스트로, 다르면 문장을 고쳐 다시 요청한다.

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { CheckList, Explanation, checksFromLogs } from "@/components/v2/outcome-parts";
import { api } from "@/lib/api";
import type { StrategyView } from "@/lib/v2";

export default function DonePage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>전략서 확인</h1>
        <p>이 화면에서 하는 일: AI 가 이해한 전략이 내가 말한 것과 같은지 확인한다.</p>
        <RequireLogin>
          {() => (
            <Suspense fallback={<p className="flow-state">불러오는 중…</p>}>
              <Result />
            </Suspense>
          )}
        </RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Result() {
  const router = useRouter();
  const id = useSearchParams().get("strategy");
  const [view, setView] = useState<StrategyView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    api<StrategyView>(`/v2/strategies/${encodeURIComponent(id)}`)
      .then((v) => !cancelled && setView(v))
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (!id) {
    return (
      <div className="flow-empty">
        <strong>확인할 전략서가 없습니다.</strong>
        <Link href="/compile">전략 요청으로</Link>
      </div>
    );
  }
  if (error) return <p className="flow-msg err">전략서를 불러오지 못했어요: {error}</p>;
  if (!view) return <p className="flow-state">불러오는 중…</p>;

  return (
    <>
      <p className="flow-msg ok" style={{ marginTop: 0, marginBottom: 16 }}>
        전략서를 만들었어요. 초안(draft)으로 저장됐어요.
      </p>
      {view.input_prompt && (
        <p className="flow-msg" style={{ marginTop: 0, marginBottom: 16 }}>
          <span className="flow-hint" style={{ marginRight: 8 }}>
            요청
          </span>
          &ldquo;{view.input_prompt}&rdquo;
        </p>
      )}

      <h2>AI가 이해한 전략</h2>
      <section className="flow-card">
        <p className="flow-hint" style={{ marginTop: 0 }}>
          {view.name} · <span className="mono">{view.strategy_id}</span>
        </p>
        <Explanation lines={view.explanation} />
      </section>

      <h2>검사 결과</h2>
      <section className="flow-card">
        <CheckList checks={checksFromLogs(view.checks)} />
      </section>

      <details className="flow-details">
        <summary>전략서 원본(JSON)</summary>
        <pre className="mono" style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
          {JSON.stringify(view.spec, null, 2)}
        </pre>
      </details>

      <div className="flow-next" style={{ justifyContent: "space-between" }}>
        <Link href="/compile?retry=1">다르게 이해했어요 — 문장 고쳐서 다시 요청</Link>
        <button className="primary" onClick={() => router.push(`/backtest?strategy=${encodeURIComponent(view.strategy_id)}`)}>
          다음 — 백테스트
        </button>
      </div>
    </>
  );
}
