"use client";

// 백테스트 — docs/frontend_milestone.md 6단계, 확정 결정 3·4번.
//
// 백테스트 API 가 아직 501 이라, /report 와 같은 저장된 결과 파일(data/backtest-result.json)을
// lib/data.ts 가 계산한 지표 그대로 숫자로만 보여 준다.

import Link from "next/link";
import { useRouter } from "next/navigation";

import ScaffoldShell from "@/components/scaffold-shell";
import { control, DATA_SOURCE, market, PERIOD_END, PERIOD_START, PROFILE_LABEL, strategy } from "@/lib/data";

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

const ROWS = [
  { label: "전략 (3관점 신호 사용)", m: strategy },
  { label: "대조군 (같은 제약, 신호 없음)", m: control },
  { label: "시장 (KODEX 200 매수 후 보유)", m: market },
];

export default function BacktestPage() {
  const router = useRouter();
  return (
    <ScaffoldShell>
      <main>
        <h1>백테스트</h1>
        <p>이 화면에서 하는 일: 전략을 과거 데이터로 돌려 본 결과를 확인한다.</p>
        <p>방금 만든 전략서가 아니라 미리 저장된 백테스트 결과입니다.</p>
        <p>
          출처: data/backtest-result.json ({DATA_SOURCE} 실제 종가, 성향 {PROFILE_LABEL} 기준)
        </p>

        <ul>
          <li>
            기간: {PERIOD_START} ~ {PERIOD_END}
          </li>
          {ROWS.map(({ label, m }) => (
            <li key={label}>
              {label}: 최종 수익률 {pct(m.total)}, 최대 낙폭 {pct(m.mdd)}
            </li>
          ))}
        </ul>

        <p>
          <Link href="/report">자세한 리포트 보기 (/report)</Link>
        </p>
        <p>
          <button onClick={() => router.push("/confirm")}>다음 — 최종 확인</button>
        </p>
      </main>
    </ScaffoldShell>
  );
}
