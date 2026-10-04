"use client";

// 투자 성향 결과 — docs/frontend_milestone.md 3단계.
//
// 서버의 "내 성향 보기"를 매번 부른다. 새로고침해도 같은 값이 나오고, 판정이
// 여러 번이면 서버가 가장 최근 것을 준다.
//
// 성향 이름과 5단계 중 위치, 그리고 "이 성향이면 무엇이 제한되는가" 를 보여 준다.
// 한도 값은 서버가 주지 않으므로 lib/policy.ts(정책 값 단일 출처)에서 읽는다 —
// 전략 만들기·백테스트가 실제로 거는 한도와 같은 값이다. 설문 점수는 서버 응답에
// 없어서(RiskProfileResponse 는 확정 컬럼만) 보여 주지 않는다.

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
      {profile ? <ProfileResult profile={profile} /> : <p>{message ?? "불러오는 중…"}</p>}
      <div className="flow-actions">
        <button onClick={() => router.push("/compile")} disabled={!profile}>
          전략 만들기
        </button>
        <Link href="/survey">다시 설문하기</Link>
      </div>
      {profile && (
        <p className="flow-footnote">
          {formatTime(profile.created_at)} 확정 · 기준표 {profile.preset_version}
        </p>
      )}
    </>
  );
}

const LEVELS = [1, 2, 3, 4, 5];

function ProfileResult({ profile }: { profile: RiskProfile }) {
  const level = profile.risk_level;
  const label = PROFILE_LABEL[level];
  if (!label) return <p>알 수 없는 등급입니다: {level}</p>;

  const limits = [
    { name: "주식", kind: "최대", value: ASSET_CAP.EQUITY[level] },
    { name: "채권", kind: "최대", value: ASSET_CAP.BOND[level] },
    { name: "원자재", kind: "최대", value: ASSET_CAP.COMMODITY[level] },
    { name: "현금", kind: "최소", value: CASH_MIN[level] },
  ];

  return (
    <section className="flow-result">
      <p className="flow-result-kicker">당신의 투자 성향은</p>
      <div className="flow-result-head">
        <p className="flow-result-label">{label}</p>
        <span className="flow-scale" role="img" aria-label={`5단계 중 ${level}단계`}>
          {LEVELS.map((i) => (
            <span key={i} className={i <= level ? "on" : undefined} />
          ))}
          <span className="flow-scale-num">{level} / 5</span>
        </span>
      </div>

      <p className="flow-result-kicker">이 성향에서는</p>
      <div className="flow-limits">
        {limits.map((l) => (
          <div key={l.name}>
            <span className="flow-limit-name">
              {l.name} {l.kind}
            </span>
            <span className="flow-limit-value">{Math.round(l.value * 100)}%</span>
          </div>
        ))}
      </div>
    </section>
  );
}

// 2026-10-05T01:23:45.123+00:00 → 2026.10.05 10:23 (보는 사람의 현지 시각)
function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}.${p(d.getMonth() + 1)}.${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
