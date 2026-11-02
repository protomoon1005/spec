"use client";

// 채팅형 화면의 카드. 현서 님 demo/app.py 의 draw_clarify · draw_reject · draw_understood · draw_result 를 옮겼다.

import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { fmtPct, fmtWon, REJECT_PLAIN, type Ask, type Ok, type Reject } from "@/lib/chat";

import { TradeCharts } from "./charts";
import { REJECT_LABELS, STAGE_NAMES, type BacktestV2, type Check } from "@/lib/v2";

export const COLORS = { accent: "#2a78d6", orange: "#eb6834", green: "#1baf7a", muted: "#6b7280", grid: "#eef0f2" };

export function UserBubble({ text }: { text: string }) {
  return (
    <div className="u-row">
      <div className="u-bubble">{text}</div>
    </div>
  );
}

// ── 되묻기 ─────────────────────────────────────────────────────────────

const DIRECT = "__direct__";

export function AskCard({
  outcome,
  reply,
  live,
  busy,
  onAnswer,
}: {
  outcome: Ask;
  reply?: Record<string, string>;
  live: boolean;
  busy: boolean;
  onAnswer: (answers: { question_id: string; choice_id?: string; text?: string }[], labels: Record<string, string>) => void;
}) {
  const [picks, setPicks] = useState<Record<string, string>>({});
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [warn, setWarn] = useState<string | null>(null);

  function submit() {
    const answers = [];
    const labels: Record<string, string> = {};
    for (const q of outcome.questions) {
      const pick = picks[q.id];
      if (pick === DIRECT) {
        const text = (typed[q.id] ?? "").trim();
        if (!text) return setWarn("직접 입력을 골랐으면 답을 적어 주세요.");
        answers.push({ question_id: q.id, text });
        labels[q.id] = text;
      } else if (pick) {
        answers.push({ question_id: q.id, choice_id: pick });
        labels[q.id] = q.choices.find((c) => c.id === pick)?.label ?? pick;
      } else {
        return setWarn("모든 질문에 답해 주세요.");
      }
    }
    setWarn(null);
    onAnswer(answers, labels);
  }

  return (
    <div className="ai-block">
      <p className="ai-title">
        확인할 게 있어요 <span className="tag">되묻기 {outcome.round}회째</span>
      </p>
      {outcome.questions.map((q) => (
        <div key={q.id} className="question">
          <p>{q.text}</p>
          {reply ? (
            <div className="pills">
              <span className="pill on">{reply[q.id] ?? "—"}</span>
            </div>
          ) : (
            <>
              <div className="pills">
                {q.choices.map((c) => (
                  <button
                    key={c.id}
                    className={`pill${picks[q.id] === c.id ? " on" : ""}`}
                    disabled={!live || busy}
                    onClick={() => setPicks({ ...picks, [q.id]: c.id })}
                  >
                    {c.label}
                  </button>
                ))}
                {q.allow_text && (
                  <button
                    className={`pill${picks[q.id] === DIRECT ? " on" : ""}`}
                    disabled={!live || busy}
                    onClick={() => setPicks({ ...picks, [q.id]: DIRECT })}
                  >
                    직접 입력
                  </button>
                )}
              </div>
              {picks[q.id] === DIRECT && (
                <input
                  className="text-input"
                  placeholder="답을 적어 주세요"
                  value={typed[q.id] ?? ""}
                  disabled={!live || busy}
                  onChange={(e) => setTyped({ ...typed, [q.id]: e.target.value })}
                />
              )}
            </>
          )}
        </div>
      ))}
      {live && !reply && (
        <div className="row-buttons">
          <button className="btn primary" disabled={busy} onClick={submit}>
            답하기
          </button>
          {warn && <span className="warn-text">{warn}</span>}
        </div>
      )}
    </div>
  );
}

// ── 거절 ───────────────────────────────────────────────────────────────

export function RejectCard({ outcome, request }: { outcome: Reject; request: string }) {
  const label = REJECT_LABELS[outcome.code] ?? outcome.code;
  const at = outcome.evidence ? request.indexOf(outcome.evidence) : -1;
  return (
    <div className="ai-block">
      <span className="badge red">
        {outcome.code} · {label}
      </span>
      <p style={{ marginTop: 10 }}>{REJECT_PLAIN[outcome.code] ?? outcome.message}</p>
      <div className="muted">원문</div>
      <div>
        &ldquo;
        {at >= 0 ? (
          <>
            {request.slice(0, at)}
            <mark>{outcome.evidence}</mark>
            {request.slice(at + outcome.evidence.length)}
          </>
        ) : (
          request
        )}
        &rdquo;
      </div>
      <details className="expander">
        <summary>자세히</summary>
        <p>{outcome.message}</p>
        {outcome.stage ? (
          <p className="muted">
            검사 {outcome.stage}단계 {STAGE_NAMES[outcome.stage] ?? ""} 에서 막혔어요.
          </p>
        ) : null}
      </details>
    </div>
  );
}

// ── AI가 이해한 전략 ───────────────────────────────────────────────────

const CHECK_LABEL: Record<Check["result"], { icon: string; text: string }> = {
  pass: { icon: "✅", text: "통과" },
  warn: { icon: "⚠️", text: "경고" },
  ask: { icon: "❓", text: "되묻기" },
  block: { icon: "⛔", text: "막힘" },
  not_run: { icon: "➖", text: "건너뜀" },
};

export function UnderstoodCard({
  outcome,
  used,
  busy,
  onBacktest,
  onRevise,
  anchorId,
}: {
  outcome: Ok;
  used?: boolean;
  busy: boolean;
  onBacktest: () => void;
  onRevise: () => void;
  anchorId: string;
}) {
  const checks = [...outcome.checks].sort((a, b) => a.stage - b.stage);
  const passed = checks.filter((c) => c.result === "pass").length;
  const warned = checks.some((c) => c.result === "warn");
  return (
    <div className="card" id={anchorId}>
      <p className="ai-title">AI가 이해한 전략</p>
      <div className="core">
        {outcome.explanation.map((line, i) => {
          if (line.startsWith("  - ")) {
            return (
              <div key={i} style={{ display: "contents" }}>
                <div className="k" />
                <div className="sub">· {line.slice(4)}</div>
              </div>
            );
          }
          const at = line.indexOf(": ");
          const head = at > 0 ? line.slice(0, at) : "";
          let body = at > 0 ? line.slice(at + 2) : line;
          const isDefault = body.endsWith(" (기본값)");
          if (isDefault) body = body.slice(0, -" (기본값)".length);
          return (
            <div key={i} style={{ display: "contents" }}>
              <div className="k">{head}</div>
              <div>
                {body}
                {isDefault && <span className="tag">기본값</span>}
              </div>
            </div>
          );
        })}
      </div>

      <details className="expander" open={warned}>
        <summary>
          검사 {passed}/{checks.length} 통과{warned ? " · 경고 있음" : ""}
        </summary>
        {checks.map((c) => (
          <div key={c.stage} className="check-line">
            {CHECK_LABEL[c.result].icon} <b>{c.stage}단계 {c.name ?? STAGE_NAMES[c.stage]}</b> — {CHECK_LABEL[c.result].text}
            {c.items.map((item, i) => (
              <div key={i} className="muted">
                {item.message}
              </div>
            ))}
          </div>
        ))}
      </details>
      <details className="expander">
        <summary>공통 규칙</summary>
        <div className="rules">
          <div className="k">판단과 체결</div>
          <div>그날 종가로 판단하고 다음 거래일 시가에 사고팝니다. 사기 · 팔기 조건이 같은 날 둘 다 맞으면 사지 않아요.</div>
          <div className="k">비용</div>
          <div>수수료 0.015%, 체결 오차 0.05%를 사고팔 때마다 뺍니다.</div>
          <div className="k">세금</div>
          <div>주식은 팔 때 증권거래세, 국내 주식형이 아닌 ETF 는 매매 차익의 15.4% 를 뺍니다.</div>
          <div className="k">주문 단위</div>
          <div>1주 단위로 사고, 남는 돈은 현금으로 둡니다.</div>
        </div>
      </details>
      <details className="expander">
        <summary>전략서 JSON</summary>
        <pre className="json">{JSON.stringify(outcome.spec, null, 2)}</pre>
      </details>

      <div className="row-buttons">
        <button className="btn primary" disabled={used || busy} onClick={onBacktest}>
          이대로 백테스트
        </button>
        <button className="btn" disabled={used || busy} onClick={onRevise}>
          고쳐 말하기
        </button>
      </div>
    </div>
  );
}

// ── 백테스트 결과 ───────────────────────────────────────────────────────

export function ResultCard({
  run,
  onRetry,
  onToCard,
  onHome,
  onBaseline,
  baselineDone,
  busy,
}: {
  run: BacktestV2;
  onRetry: () => void;
  onToCard: () => void;
  onHome: () => void;
  onBaseline: () => void;
  baselineDone?: boolean;
  busy: boolean;
}) {
  const s = run.summary!;
  const trades = run.trades ?? [];
  const names = run.names ?? {};
  const name = (t: string) => names[t] ?? t;
  const holding = Object.entries(run.holdings_at_end ?? {});
  return (
    <>
      <div className="card">
        <p className="ai-title">
          백테스트 결과{" "}
          <span className="muted">
            {run.period_start} ~ {run.period_end} · 시작 {fmtWon(run.seed_money)}
          </span>
        </p>
        <div className="metrics">
          <Metric label="총수익률" value={fmtPct(s.strategy.total_return)} tone={s.strategy.total_return} />
          <Metric label="연 수익률" value={fmtPct(s.strategy.cagr)} tone={s.strategy.cagr ?? 0} />
          <Metric label="MDD" value={fmtPct(Math.abs(s.strategy.mdd), false)} />
          <Metric label="거래 횟수" value={`${trades.length}회`} />
        </div>
        <p className="muted">
          비교 — KODEX 200 보유(코스피 대신) {fmtPct(s.market_proxy.total_return)} · 같은 종목 단순 보유{" "}
          {fmtPct(s.hold_same.total_return)}
        </p>

        <p className="chart-title">자산 곡선</p>
        <div style={{ height: 320 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={run.series ?? []} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
              <CartesianGrid stroke={COLORS.grid} vertical={false} />
              <XAxis dataKey="date" tick={{ fill: COLORS.muted, fontSize: 11 }} tickFormatter={(d: string) => d.slice(0, 7)} minTickGap={48} />
              <YAxis
                tick={{ fill: COLORS.muted, fontSize: 11 }}
                tickFormatter={(v: number) => `${Math.round(v / 10_000).toLocaleString("ko-KR")}만`}
                width={60}
                domain={["auto", "auto"]}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip formatter={(v: number) => fmtWon(v)} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line type="monotone" dataKey="strategy" name="전략" stroke={COLORS.accent} dot={false} strokeWidth={2} />
              <Line type="monotone" dataKey="market_proxy" name="KODEX 200 보유" stroke={COLORS.orange} dot={false} strokeWidth={1.5} />
              <Line type="monotone" dataKey="hold_same" name="단순 보유" stroke={COLORS.green} dot={false} strokeWidth={1.5} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {(run.trade_chart ?? []).length > 0 && (
          <TradeCharts dates={(run.series ?? []).map((p) => p.date)} charts={run.trade_chart ?? []} />
        )}

        {(run.unfilled ?? []).length > 0 && (
          <div className="warn-box">
            체결하지 못한 주문 {(run.unfilled ?? []).length}건 —{" "}
            {(run.unfilled ?? [])
              .slice(0, 3)
              .map((u) => `${name(u.ticker)} ${u.decided_on}: ${u.reason}`)
              .join(" · ")}
          </div>
        )}

        <details className="expander">
          <summary>거래 내역 ({trades.length + holding.length}건)</summary>
          {trades.length + holding.length === 0 ? (
            <p>거래가 없습니다.</p>
          ) : (
            <div className="trades-wrap">
              <table className="trades">
                <thead>
                  <tr>
                    <th>종목</th>
                    <th>산 날</th>
                    <th className="r">산 값</th>
                    <th>판 날</th>
                    <th className="r">판 값</th>
                    <th className="r">보유일</th>
                    <th className="r">수익률</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t, i) => (
                    <tr key={i}>
                      <td>
                        {name(t.ticker)}
                        <span className="code">{t.ticker}</span>
                      </td>
                      <td>{t.buy_date}</td>
                      <td className="r">{fmtWon(t.buy_price)}</td>
                      <td>{t.sell_date}</td>
                      <td className="r">{fmtWon(t.sell_price)}</td>
                      <td className="r">{t.holding_days}일</td>
                      <td className={`r ${t.return > 0 ? "up" : t.return < 0 ? "down" : ""}`}>{fmtPct(t.return)}</td>
                    </tr>
                  ))}
                  {holding.map(([t, n]) => (
                    <tr key={t}>
                      <td>
                        {name(t)}
                        <span className="code">{t}</span>
                      </td>
                      <td colSpan={2} className="muted">
                        {n.toLocaleString("ko-KR")}주
                      </td>
                      <td className="open">보유 중</td>
                      <td colSpan={3} />
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </details>
        {run.repro && (
          <div style={{ marginTop: 12 }}>
            {run.repro.match ? (
              <span className="badge green" title={run.repro.hash}>
                다시 실행 → 결과 일치 ✓ {run.repro.hash.slice(0, 8)}
              </span>
            ) : (
              <span className="badge red">
                다시 실행 → 결과 불일치 ✗ {run.repro.first_hash.slice(0, 8)} ≠ {run.repro.hash.slice(0, 8)}
              </span>
            )}
          </div>
        )}
        <p className="small">
          가격 데이터 {run.data_snapshot_asof} 까지 · 수수료 0.015% · 체결 오차 0.05% · 세금 포함
          {(run.notes ?? []).map((n) => (
            <span key={n}>
              <br />
              {n}
            </span>
          ))}
        </p>
      </div>
      <div className="row-buttons three">
        <button className="btn" onClick={onRetry} title="원래 문장을 입력창에 채워 둡니다.">
          조건 바꿔서 다시
        </button>
        <button className="btn" onClick={onToCard}>
          전략 카드로 돌아가기
        </button>
        <button className="btn" onClick={onHome}>
          처음으로
        </button>
      </div>
      <button
        className="btn wide"
        style={{ marginTop: 8 }}
        disabled={baselineDone || busy}
        onClick={onBaseline}
        title="같은 문장으로 LLM 이 파이썬 코드를 직접 짜게 해 돌리고, 우리 결과와 나란히 보여 줍니다."
      >
        LLM 코드 생성과 비교{baselineDone ? " (비교함)" : ""}
      </button>
    </>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: number }) {
  const cls = tone === undefined || tone === 0 ? "" : tone > 0 ? "up" : "down";
  return (
    <div className="metric">
      <div className="muted">{label}</div>
      <div className={`metric-value ${cls}`}>{value}</div>
    </div>
  );
}
