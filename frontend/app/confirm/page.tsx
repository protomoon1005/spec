"use client";

// 최종 확인 — docs/frontend_milestone.md 7단계.
//
// 앞 단계 값을 한 화면에 모은다. 백테스트는 백테스트 화면이 이번 전략서로 접수한 실행을
// GET /backtest/runs/{run_id} 로 다시 받아 보여 준다. 승인 주소(POST /portfolios/{id}/approve)는 아직 501 이고,
// 승인할 포트폴리오를 만드는 단계도 없다. 그래서 포트폴리오 번호 자리에 0 을 넣어 부르고
// 서버 응답을 그대로 보여 준다 — 인증은 통과하고 본체에서 501 이 난다.
//
// 앞 단계 넷(성향 · 전략서 · 하드캡 검사 · 백테스트)을 통과 · 막힘 · 아직 으로 모으고, 줄마다
// 그 화면으로 가는 링크를 둔다.
//   전략서      GET /specs/{spec_id} 의 이름과 종목 수
//   하드캡 검사  하드캡 화면이 탭 저장소에 남긴 결과(loadValidation). 검사 API 는 전략서를
//               저장하는 요청이라 여기서 다시 부르지 않는다
//   백테스트    전략 · 대조군 · 신호 효과 한 줄. 리밸런싱 규칙 실패는 풀어 쓴다

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, type BacktestRun, loadBacktestRun, loadCompileResult, loadValidation, type Session } from "@/lib/api";
import { PROFILE_LABEL } from "@/lib/policy";

const PLACEHOLDER_PORTFOLIO_ID = 0;

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
  const [profile, setProfile] = useState<Item>({ label: "성향", ok: null, text: "불러오는 중…" });
  const [spec, setSpec] = useState<Item>({ label: "전략서", ok: null, text: "불러오는 중…" });
  const [hardcap, setHardcap] = useState<Item>({ label: "하드캡 검사", ok: null, text: "불러오는 중…" });
  const [backtest, setBacktest] = useState<Item>({ label: "백테스트", ok: null, text: "불러오는 중…" });
  const [approval, setApproval] = useState<string | null>(null);

  useEffect(() => {
    api<{ risk_level: number }>("/profile/me")
      .then((p) =>
        setProfile({
          label: "성향",
          ok: true,
          text: `${PROFILE_LABEL[p.risk_level] ?? "알 수 없는 등급"} (${p.risk_level} / 5)`,
          link: { href: "/profile", text: "보기" },
        }),
      )
      .catch((err) => setProfile({ label: "성향", ok: false, text: errorText(err), link: { href: "/survey", text: "설문" } }));

    const r = loadCompileResult();
    const id = r?.status === "completed" ? r.spec_id : null;
    if (!id) {
      const none = { ok: null, text: "이번 탭에서 만든 전략서가 없어요" };
      setSpec({ label: "전략서", ...none, link: { href: "/compile", text: "전략 요청" } });
      setHardcap({ label: "하드캡 검사", ...none });
      setBacktest({ label: "백테스트", ...none });
      return;
    }

    api<{ name: string; universe: unknown[] }>(`/specs/${id}`)
      .then((s) =>
        setSpec({
          label: "전략서",
          ok: true,
          text: `${s.name} · 종목 ${s.universe.length}개`,
          link: { href: `/specs/view?id=${encodeURIComponent(id)}`, text: "보기" },
        }),
      )
      .catch((err) => setSpec({ label: "전략서", ok: false, text: errorText(err) }));

    const v = loadValidation(id);
    setHardcap(
      v
        ? {
            label: "하드캡 검사",
            ok: v.passed,
            text: v.passed ? "4단계 모두 통과" : `${v.blocked_at}단계에서 막힘`,
            link: { href: "/hardcap", text: "보기" },
          }
        : { label: "하드캡 검사", ok: null, text: "아직 확인 안 함", link: { href: "/hardcap", text: "하드캡 확인" } },
    );

    const saved = loadBacktestRun(id);
    if (!saved) {
      setBacktest({ label: "백테스트", ok: null, text: "아직 돌리지 않음", link: { href: "/backtest", text: "백테스트" } });
      return;
    }
    api<BacktestRun>(`/backtest/runs/${saved.run_id}`)
      .then((run) => setBacktest(describeRun(run)))
      .catch((err) => setBacktest({ label: "백테스트", ok: false, text: errorText(err) }));
  }, []);

  async function approve() {
    setApproval("요청 중…");
    try {
      const res = await api<unknown>(`/portfolios/${PLACEHOLDER_PORTFOLIO_ID}/approve`, { method: "POST" });
      setApproval(`승인됨: ${JSON.stringify(res)}`);
    } catch (err) {
      setApproval(`승인 기능은 구현 예정입니다. 서버 응답 — ${errorText(err)}`);
    }
  }

  return (
    <>
      <p className="flow-hint" style={{ marginBottom: 10 }}>
        <b className="mono" style={{ color: "var(--c-text)" }}>
          {session.username}
        </b>{" "}
        의 전략서를 승인할 준비가 됐는지 확인해요
      </p>
      <dl className="flow-kv">
        {[profile, spec, hardcap, backtest].map((item) => (
          <div key={item.label} style={{ display: "contents" }}>
            <dt>{item.label}</dt>
            <dd>
              <span className={`flow-badge ${item.ok === true ? "ok" : item.ok === false ? "err" : "muted"}`}>
                {item.ok === true ? "통과" : item.ok === false ? "막힘" : "아직"}
              </span>
              <span style={{ marginLeft: 10 }}>{item.text}</span>
              {item.link && (
                <Link href={item.link.href} style={{ marginLeft: 10, fontSize: 12 }}>
                  {item.link.text} →
                </Link>
              )}
            </dd>
          </div>
        ))}
      </dl>

      <section className="flow-card" style={{ marginTop: 24, borderColor: "#e0a23c55" }}>
        <div className="flow-actions" style={{ justifyContent: "space-between" }}>
          <span>승인하면 전략서를 고칠 수 없습니다.</span>
          <button className="primary" onClick={approve}>
            승인
          </button>
        </div>
        {approval && (
          <p className={approval.startsWith("승인됨") ? "flow-msg ok" : "flow-msg warn"} role="status">
            {approval}
          </p>
        )}
      </section>

      <div className="flow-next">
        <button onClick={() => router.push("/views")}>다음 — 관점 판단</button>
      </div>
    </>
  );
}

// ok: true 통과 · false 막힘 · null 아직 모름(확인 안 함 / 불러오는 중)
type Item = { label: string; ok: boolean | null; text: string; link?: { href: string; text: string } };

const signedPct = (v: number) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
const signedPp = (v: number) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%p`;
const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

function describeRun(run: BacktestRun): Item {
  const base = { label: "백테스트" };
  if (run.status === "failed") {
    const rebalance = (run.reason ?? "").startsWith("러너가 지원하지 않는 리밸런싱 규칙");
    return {
      ...base,
      ok: false,
      text: rebalance ? "리밸런싱 규칙을 아직 돌릴 수 없어요" : `실패 — ${run.reason ?? "이유 없음"}`,
      link: { href: "/compile?retry=1", text: "다시 요청" },
    };
  }
  if (run.status !== "done" || !run.summary) {
    return { ...base, ok: null, text: `아직 도는 중 (${run.status ?? "알 수 없음"})`, link: { href: "/backtest", text: "보기" } };
  }
  const { strategy, control } = run.summary;
  return {
    ...base,
    ok: true,
    text: `전략 ${signedPct(strategy.total)} · 대조군 ${signedPct(control.total)} · 신호 효과 ${signedPp(strategy.total - control.total)}`,
    link: { href: `/report?run_id=${run.run_id}`, text: "리포트" },
  };
}
