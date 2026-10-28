"use client";

// 데모 화면(/samples · /repro) 공통: 샘플 결과 파일 받기, 기준 시점 줄, 파일이 없을 때 안내.

import { useEffect, useState } from "react";

import { DEMO_SAMPLES_URL, fmtStamp, loadDemoSamples, periodLabel, type DemoSamples, type LoadResult } from "@/lib/demo-samples";

export function useDemoSamples(): LoadResult | { kind: "loading" } {
  const [state, setState] = useState<LoadResult | { kind: "loading" }>({ kind: "loading" });
  useEffect(() => {
    let alive = true;
    loadDemoSamples().then((r) => {
      if (alive) setState(r);
    });
    return () => {
      alive = false;
    };
  }, []);
  return state;
}

// 파일이 아직 없거나 읽지 못했을 때. 지어낸 수치를 그리지 않는다.
export function DemoNotReady({ state }: { state: LoadResult | { kind: "loading" } }) {
  if (state.kind === "loading") return <p className="flow-state">샘플 결과 파일을 받는 중…</p>;
  if (state.kind === "missing") {
    return (
      <div className="flow-empty">
        <strong>샘플 결과 파일이 아직 없습니다.</strong>
        샘플 문장을 실제 스택에 돌린 기록(<code>public{DEMO_SAMPLES_URL}</code>)이 만들어지면 여기에 나타납니다.
      </div>
    );
  }
  if (state.kind === "error") {
    return (
      <p className="flow-msg err" role="alert">
        {state.message}
      </p>
    );
  }
  return null;
}

// 모든 수치의 기준: 파일 생성 시각 · 커밋 · 모델 · 백테스트 기간
export function DemoBasis({ data }: { data: DemoSamples }) {
  return (
    <div className="flow-basis" aria-label="기준 시점">
      <span>
        기록 생성 <b>{fmtStamp(data.generated_at)}</b>
      </span>
      <span>
        백테스트 기간 <b>{periodLabel(data.period)}</b>
      </span>
      {data.llm_model && (
        <span>
          컴파일 모델 <b>{data.llm_model}</b>
        </span>
      )}
      {data.git_commit && (
        <span>
          커밋 <b>{data.git_commit.slice(0, 7)}</b>
        </span>
      )}
    </div>
  );
}
