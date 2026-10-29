"use client";

// 샘플 결과 (데모 시연 4번 "나머지 샘플 결과 테이블").
//
// 샘플 문장을 실제 스택(컴파일 → 검증 → 백테스트)에 차례로 넣어 본 기록 public/data/demo-samples.json 을
// 그대로 보여 준다. 정적 파일이라 로그인 없이 볼 수 있다. 파일이 없으면 빈 상태를 안내한다.
// 실행 결과 링크(/report?run_id=)는 그 실행을 돌린 계정으로 로그인해야 열린다. 샘플은 성향 N 마다
// 데모 계정 demo-pN 으로 돌렸으므로(데모 운영 결정) 링크 옆에 그 계정을 적는다.
//
// 모든 수치 옆에 기준(기록 생성 시각 · 백테스트 기간)을 적는다 — 성공 기준 항목이다.

import Link from "next/link";
import { Fragment, useState } from "react";

import { DemoBasis, DemoNotReady, useDemoSamples } from "@/components/demo-samples";
import ExcludedList from "@/components/excluded-list";
import { readExcluded } from "@/lib/excluded";
import ScaffoldShell from "@/components/scaffold-shell";
import {
  COMPILE_STATUS,
  fmtAdjustment,
  fmtAny,
  fmtNum,
  fmtPct,
  fmtRebalance,
  fmtSec,
  fmtStamp,
  latencyVerdict,
  periodLabel,
  RESULT_BADGE,
  SUCCESS,
  summarize,
  universeSummary,
  type DemoCase,
  type DemoSamples,
} from "@/lib/demo-samples";

export default function SamplesPage() {
  const state = useDemoSamples();
  return (
    <ScaffoldShell wide>
      <main>
        <h1>샘플 결과</h1>
        <p>이 화면에서 하는 일: 샘플 문장 전부를 전략서 만들기부터 백테스트까지 돌린 결과를 한 표로 보고, 성공 기준을 판정한다.</p>
        {state.kind === "ok" ? <Body data={state.data} /> : <DemoNotReady state={state} />}
        <div className="flow-next" style={{ justifyContent: "flex-start" }}>
          <Link href="/repro">재현성 비교로</Link>
          <Link href="/home">홈으로</Link>
        </div>
      </main>
    </ScaffoldShell>
  );
}

function Body({ data }: { data: DemoSamples }) {
  const cases = data.cases ?? [];
  const s = summarize(cases);
  const lat = latencyVerdict(data.latency);
  const stamp = fmtStamp(data.generated_at);

  return (
    <>
      <DemoBasis data={data} />

      <div className="flow-tiles">
        <div>
          <span>전체</span>
          <b>{s.n}건</b>
          <small>{stamp} 기록</small>
        </div>
        <div>
          <span>통과</span>
          <b className="up">{s.pass}건</b>
          <small>목표 {SUCCESS.pass}건 이상</small>
        </div>
        <div>
          <span>의도된 차단</span>
          <b style={{ color: "var(--c-warn)" }}>{s.blocked}건</b>
          <small>목표 {SUCCESS.blocked}건 이상</small>
        </div>
        <div>
          <span>실패</span>
          <b className={s.fail ? "down" : undefined}>{s.fail}건</b>
          <small>{s.unknown ? `결과 미기록 ${s.unknown}건` : "목표 0건"}</small>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12, marginTop: 12 }}>
        <section className={`flow-card ${s.met === true ? "ok" : s.met === false ? "err" : ""}`} style={{ margin: 0 }}>
          <span className="flow-hint">성공 기준 — 정상 {SUCCESS.pass}건 완주 + 의도된 차단 {SUCCESS.blocked}건, 실패 0건</span>
          <div className={`flow-verdict ${s.met === true ? "ok" : s.met === false ? "err" : "muted"}`} style={{ marginTop: 6 }}>
            <strong>{s.met === true ? "충족" : s.met === false ? "미충족" : "판정 보류"}</strong>
            <span className="flow-hint">
              {s.met === null
                ? `결과 칸이 빈 건이 ${s.unknown}건 있어 판정하지 않습니다`
                : `통과 ${s.pass} · 차단 ${s.blocked} · 실패 ${s.fail} (${stamp} 기록)`}
            </span>
          </div>
        </section>
        <section className={`flow-card ${lat ? (lat.faster ? "ok" : "err") : ""}`} style={{ margin: 0 }}>
          <span className="flow-hint">
            컴파일 응답시간 중앙값{data.latency?.n != null ? ` (${data.latency.n}건)` : ""} vs 기준{" "}
            {data.latency?.baseline_seconds ?? "–"}초
            {data.latency?.baseline_date ? ` (${data.latency.baseline_date} 측정)` : ""}
          </span>
          {lat ? (
            <div className={`flow-verdict ${lat.faster ? "ok" : "err"}`} style={{ marginTop: 6 }}>
              <strong className="mono" style={{ fontSize: 22 }}>
                {fmtSec(lat.median)}
              </strong>
              <span className="flow-hint">
                기준의 {(lat.ratio * 100).toFixed(0)}% — {lat.faster ? "기준 이내" : "기준 초과"}
                {data.latency?.processing_seconds_median != null &&
                  `, 대기 뺀 처리시간 중앙값 ${fmtSec(data.latency.processing_seconds_median)}`}
                {` (${stamp} 기록)`}
              </span>
            </div>
          ) : (
            <p className="flow-hint" style={{ marginTop: 6 }}>
              응답시간 기록이 없습니다.
            </p>
          )}
        </section>
      </div>

      <h2>샘플별 결과</h2>
      <p className="flow-hint" style={{ marginBottom: 10 }}>
        행의 번호를 누르면 되묻기 · 검증 사유 · 보정 내역 · 종목 범위가 펼쳐집니다. 백테스트 지표는 {periodLabel(data.period)} 구간,
        {" "}
        {stamp} 기록입니다.
      </p>
      {cases.length === 0 ? (
        <div className="flow-empty">
          <strong>기록된 샘플이 없습니다.</strong>
          파일에 cases 가 비어 있습니다.
        </div>
      ) : (
        <CaseTable cases={cases} period={periodLabel(data.period)} />
      )}
    </>
  );
}

function CaseTable({ cases, period }: { cases: DemoCase[]; period: string }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <div className="flow-table flow-cases">
      <table style={{ minWidth: 980 }}>
        <thead>
          <tr>
            <th>번호</th>
            <th>성향</th>
            <th>입력 문장</th>
            <th>검증 축</th>
            <th>유니버스</th>
            <th>검증</th>
            <th>백테스트 ({period})</th>
            <th>결과</th>
          </tr>
        </thead>
        <tbody>
          {cases.map((c, i) => {
            const key = `${c.no}-${i}`;
            const isOpen = open.has(key);
            return (
              <Fragment key={key}>
                <tr className={isOpen ? "open" : undefined}>
                  <td>
                    <button
                      className="flow-expand"
                      aria-expanded={isOpen}
                      aria-controls={`case-${key}`}
                      onClick={() => toggle(key)}
                    >
                      {isOpen ? "▾" : "▸"} {c.no}
                    </button>
                  </td>
                  <td data-label="성향" style={{ whiteSpace: "nowrap" }}>
                    {c.profile_level != null && <span className="mono">{c.profile_level} </span>}
                    {c.profile_label ?? (c.profile_level == null ? "–" : "")}
                  </td>
                  <td data-label="입력 문장" className="text">
                    “{c.text}”
                  </td>
                  <td data-label="검증 축">
                    {c.axis ?? "–"}
                    {c.expected && <span className="sub">기대: {c.expected}</span>}
                  </td>
                  <td data-label="유니버스">
                    {universeSummary(c.compile)}
                    {c.compile?.status && c.compile.status !== "completed" && (
                      <span className="sub">컴파일 {COMPILE_STATUS[c.compile.status] ?? c.compile.status}</span>
                    )}
                  </td>
                  <td data-label="검증">
                    <ValidationCell v={c.validation} />
                  </td>
                  <td data-label={`백테스트 (${period})`}>
                    <BacktestCell b={c.backtest} level={c.profile_level} />
                  </td>
                  <td data-label="결과">
                    {c.result && RESULT_BADGE[c.result] ? (
                      <span className={`flow-badge ${RESULT_BADGE[c.result].tone}`}>{RESULT_BADGE[c.result].label}</span>
                    ) : (
                      <span className="flow-badge muted">미기록</span>
                    )}
                  </td>
                </tr>
                {isOpen && (
                  <tr className="detail" id={`case-${key}`}>
                    <td colSpan={8}>
                      <CaseDetail c={c} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ValidationCell({ v }: { v: DemoCase["validation"] }) {
  if (!v || v.passed === null || v.passed === undefined) return <span className="flow-hint">–</span>;
  if (v.passed) {
    return (
      <>
        <span className="flow-badge ok">통과</span>
        {!!v.adjustments?.length && <span className="sub">보정 {v.adjustments.length}건</span>}
      </>
    );
  }
  return (
    <>
      <span className="flow-badge err">막힘</span>
      {v.stage_failed != null && <span className="sub">단계 {String(v.stage_failed)}</span>}
    </>
  );
}

// 샘플 실행을 돌린 데모 계정. 성향 번호를 모르면 null
const demoAccount = (level: number | null) => (level != null && level >= 1 && level <= 5 ? `demo-p${level}` : null);

function LoginHint({ level }: { level: number | null }) {
  const acct = demoAccount(level);
  return (
    <span className="sub">
      {acct ? (
        <>
          <span className="mono">{acct}</span> 계정으로 로그인 필요
        </>
      ) : (
        "그 실행을 돌린 계정으로 로그인 필요"
      )}
    </span>
  );
}

function BacktestCell({ b, level }: { b: DemoCase["backtest"]; level: number | null }) {
  if (!b || (!b.status && b.run_id == null)) return <span className="flow-hint">–</span>;
  const done = b.status === "done";
  return (
    <>
      <span className={`flow-badge ${done ? "ok" : b.status === "failed" ? "err" : "muted"}`}>
        {done ? "완주" : b.status === "failed" ? "실패" : (b.status ?? "기록 없음")}
      </span>
      {done && (
        <span className="sub mono" style={{ fontSize: 11, color: "var(--c-text)" }}>
          CAGR {fmtPct(b.cagr)} · MDD {fmtPct(b.mdd)} · 샤프 {fmtNum(b.sharpe)}
        </span>
      )}
      {b.run_id != null && (
        <span className="sub">
          <Link href={`/report?run_id=${b.run_id}`}>실행 #{b.run_id}</Link>
        </span>
      )}
      {b.run_id != null && (
        <LoginHint level={level} />
      )}
    </>
  );
}

function List({
  items,
  empty,
  format = fmtAny,
}: {
  items: unknown[] | null | undefined;
  empty: string;
  format?: (v: unknown) => string;
}) {
  if (!items?.length) return <span className="flow-hint">{empty}</span>;
  return (
    <span style={{ display: "grid", gap: 2 }}>
      {items.map((x, i) => (
        <span key={i}>· {format(x)}</span>
      ))}
    </span>
  );
}

function CaseDetail({ c }: { c: DemoCase }) {
  const comp = c.compile;
  const rounds = comp?.rounds ?? [];
  const universe = comp?.universe ?? [];
  const b = c.backtest;
  const excluded = readExcluded(comp?.excluded);
  return (
    <dl className="flow-kv" style={{ borderTop: 0 }}>
      <dt>입력 문장</dt>
      <dd className="prose">“{c.text}”</dd>

      <dt>컴파일</dt>
      <dd>
        {comp?.status ? (COMPILE_STATUS[comp.status] ?? comp.status) : "–"}
        {comp?.spec_id && (
          <>
            {" "}
            · 전략서 <span className="mono">{comp.spec_id}</span>
          </>
        )}
        {" "}· 걸린 시간 <span className="mono">{fmtSec(comp?.seconds_total)}</span> (대기 뺀 처리{" "}
        <span className="mono">{fmtSec(comp?.seconds_processing)}</span>)
        {comp?.failure_reason && <span className="sub down">{comp.failure_reason}</span>}
      </dd>

      <dt>되묻기</dt>
      <dd>
        {rounds.length === 0 ? (
          <span className="flow-hint">되묻기 없이 끝남</span>
        ) : (
          <span style={{ display: "grid", gap: 8 }}>
            {rounds.map((r, i) => (
              <span key={i} style={{ display: "grid", gap: 2 }}>
                <span>
                  <span className="mono" style={{ color: "var(--c-warn)", marginRight: 8 }}>
                    {i + 1}
                  </span>
                  {r.question ?? "–"}
                </span>
                {!!r.choices?.length && <span className="flow-hint">선택지: {r.choices.join(" / ")}</span>}
                <span>
                  답: <b>{r.answer ?? "–"}</b>
                </span>
              </span>
            ))}
          </span>
        )}
      </dd>

      <dt>리밸런싱</dt>
      <dd>{comp?.rebalance == null ? "–" : fmtRebalance(comp.rebalance)}</dd>

      <dt>검증 사유</dt>
      <dd>
        <List items={c.validation?.reasons} empty="기록 없음" />
      </dd>

      <dt>보정 내역</dt>
      <dd>
        <List items={c.validation?.adjustments} empty="보정 없음" format={fmtAdjustment} />
      </dd>

      <dt>종목 범위</dt>
      <dd>
        {universe.length === 0 ? (
          <span className="flow-hint">–</span>
        ) : (
          <span style={{ display: "grid", gap: 2 }}>
            {universe.map((u) => (
              <span key={u.ticker}>
                <span className="mono" style={{ color: "var(--c-muted)", marginRight: 8 }}>
                  {u.ticker}
                </span>
                {u.name ?? ""}{" "}
                <span className="mono">
                  {fmtPct(u.weight_min, 0, false)} ~ {fmtPct(u.weight_max, 0, false)}
                </span>
              </span>
            ))}
          </span>
        )}
      </dd>

      {excluded.length > 0 && (
        <>
          <dt>담지 못한 종목</dt>
          <dd>
            <ExcludedList items={excluded} />
          </dd>
        </>
      )}

      <dt>백테스트</dt>
      <dd>
        {b?.run_id != null ? (
          <>
            <Link href={`/report?run_id=${b.run_id}`}>실행 #{b.run_id} 리포트</Link> ·{" "}
            <Link href={`/views?run_id=${b.run_id}`}>관점 브리핑</Link>
            <LoginHint level={c.profile_level} />
          </>
        ) : (
          <span className="flow-hint">실행 없음</span>
        )}
        {b?.view_sources && (
          <span className="sub">
            관점 출처:{" "}
            {Object.entries(b.view_sources)
              .map(([k, v]) => `${VIEW_LABEL[k] ?? k} ${v}`)
              .join(" · ")}
          </span>
        )}
        {b?.failure_reason && <span className="sub down">{b.failure_reason}</span>}
      </dd>

      {c.note && (
        <>
          <dt>메모</dt>
          <dd className="prose">{c.note}</dd>
        </>
      )}
    </dl>
  );
}

const VIEW_LABEL: Record<string, string> = { market: "시장분석", sentiment: "뉴스 감성", regime: "시장온도" };
