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
import { PROFILE_LABEL } from "@/lib/policy";

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
        <ul>
          <li>
            성향: {profile.risk_level} {PROFILE_LABEL[profile.risk_level] ?? "(알 수 없는 등급)"}
          </li>
          <li>기준표 버전: {profile.preset_version}</li>
          <li>확정 시각: {profile.created_at}</li>
        </ul>
      ) : (
        <p>{message ?? "불러오는 중…"}</p>
      )}
      <p>
        <Link href="/survey">다시 설문하기</Link>
      </p>
      <p>
        <button onClick={() => router.push("/compile")} disabled={!profile}>
          전략 만들기로
        </button>
      </p>
    </>
  );
}
