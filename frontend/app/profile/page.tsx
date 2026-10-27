"use client";

// 투자 성향 결과 — docs/frontend_milestone.md 3단계.
//
// 서버의 "내 성향 보기"를 매번 부른다. 새로고침해도 같은 값이 나오고, 판정이
// 여러 번이면 서버가 가장 최근 것을 준다.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, ApiError } from "@/lib/api";
import { ASSET_CAP, CASH_MIN, PROFILE_LABEL } from "@/lib/policy";

type RiskProfile = {
  profile_id: number;
  user_id: number;
  risk_level: number;
  preset_version: string;
  created_at: string;
};

export default function ProfilePage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>투자 성향 결과</h1>
        <p>이 화면에서 하는 일: 확정된 투자 성향을 확인한다.</p>
        <RequireLogin>{() => <Result />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Result() {
  const router = useRouter();
  const [profile, setProfile] = useState<RiskProfile | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api<RiskProfile>("/profile/me")
      .then(setProfile)
      .catch((err) =>
        setMessage(
          err instanceof ApiError && err.status === 404
            ? `아직 확정된 성향이 없습니다. (${err.message})`
            : `성향을 받지 못했습니다: ${err instanceof Error ? err.message : String(err)}`,
        ),
      );
  }, []);

  return (
    <>
      {profile ? (
        <section className="flow-card">
          <div style={{ display: "flex", alignItems: "baseline", gap: 14, marginBottom: 16, flexWrap: "wrap" }}>
            <span className="mono" style={{ fontSize: 40, fontWeight: 600, color: "var(--c-warn)", lineHeight: 1 }}>
              {profile.risk_level}
            </span>
            <span style={{ fontSize: 20, fontWeight: 600, color: "var(--c-bright)" }}>
              {PROFILE_LABEL[profile.risk_level] ?? "(알 수 없는 등급)"}
            </span>
            <span className="flow-hint">
              1 {PROFILE_LABEL[1]} ~ 5 {PROFILE_LABEL[5]}
            </span>
          </div>
          <Limits level={profile.risk_level} />
          <dl className="flow-kv">
            <dt>기준표 버전</dt>
            <dd>{profile.preset_version}</dd>
            <dt>확정 시각</dt>
            <dd>{formatTime(profile.created_at)}</dd>
          </dl>
        </section>
      ) : message ? (
        <p className="flow-msg warn" role="alert">
          {message}
        </p>
      ) : (
        <p className="flow-state">불러오는 중…</p>
      )}
      <div className="flow-next" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ display: "flex", gap: 16 }}>
          <Link href="/survey">다시 설문하기</Link>
          {profile && <Link href="/bounds">종목별 허용범위 보기</Link>}
        </span>
        <button className="primary" onClick={() => router.push("/compile")} disabled={!profile}>
          전략 만들기로
        </button>
      </div>
    </>
  );
}

// 이 성향이면 무엇이 제한되는가. 서버는 한도를 주지 않으므로 lib/policy.ts(정책 값 단일
// 출처)에서 읽는다 — 전략 만들기 · 백테스트가 실제로 거는 한도와 같은 값이다.
function Limits({ level }: { level: number }) {
  if (!PROFILE_LABEL[level]) return null;
  const limits = [
    { name: "주식 최대", value: ASSET_CAP.EQUITY[level] },
    { name: "채권 최대", value: ASSET_CAP.BOND[level] },
    { name: "원자재 최대", value: ASSET_CAP.COMMODITY[level] },
    { name: "현금 최소", value: CASH_MIN[level] },
  ];
  return (
    <>
      <p className="flow-hint" style={{ marginBottom: 8 }}>
        이 성향에서는
      </p>
      <div className="flow-limits">
        {limits.map((l) => (
          <div key={l.name}>
            <span>{l.name}</span>
            <b className="mono">{Math.round(l.value * 100)}%</b>
          </div>
        ))}
      </div>
    </>
  );
}

// 2026-10-05T01:23:45.123+00:00 → 2026.10.05 10:23 (보는 사람의 현지 시각)
function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}.${p(d.getMonth() + 1)}.${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
