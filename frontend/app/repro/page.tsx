"use client";

// 재현성 비교 (데모 시연 5번) — 같은 입력이면 같은 주문이 나오는가.
//
//   1) 기록 판정   public/data/demo-samples.json 의 reproducibility. 같은 전략서로 두 번 돌린 실행의
//                  목표비중 · 주문 · 자산곡선이 같은지, 주문 표본을 두 열로 놓는다. 로그인 없이 본다.
//   2) 서버 대조   로그인돼 있으면 두 실행을 GET /backtest/runs/{id} 로 받아 결정마다 맞대 본다.
//                  실행은 돌린 사람만 볼 수 있어 다른 계정이면 404 다(그 이유를 그대로 적는다).
//   3) 컴파일      같은 문장을 두 번 컴파일한 전략서가 같은지.

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { DemoBasis, DemoNotReady, useDemoSamples } from "@/components/demo-samples";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, ApiError, isExpired, loadSession } from "@/lib/api";
import { fmtAny, fmtStamp, isNum, type DemoSamples } from "@/lib/demo-samples";
import { compareRuns, deltaPair, splitSampleOrder, TOLERANCE, type ReproComparison, type RunForRepro } from "@/lib/repro";

export default function ReproPage() {
  const state = useDemoSamples();
  return (
    <ScaffoldShell wide>
      <main>
        <h1>재현성 비교</h1>
        <p>이 화면에서 하는 일: 같은 전략서를 두 번 돌린 두 실행의 목표비중 · 주문 · 자산곡선이 같은지, 같은 문장을 두 번 컴파일한 전략서가 같은지 본다.</p>
        {state.kind === "ok" ? <Body data={state.data} /> : <DemoNotReady state={state} />}
        <div className="flow-next" style={{ justifyContent: "flex-start" }}>
          <Link href="/samples">샘플 결과로</Link>
          <Link href="/home">홈으로</Link>
        </div>
      </main>
    </ScaffoldShell>
  );
}

type Tri = boolean | null | undefined;

const triLabel = (v: Tri) => (v === true ? "같음" : v === false ? "다름" : "기록 없음");
const triTone = (v: Tri) => (v === true ? "ok" : v === false ? "err" : "muted");

function verdictOf(values: Tri[]): Tri {
  if (values.some((v) => v === false)) return false;
  if (values.every((v) => v === true)) return true;
  return null;
}

function Body({ data }: { data: DemoSamples }) {
  const bt = data.reproducibility?.backtest ?? null;
  const cp = data.reproducibility?.compile ?? null;
  const stamp = fmtStamp(data.generated_at);
  const runIds = bt?.run_ids ?? null;
  const [ra, rb] = [runIds?.[0] ?? null, runIds?.[1] ?? null];

  return (
    <>
      <DemoBasis data={data} />

      <h2 style={{ marginTop: 0 }}>백테스트 — 기록 판정</h2>
      {!bt ? (
        <div className="flow-empty">
          <strong>백테스트 재현성 기록이 없습니다.</strong>
        </div>
      ) : (
        <BacktestRecord bt={bt} stamp={stamp} ra={ra} rb={rb} />
      )}

      {bt && ra != null && rb != null && <LiveCompare a={ra} b={rb} />}

      <h2>컴파일 — 같은 문장, 두 번</h2>
      {!cp ? (
        <div className="flow-empty">
          <strong>컴파일 재현성 기록이 없습니다.</strong>
        </div>
      ) : (
        <CompileRecord cp={cp} stamp={stamp} model={data.llm_model} />
      )}
    </>
  );
}

function BacktestRecord({
  bt,
  stamp,
  ra,
  rb,
}: {
  bt: NonNullable<NonNullable<DemoSamples["reproducibility"]>["backtest"]>;
  stamp: string;
  ra: number | null;
  rb: number | null;
}) {
  const verdict = verdictOf([bt.same_weights, bt.same_orders, bt.same_curve]);
  const orders = bt.sample_orders ?? [];
  const split = orders.map((o) => splitSampleOrder(o, bt.run_ids));
  const paired = orders.length > 0 && split.every((x) => x !== null);

  return (
    <>
      <section className={`flow-card ${verdict === true ? "ok" : verdict === false ? "err" : ""}`}>
        <div className={`flow-verdict ${triTone(verdict)}`}>
          <strong style={{ fontSize: 30 }}>{verdict === true ? "재현됨" : verdict === false ? "재현 안 됨" : "판정 보류"}</strong>
          <span className="flow-hint">
            전략서 <span className="mono">{bt.spec_id ?? "–"}</span> · 실행{" "}
            {ra != null ? <Link href={`/report?run_id=${ra}`}>#{ra}</Link> : "–"} 와{" "}
            {rb != null ? <Link href={`/report?run_id=${rb}`}>#{rb}</Link> : "–"} · 결정{" "}
            <span className="mono">{isNum(bt.n_decisions) ? bt.n_decisions : "–"}</span>회 ({stamp} 기록)
          </span>
        </div>
        <div className="flow-tiles" style={{ marginTop: 14 }}>
          {(
            [
              ["목표비중", bt.same_weights],
              ["주문", bt.same_orders],
              ["자산곡선", bt.same_curve],
            ] as const
          ).map(([label, v]) => (
            <div key={label}>
              <span>{label}</span>
              <b className={v === true ? "up" : v === false ? "down" : undefined}>{triLabel(v)}</b>
              <small>
                결정 {isNum(bt.n_decisions) ? bt.n_decisions : "–"}회 전체 · {stamp}
              </small>
            </div>
          ))}
        </div>
      </section>

      <h3>주문 표본 — 목표비중 변화(Δ)</h3>
      {orders.length === 0 ? (
        <p className="flow-hint">기록된 주문 표본이 없습니다.</p>
      ) : paired ? (
        <div style={{ display: "grid", gap: 8 }}>
          <div className="flow-pair" aria-hidden>
            <span className="flow-hint">실행 #{ra ?? "A"}</span>
            <span className="flow-hint">실행 #{rb ?? "B"}</span>
          </div>
          {split.map((x, i) => {
            const delta = deltaPair(x!.a, x!.b);
            const same = delta ? delta.diffKeys.length === 0 : fmtAny(x!.a) === fmtAny(x!.b);
            const bad = new Set(delta?.diffKeys ?? []);
            return (
              <div key={i}>
                {Object.keys(x!.common).length > 0 && (
                  <div className="flow-hint mono" style={{ fontSize: 11 }}>
                    {Object.entries(x!.common)
                      .map(([k, v]) => `${k} ${fmtAny(v)}`)
                      .join(" · ")}
                  </div>
                )}
                <div className="flow-pair">
                  <div style={same ? undefined : { borderColor: "#f2647a66" }}>
                    {delta ? <Weights w={delta.a} bad={bad} signed /> : <pre>{pretty(x!.a)}</pre>}
                  </div>
                  <div style={same ? undefined : { borderColor: "#f2647a66" }}>
                    {delta ? <Weights w={delta.b} bad={bad} signed /> : <pre>{pretty(x!.b)}</pre>}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <>
          <p className="flow-hint" style={{ marginBottom: 8 }}>
            표본이 두 실행 공통 한 목록으로 기록돼 있습니다(두 실행의 값을 나눠 적지 않은 모양).
          </p>
          <div className="flow-pair">
            <div>
              <span className="flow-hint">실행 #{ra ?? "A"}</span>
              <pre>{orders.map(pretty).join("\n")}</pre>
            </div>
            <div>
              <span className="flow-hint">실행 #{rb ?? "B"}</span>
              <pre>{bt.same_orders === true ? orders.map(pretty).join("\n") : "주문이 같다는 기록이 없어 옮겨 적지 않습니다."}</pre>
            </div>
          </div>
        </>
      )}
    </>
  );
}

function pretty(v: unknown): string {
  if (v && typeof v === "object") {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${k}: ${typeof x === "number" ? fmtNumber(x) : fmtAny(x)}`)
      .join("\n");
  }
  return fmtAny(v);
}

const fmtNumber = (x: number) => String(Number(x.toFixed(6)));

// --- 서버 대조 --------------------------------------------------------------

type Live =
  | { kind: "idle"; message: string; login?: boolean }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; cmp: ReproComparison; a: RunForRepro; b: RunForRepro };

function LiveCompare({ a, b }: { a: number; b: number }) {
  const [state, setState] = useState<Live>({ kind: "loading" });
  const [onlyDiff, setOnlyDiff] = useState(false);

  useEffect(() => {
    const session = loadSession();
    if (!session) {
      setState({ kind: "idle", message: "로그인하면 두 실행의 전체 결정을 서버에서 받아 행마다 맞대 봅니다.", login: true });
      return;
    }
    if (isExpired(session.access)) {
      setState({ kind: "idle", message: "로그인이 만료됐습니다(30분). 다시 로그인하면 서버에서 대조합니다.", login: true });
      return;
    }
    let alive = true;
    Promise.all([api<RunForRepro>(`/backtest/runs/${a}`), api<RunForRepro>(`/backtest/runs/${b}`)])
      .then(([x, y]) => {
        if (!alive) return;
        if (x.status !== "done" || y.status !== "done") {
          setState({ kind: "error", message: `두 실행이 모두 끝난 상태가 아닙니다 (#${a} ${x.status ?? "?"}, #${b} ${y.status ?? "?"}).` });
          return;
        }
        setState({ kind: "ok", cmp: compareRuns(x, y), a: x, b: y });
      })
      .catch((err) => {
        if (!alive) return;
        const message =
          err instanceof ApiError && err.status === 404
            ? `실행 기록을 찾을 수 없습니다 — 실행은 돌린 계정만 볼 수 있습니다. 샘플을 돌린 계정으로 로그인하세요. (${err.message})`
            : err instanceof ApiError && err.status === 401
              ? "로그인이 만료됐습니다. 다시 로그인하세요."
              : `실행 기록을 받지 못했습니다 — ${err instanceof Error ? err.message : String(err)}`;
        setState({ kind: "error", message });
      });
    return () => {
      alive = false;
    };
  }, [a, b]);

  return (
    <>
      <h2>백테스트 — 서버에서 받아 결정마다 대조</h2>
      {state.kind === "loading" && <p className="flow-state">실행 #{a} · #{b} 를 받아 오는 중…</p>}
      {state.kind === "idle" && (
        <div className="flow-empty">
          <strong>서버 대조는 로그인 후에 합니다.</strong>
          {state.message} {state.login && <Link href="/login">로그인</Link>}
        </div>
      )}
      {state.kind === "error" && (
        <p className="flow-msg warn" role="alert">
          {state.message}
        </p>
      )}
      {state.kind === "ok" && <LiveResult s={state} a={a} b={b} onlyDiff={onlyDiff} setOnlyDiff={setOnlyDiff} />}
    </>
  );
}

function LiveResult({
  s,
  a,
  b,
  onlyDiff,
  setOnlyDiff,
}: {
  s: Extract<Live, { kind: "ok" }>;
  a: number;
  b: number;
  onlyDiff: boolean;
  setOnlyDiff: (v: boolean) => void;
}) {
  const { cmp } = s;
  const verdict = cmp.sameWeights && cmp.sameOrders && cmp.curve.same;
  const rows = useMemo(() => (onlyDiff ? cmp.rows.filter((r) => !r.same) : cmp.rows), [cmp, onlyDiff]);
  const asof = s.a.data_snapshot_asof === s.b.data_snapshot_asof ? s.a.data_snapshot_asof : `${s.a.data_snapshot_asof} / ${s.b.data_snapshot_asof}`;

  return (
    <>
      <section className={`flow-card ${verdict ? "ok" : "err"}`}>
        <div className={`flow-verdict ${verdict ? "ok" : "err"}`}>
          <strong>{verdict ? "서버 기록도 같음" : `다른 결정 ${cmp.diffRows}건`}</strong>
          <span className="flow-hint">
            결정 #{a} {cmp.nA}회 · #{b} {cmp.nB}회, 자산곡선 {cmp.curve.nA}점 중 다른 점 {cmp.curve.diffPoints}개 · 데이터 기준{" "}
            <span className="mono">{asof ?? "–"}</span> · 허용오차 {TOLERANCE}
          </span>
        </div>
        <div className="flow-tiles" style={{ marginTop: 14 }}>
          {(
            [
              ["목표비중", cmp.sameWeights],
              ["주문 (비중 변화)", cmp.sameOrders],
              ["자산곡선", cmp.curve.same],
            ] as const
          ).map(([label, v]) => (
            <div key={label}>
              <span>{label}</span>
              <b className={v ? "up" : "down"}>{v ? "같음" : "다름"}</b>
              <small>서버 기록 · 기준 {asof ?? "–"}</small>
            </div>
          ))}
        </div>
      </section>

      <div className="flow-actions" style={{ justifyContent: "space-between", margin: "14px 0 8px" }}>
        <span className="flow-hint">
          주문은 직전 결정 대비 목표비중 변화(Δ)입니다 — 실행 기록에 체결 주문이 따로 남지 않습니다. 다른 칸은 빨간색입니다.
        </span>
        <button className="flow-chip" aria-pressed={onlyDiff} onClick={() => setOnlyDiff(!onlyDiff)}>
          다른 행만 보기
        </button>
      </div>
      {rows.length === 0 ? (
        <div className="flow-empty">
          <strong>다른 행이 없습니다.</strong>
          결정 {cmp.rows.length}회 전부 목표비중 · 현금 · 주문이 같습니다.
        </div>
      ) : (
        <div className="flow-table" style={{ maxHeight: 520, overflowY: "auto" }}>
          <table style={{ minWidth: 760 }}>
            <thead>
              <tr>
                <th>#</th>
                <th>결정일</th>
                <th>목표비중 #{a}</th>
                <th>목표비중 #{b}</th>
                <th>주문 Δ #{a}</th>
                <th>주문 Δ #{b}</th>
                <th>판정</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const bad = new Set(r.diffKeys);
                return (
                  <tr key={r.index} className={r.same ? undefined : "diff"}>
                    <td className="mono">{r.index}</td>
                    <td className={`mono${bad.has("날짜") ? " diff-val" : ""}`}>
                      {r.dateA === r.dateB ? r.date : `${r.dateA ?? "–"} / ${r.dateB ?? "–"}`}
                    </td>
                    <td>
                      <Weights w={r.targetA} cash={r.cashA} bad={bad} />
                    </td>
                    <td>
                      <Weights w={r.targetB} cash={r.cashB} bad={bad} />
                    </td>
                    <td>
                      <Weights w={r.orderA} bad={bad} signed />
                    </td>
                    <td>
                      <Weights w={r.orderB} bad={bad} signed />
                    </td>
                    <td>
                      {r.same ? (
                        <span className="flow-badge ok">같음</span>
                      ) : (
                        <>
                          <span className="flow-badge err">다름</span>
                          <span className="flow-hint" style={{ display: "block", fontSize: 11 }}>
                            {r.diffKeys.join(", ")}
                          </span>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function Weights({
  w,
  cash,
  bad,
  signed = false,
}: {
  w: Record<string, number> | null;
  cash?: number | null;
  bad: Set<string>;
  signed?: boolean;
}) {
  if (!w) return <span className="flow-hint">행 없음</span>;
  const fmt = (v: number) => {
    const s = `${(Math.abs(v) * 100).toFixed(2)}%`;
    if (!signed) return `${(v * 100).toFixed(2)}%`;
    return v > 1e-12 ? `+${s}` : v < -1e-12 ? `−${s}` : "0.00%";
  };
  return (
    <span className="flow-cell-list">
      {Object.keys(w)
        .sort()
        .map((k) => (
          <span key={k} className={bad.has(k) ? "diff-val" : undefined}>
            <span style={{ color: "var(--c-muted)" }}>{k}</span> {fmt(w[k])}
          </span>
        ))}
      {cash != null && (
        <span className={bad.has("현금") ? "diff-val" : undefined}>
          <span style={{ color: "var(--c-muted)" }}>현금</span> {fmt(cash)}
        </span>
      )}
    </span>
  );
}

// --- 컴파일 -----------------------------------------------------------------

function CompileRecord({
  cp,
  stamp,
  model,
}: {
  cp: NonNullable<NonNullable<DemoSamples["reproducibility"]>["compile"]>;
  stamp: string;
  model: string | null;
}) {
  const v = cp.same_spec;
  const diff = cp.diff ?? [];
  return (
    <section className={`flow-card ${v === true ? "ok" : v === false ? "err" : ""}`}>
      <div className={`flow-verdict ${triTone(v)}`}>
        <strong>{v === true ? "같은 전략서" : v === false ? "다른 전략서" : "판정 보류"}</strong>
        <span className="flow-hint">
          {model ? `${model}, ` : ""}
          {stamp} 기록
        </span>
      </div>
      <dl className="flow-kv" style={{ marginTop: 12 }}>
        <dt>입력 문장</dt>
        <dd className="prose">{cp.text ? `“${cp.text}”` : "–"}</dd>
        <dt>전략서</dt>
        <dd className="mono">{(cp.spec_ids ?? []).map((x) => x ?? "–").join("  ·  ") || "–"}</dd>
        <dt>다른 곳</dt>
        <dd>
          {diff.length === 0 ? (
            <span className="flow-hint">{v === true ? "없음" : "기록 없음"}</span>
          ) : (
            <span style={{ display: "grid", gap: 2 }}>
              {diff.map((d, i) => (
                <span key={i} className="mono" style={{ fontSize: 12 }}>
                  · {fmtAny(d)}
                </span>
              ))}
            </span>
          )}
        </dd>
      </dl>
    </section>
  );
}
