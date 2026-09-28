"use client";

// 최종 확인 — docs/frontend_milestone.md 7단계.
//
// 앞 단계 값을 한 화면에 모은다. 백테스트는 백테스트 화면이 이번 전략서로 접수한 실행을
// GET /backtest/runs/{run_id} 로 다시 받아 보여 준다. 승인 주소(POST /portfolios/{id}/approve)는 아직 501 이고,
// 승인할 포트폴리오를 만드는 단계도 없다. 그래서 포트폴리오 번호 자리에 0 을 넣어 부르고
// 서버 응답을 그대로 보여 준다 — 인증은 통과하고 본체에서 501 이 난다.

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, type BacktestRun, loadBacktestRun, loadCompileResult, type Session } from "@/lib/api";
import { PROFILE_LABEL } from "@/lib/policy";

const PLACEHOLDER_PORTFOLIO_ID = 0;

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

export default function ConfirmPage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>최종 확인</h1>
        <p>이 화면에서 하는 일: 지금까지의 결과를 확인하고 전략서를 승인한다.</p>
        <RequireLogin>{(session) => <Summary session={session} />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Summary({ session }: { session: Session }) {
  const router = useRouter();
  const [profile, setProfile] = useState<string>("불러오는 중…");
  const [specId, setSpecId] = useState<string | null>(null);
  const [approval, setApproval] = useState<string | null>(null);
  const [backtest, setBacktest] = useState<string>("불러오는 중…");

  useEffect(() => {
    const r = loadCompileResult();
    const id = r?.status === "completed" ? r.spec_id : null;
    setSpecId(id);
    const saved = id ? loadBacktestRun(id) : null;
    if (!saved) setBacktest("없음 (이번 전략서로 돌린 백테스트가 없습니다)");
    else
      api<BacktestRun>(`/backtest/runs/${saved.run_id}`)
        .then((run) => setBacktest(describeRun(run)))
        .catch((err) => setBacktest(`받지 못함 — ${err instanceof Error ? err.message : String(err)}`));
    api<{ risk_level: number }>("/profile/me")
      .then((p) => setProfile(`${p.risk_level} ${PROFILE_LABEL[p.risk_level] ?? "(알 수 없는 등급)"}`))
      .catch((err) => setProfile(`받지 못함 — ${err instanceof Error ? err.message : String(err)}`));
  }, []);

  async function approve() {
    setApproval("요청 중…");
    try {
      const res = await api<unknown>(`/portfolios/${PLACEHOLDER_PORTFOLIO_ID}/approve`, { method: "POST" });
      setApproval(`승인됨: ${JSON.stringify(res)}`);
    } catch (err) {
      setApproval(`승인 기능은 구현 예정입니다. 서버 응답 — ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  return (
    <>
      <ul>
        <li>아이디: {session.username}</li>
        <li>성향: {profile}</li>
        <li>전략서 번호: {specId ?? "없음 (이번 탭에서 완료한 전략서가 없습니다)"}</li>
        <li>백테스트: {backtest}</li>
      </ul>

      <p>승인하면 전략서를 고칠 수 없습니다.</p>
      <p>
        <button onClick={approve}>승인</button>
      </p>
      {approval && <p>{approval}</p>}

      <p>
        <button onClick={() => router.push("/views")}>다음 — 관점 판단</button>
      </p>
    </>
  );
}

function describeRun(run: BacktestRun): string {
  const head = `실행 번호 ${run.run_id}, 상태 ${run.status}`;
  if (run.status === "failed") return `${head} — 실패 이유: ${run.reason}`;
  if (run.status !== "done" || !run.summary) return head;
  const { strategy, market } = run.summary;
  return (
    `${head} (${run.period_start} ~ ${run.data_snapshot_asof}, 목업 신호): ` +
    `전략 수익률 ${pct(strategy.total)}, 최대 낙폭 ${pct(strategy.mdd)} / ` +
    `시장 수익률 ${pct(market.total)}, 최대 낙폭 ${pct(market.mdd)} / 서버 지표 ${JSON.stringify(run.metrics)}`
  );
}
