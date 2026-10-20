"use client";

// 최종 확인 — docs/frontend_milestone.md 7단계.
//
// 앞 단계 값을 한 화면에 모은다. 백테스트는 백테스트 화면이 이번 전략서로 접수한 실행을
// GET /backtest/runs/{run_id} 로 다시 받아 보여 준다. 승인은 POST /specs/{spec_id}/approve 에
// 그 실행 번호(run_id)를 실어 부른다 — 승인 기록 · 전략서 잠금 · 포트폴리오(가상 운용) · 관점
// 가중치 1/3 이 한 번에 생긴다. 서버가 막으면(검증 미통과 · 옛 실행 · 이미 승인) 이유를 그대로 보여 준다.
// 이미 승인한 전략서는 GET /specs/{spec_id} 의 status 로 알아보고 버튼을 끈다.
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
import {
  api,
  ApiError,
  approveSpec,
  type BacktestRun,
  loadBacktestRun,
  loadCompileResult,
  loadValidation,
  type Session,
} from "@/lib/api";
import { PROFILE_LABEL } from "@/lib/policy";

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
  const [approval, setApproval] = useState<{ ok: boolean; text: string } | null>(null);
  // 승인에 필요한 것 — 이번 탭의 전략서 · 그 전략서로 끝난 실행 · 아직 draft 인지
  const [target, setTarget] = useState<{ specId: string; runId: number | null; draft: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

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

    const saved = loadBacktestRun(id);
    api<{ name: string; status: string; universe: unknown[] }>(`/specs/${id}`)
      .then((s) => {
        setSpec({
          label: "전략서",
          ok: true,
          text: `${s.name} · 종목 ${s.universe.length}개${s.status === "draft" ? "" : ` · ${s.status}`}`,
          link: { href: `/specs/view?id=${encodeURIComponent(id)}`, text: "보기" },
        });
        setTarget({ specId: id, runId: saved?.run_id ?? null, draft: s.status === "draft" });
        if (s.status !== "draft") setApproval({ ok: true, text: "이미 승인한 전략서예요. 고칠 수 없습니다." });
      })
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

    if (!saved) {
      setBacktest({ label: "백테스트", ok: null, text: "아직 돌리지 않음", link: { href: "/backtest", text: "백테스트" } });
      return;
    }
    api<BacktestRun>(`/backtest/runs/${saved.run_id}`)
      .then((run) => setBacktest(describeRun(run)))
      .catch((err) => setBacktest({ label: "백테스트", ok: false, text: errorText(err) }));
  }, []);

  async function approve() {
    if (!target || target.runId === null) return;
    setBusy(true);
    setApproval(null);
    try {
      const p = await approveSpec(target.specId, target.runId);
      setTarget({ ...target, draft: false });
      const cash = p.initial_cash === null ? "" : ` · 시작 금액 ${p.initial_cash.toLocaleString("ko-KR")}원`;
      setApproval({ ok: true, text: `승인했어요 — 포트폴리오 #${p.portfolio_id} (가상 운용${cash})` });
    } catch (err) {
      // 409 · 422 의 detail 은 사용자에게 그대로 보여 줄 문장이다
      const detail = err instanceof ApiError && typeof err.detail === "string" ? err.detail : errorText(err);
      setApproval({ ok: false, text: `승인하지 못했어요 — ${detail}` });
    } finally {
      setBusy(false);
    }
  }

  const blocker = !target
    ? "이번 탭에서 만든 전략서가 없어요"
    : target.runId === null
      ? "백테스트를 먼저 돌려야 해요"
      : null;

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
          <span>{blocker ?? "승인하면 전략서를 고칠 수 없습니다."}</span>
          <button className="primary" onClick={approve} disabled={busy || blocker !== null || !target?.draft}>
            {busy ? "승인 중…" : "승인"}
          </button>
        </div>
        {approval && (
          <p className={approval.ok ? "flow-msg ok" : "flow-msg warn"} role="status">
            {approval.text}
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
