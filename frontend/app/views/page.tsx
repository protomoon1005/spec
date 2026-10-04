"use client";

// 관점 판단 — docs/frontend_milestone.md 8단계.
//
// 관점 판단 결과만 주는 주소는 없다. 대신 백테스트 실행(GET /backtest/runs/{run_id})에
// 리밸런싱 때마다의 판단 기록(decisions)이 있어 그중 마지막 판단을 보여 준다.
//   종목별 통합 신호(-1 ~ +1) · 목표 비중 · 현금 · 그룹 상한 적용
// 관점별 점수와 관점 가중치는 실행 결과에 저장되지 않아 보여 줄 수 없다 — 그러려면
// 러너(M4)가 판단할 때 BacktestJudge 의 관점 점수 · 가중치도 남겨야 한다.
//
// 세 관점의 상태(시험용 점수 · 중립 · 실제 모델)는 실행의 scorer_sources 로 표시한다.
// 실물 스코어러가 붙으면 저절로 바뀐다. 실행이 없으면 CURRENT_SOURCES(bridge.py 사본). 이번 탭에 성공한 백테스트가 없으면 관점 설명만 둔다.
// 관점 설명은 docs/human_manual.md 2.5 기준.

import Link from "next/link";
import { useEffect, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, type BacktestRun, loadBacktestRun, loadCompileResult } from "@/lib/api";
import type { Decision, UniverseItem } from "@/lib/data";
import { GROUP_LABEL } from "@/lib/policy";

type RunWithDecisions = BacktestRun & { universe: UniverseItem[] | null; decisions: Decision[] | null };

// scorer_sources 의 키 → 화면 이름과 설명
const VIEWS = [
  { key: "market", name: "시장분석", what: "가격 지표 9개" },
  { key: "sentiment", name: "뉴스 감성", what: "업종별 뉴스 긍정 · 부정" },
  { key: "regime", name: "시장온도", what: "변동성 지수 · 신용 스프레드 · 환율 · 지수 추세" },
] as const;

const SOURCE_LABEL: Record<string, string> = { mock: "시험용 점수", neutral: "중립 고정", real: "실제 모델" };

// 성공한 실행이 없을 때 쓰는 지금 설정. backend/app/views/bridge.py 의 SCORERS 와 같아야 한다.
// 실행이 있으면 그 실행의 scorer_sources 를 쓴다.
const CURRENT_SOURCES: Record<string, string> = { market: "mock", sentiment: "neutral", regime: "mock" };

const pct = (v: number) => `${Math.round(v * 1000) / 10}%`;
const signed = (v: number) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}`;

export default function ViewsPage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>관점 판단</h1>
        <p>이 화면에서 하는 일: 세 관점의 점수와 이를 합친 매매 신호를 확인한다.</p>
        <RequireLogin>{() => <Views />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Views() {
  const [run, setRun] = useState<RunWithDecisions | null | undefined>(undefined);

  useEffect(() => {
    const r = loadCompileResult();
    const saved = r?.status === "completed" ? loadBacktestRun(r.spec_id) : null;
    if (!saved) {
      setRun(null);
      return;
    }
    api<RunWithDecisions>(`/backtest/runs/${saved.run_id}`)
      .then(setRun)
      .catch(() => setRun(null));
  }, []);

  const sources = run?.scorer_sources ?? CURRENT_SOURCES;
  const last = run?.status === "done" ? run.decisions?.at(-1) : undefined;

  return (
    <>
      <p className="flow-run-title">세 관점이 낸 점수를 하나의 신호로 합쳐 종목 비중을 정해요</p>

      <div className="flow-views">
        {VIEWS.map((v) => {
          const source = sources[v.key];
          return (
            <div key={v.key}>
              <span className="flow-series-label">{v.name}</span>
              <span className="flow-series-sub">{v.what}</span>
              {source && <span className={`flow-source flow-source-${source}`}>{SOURCE_LABEL[source] ?? source}</span>}
            </div>
          );
        })}
      </div>

      <p className="flow-merge">
        <span>어떻게 합치나</span> 처음엔 1/3씩 → 최근 60거래일 동안 잘 맞은 관점의 비중을 높여요 · 어떤 관점도 10%
        밑으로는 안 내려가요 · 절댓값 0.1 미만 신호는 0으로 봐요
      </p>

      {run === undefined ? (
        <p>불러오는 중…</p>
      ) : last && run ? (
        <LastDecision decision={last} run={run} />
      ) : (
        <p className="flow-note">
          이번 탭에서 성공한 백테스트가 없어요. 백테스트가 성공하면 마지막 판단이 여기 나와요.{" "}
          <Link href="/backtest">백테스트 →</Link>
        </p>
      )}

      <div className="flow-actions">
        <Link className="flow-button" href="/compile">
          처음으로 — 전략 요청
        </Link>
      </div>
    </>
  );
}

function LastDecision({ decision, run }: { decision: Decision; run: RunWithDecisions }) {
  const names = Object.fromEntries((run.universe ?? []).map((u) => [u.ticker, u.name]));
  const tickers = Object.keys(decision.signals).sort((a, b) => decision.signals[b] - decision.signals[a]);

  return (
    <section className="flow-decision">
      <div className="flow-run-head">
        <p className="flow-section-title">마지막 판단</p>
        <span className="flow-hint">
          {decision.date} · 실행 {run.run_id}
        </span>
      </div>

      <div className="flow-signals">
        <div className="flow-signals-head" aria-hidden="true">
          <span />
          <span>
            <span>덜 담기 −1</span>
            <span>0</span>
            <span>+1 더 담기</span>
          </span>
          <span>신호</span>
          <span>목표 비중</span>
        </div>
        {tickers.map((t) => {
          const s = decision.signals[t];
          return (
            <div key={t}>
              <span className="flow-holding-name">
                {names[t] ?? t} <span className="flow-choice-code">{t}</span>
              </span>
              <span className="flow-signal-bar" aria-hidden="true">
                <span
                  className={s >= 0 ? "up" : "down"}
                  style={s >= 0 ? { left: "50%", width: `${s * 50}%` } : { right: "50%", width: `${-s * 50}%` }}
                />
              </span>
              <span className={`flow-signal-value ${s >= 0 ? "up" : "down"}`}>{signed(s)}</span>
              <span className="flow-signal-value">{pct(decision.target[t] ?? 0)}</span>
            </div>
          );
        })}
      </div>

      <p className="flow-confirmed-note">현금 {pct(decision.cash)}</p>
      {decision.capApplications.length > 0 && (
        <p className="flow-confirmed-note">
          그룹 상한:{" "}
          {decision.capApplications
            .map((c) => `${GROUP_LABEL[c.groupId] ?? c.groupId} ${pct(c.cap)}에 걸려 ×${c.factor.toFixed(2)}`)
            .join(" · ")}
        </p>
      )}
      <p className="flow-note">
        관점별 점수와 관점 가중치는 아직 실행 결과에 저장되지 않아 합친 신호만 보여 줘요.{" "}
        <Link href={`/report?run_id=${run.run_id}`}>자세한 판단 기록은 리포트에서 →</Link>
      </p>
    </section>
  );
}
