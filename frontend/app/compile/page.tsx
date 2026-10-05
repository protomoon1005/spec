"use client";

// 전략 요청 — docs/frontend_milestone.md 4단계.
//
// 문장을 보내면 서버가 작업 번호만 바로 주고, 실제 컴파일은 뒤에서 돈다.
// 결과는 진행 상황 화면이 스트림으로 받는다.
//
// 무엇을 쓰면 되는지 화면에서 알 수 있게 세 가지를 둔다.
//   1) 내 성향과 한도 — 문장에 "공격적으로" 라고 써도 성향 한도는 바뀌지 않는다
//      (docs/human_manual.md 2.2). 한도 값은 lib/policy.ts 에서 읽는다.
//   2) 예시 문장 — 누르면 입력칸에 채워진다(lib/compile-hints.ts).
//   3) 알아듣는 말 — 서버는 정해진 목록(BBL kw: 태그 · 업종 · 자산군) 안에서만 뽑는다.
//      업종은 lib/policy.ts 의 SECTOR_GROUPS 를 쓰고, 성격 키워드는 42개 중 사용자가
//      쓸 법한 것만 골라 여기 적는다. 서버 목록이 바뀌면 이 목록도 손으로 맞춘다.
//
// 리밸런싱은 "매주" · "매달 초" 만 보여 준다. 백테스트 러너가 돌리는 리밸런싱 블록이
// RB_WEEKLY · RB_MONTHLY_FIRST 둘뿐이라, 분기 · 비중유지 · 신호 같은 말을 쓰면 전략서는
// 만들어져도 백테스트가 실패한다. 러너가 늘면 여기도 늘린다.
// 그런데 이 둘도 보장되지 않는다. 전략서 양식이 trigger 의 day · min_interval_days 를
// 블록 값으로 묶지 않아서 AI 가 다른 값을 채울 수 있다(2026-10-05 실측: "매주" →
// day null · 간격 7일 → 러너 거부). 그래서 안내 문구를 "실패할 수 있다" 로 둔다.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, ApiError, loadCompileRequest, saveCompileRequest } from "@/lib/api";
import { COMPILE_EXAMPLES } from "@/lib/compile-hints";
import { ASSET_CAP, CASH_MIN, GROUP_LABEL, PROFILE_LABEL, SECTOR_GROUPS } from "@/lib/policy";

const VOCAB: { name: string; words: string[] }[] = [
  { name: "업종", words: SECTOR_GROUPS.map((g) => GROUP_LABEL[g]) },
  { name: "자산", words: ["주식", "채권", "원자재"] },
  { name: "ETF 성격", words: ["배당", "월배당", "국채", "시장대표", "환헤지", "액티브"] },
  { name: "투자 태도", words: ["안전하게", "보수적", "공격적"] },
  { name: "리밸런싱", words: ["매주", "매달 초"] },
  { name: "종목", words: ["ETF 이름을 그대로 (예: KODEX 200)"] },
];

export default function CompilePage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>전략 요청</h1>
        <p>이 화면에서 하는 일: 원하는 투자 방식을 문장으로 적어 전략서 생성을 요청한다.</p>
        <RequireLogin>{() => <Request />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Request() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [level, setLevel] = useState<number | null | undefined>(undefined); // undefined: 확인 중, null: 성향 없음
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 진행 상황 화면에서 실패해 "다시 요청" 으로 돌아온 경우(?retry=1) 방금 쓴 문장을 되채운다.
  // useSearchParams 대신 window.location 을 읽는다: 앞의 것은 Suspense 경계가 필요하다.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("retry") !== "1") return;
    const last = loadCompileRequest();
    if (last) setText(last);
  }, []);

  useEffect(() => {
    api<{ risk_level: number }>("/profile/me")
      .then((p) => setLevel(p.risk_level))
      .catch((err) => setLevel(err instanceof ApiError && err.status === 404 ? null : undefined));
  }, []);

  async function send() {
    if (busy || !text.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      const { job_id } = await api<{ job_id: string }>("/specs/compile", { body: { input_prompt: text.trim() } });
      saveCompileRequest(text.trim());
      router.push(`/compile/progress?job=${encodeURIComponent(job_id)}`);
    } catch (err) {
      setMessage(`요청 실패: ${err instanceof Error ? err.message : String(err)}`);
      setBusy(false);
    }
  }

  return (
    <>
      <ProfileLine level={level} />

      <section className="flow-card">
        <label>
          원하는 투자 방식
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                send();
              }
            }}
            rows={4}
            cols={60}
            placeholder="예: 안전하게 채권 위주로 굴리고 매주 비중을 맞춰줘"
            disabled={busy}
            style={{ fontSize: 16 }}
          />
        </label>
        <div className="flow-examples">
          <span className="flow-hint">예시로 시작</span>
          {COMPILE_EXAMPLES.map((ex) => (
            <button key={ex} className="flow-chip" onClick={() => setText(ex)} disabled={busy}>
              {ex}
            </button>
          ))}
        </div>
        <div className="flow-next" style={{ justifyContent: "space-between", alignItems: "center", marginTop: 16 }}>
          <span className="flow-hint">보통 2분 넘게 걸려요 · Ctrl+Enter로 보내기</span>
          <button className="primary" onClick={send} disabled={busy || !text.trim() || level === null}>
            {busy ? "요청 중…" : "전략서 만들기"}
          </button>
        </div>
        {message && (
          <p className="flow-msg err" role="alert">
            {message}
          </p>
        )}
      </section>

      <h2>이런 말을 알아들어요</h2>
      <dl className="flow-kv">
        {VOCAB.map((v) => (
          <div key={v.name} style={{ display: "contents" }}>
            <dt>{v.name}</dt>
            <dd>{v.words.join(" · ")}</dd>
          </div>
        ))}
      </dl>
      <p className="flow-hint" style={{ marginTop: 10 }}>
        리밸런싱은 지금 매주 · 매달 초만 백테스트할 수 있어요. 분기 · 비중 이탈 · 신호 기준은 전략서는 만들어지지만
        백테스트가 실패합니다. 매주 · 매달 초도 아직은 실패할 때가 있어요.
      </p>
    </>
  );
}

// 이 전략서가 어느 성향 기준으로 만들어지는지. 성향이 없으면 서버가 요청을 거절하므로
// 설문으로 안내하고 보내기를 막는다.
function ProfileLine({ level }: { level: number | null | undefined }) {
  if (level === undefined) return null;
  if (level === null || !PROFILE_LABEL[level]) {
    return (
      <p className="flow-msg warn" role="alert" style={{ marginTop: 0, marginBottom: 16 }}>
        아직 성향이 없어 전략을 만들 수 없어요. 먼저 <Link href="/survey">성향 설문</Link>을 마치세요.
      </p>
    );
  }
  return (
    <p className="flow-msg" style={{ marginTop: 0, marginBottom: 16 }}>
      <b style={{ color: "var(--c-bright)" }}>{PROFILE_LABEL[level]}</b> 기준으로 만들어집니다 · 주식 최대{" "}
      {Math.round(ASSET_CAP.EQUITY[level] * 100)}% · 현금 최소 {Math.round(CASH_MIN[level] * 100)}% ·{" "}
      <Link href="/bounds">담을 수 있는 종목 보기</Link> · <Link href="/survey">성향 바꾸기</Link>
      <br />
      <span className="flow-hint" style={{ fontSize: 12 }}>
        문장에 &ldquo;공격적으로&rdquo;라고 써도 성향 한도는 바뀌지 않습니다.
      </span>
    </p>
  );
}
