// 관점 판단 — docs/frontend_milestone.md 8단계. 결과를 주는 주소가 없어 설명만 한다.
// 각 관점의 현재 상태는 docs/human_manual.md 2.5 기준.

import Link from "next/link";

import ScaffoldShell from "@/components/scaffold-shell";

export default function ViewsPage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>관점 판단</h1>
        <p>이 화면에서 하는 일: 세 관점의 점수와 이를 합친 매매 신호를 확인한다.</p>

        <p>
          판단 시점마다 종목별로 세 관점이 점수를 내고, 이를 관점별 가중치로 합쳐 -1 ~ +1 사이 신호 하나로
          만든다. 신호가 크면 더 담고, 작으면 덜 담는다.
        </p>

        <h2>세 관점의 현재 상태</h2>
        <ul>
          <li>시장분석 (가격 지표 9개): 지표 계산은 완성, 점수 모델이 없어 고정된 가짜 점수를 쓴다</li>
          <li>뉴스 감성 (업종별 뉴스 긍정/부정): 뉴스 수집이 없어 항상 판단 불가(중립)를 낸다</li>
          <li>시장온도 (변동성 지수, 신용 스프레드, 환율, 지수 추세): 판정 규칙은 완성, 판단 흐름에는 가짜 점수로 연결돼 있다</li>
        </ul>

        <h2>통합 신호</h2>
        <ul>
          <li>세 관점 점수를 관점별 가중치로 합친다. 처음엔 1/3씩, 최근 60거래일 동안 잘 맞은 관점의 비중을 높인다</li>
          <li>어떤 관점도 10% 밑으로는 내려가지 않는다</li>
          <li>절댓값 0.1 미만의 작은 신호는 0으로 본다</li>
        </ul>

        <p>관점 판단 결과 조회는 구현 예정입니다.</p>

        <p>
          <Link href="/compile">처음으로 — 전략 요청</Link>
        </p>
      </main>
    </ScaffoldShell>
  );
}
