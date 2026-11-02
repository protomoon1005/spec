// 전략서 v0.2 흐름 화면이 같이 쓰는 조각: 거절 안내, 검사 결과, "AI가 이해한 전략" 설명.

import Link from "next/link";

import { REJECT_LABELS, STAGE_NAMES, type Check, type CheckItem, type Outcome } from "@/lib/v2";

// 거절: 사유의 쉬운 이름, 서버가 쓴 문장, 문장 속 근거. 처음 쓴 문장으로 다시 요청할 수 있다.
export function RejectBox({ outcome }: { outcome: Extract<Outcome, { status: "reject" }> }) {
  const label = REJECT_LABELS[outcome.code] ?? outcome.code;
  return (
    <div className="flow-msg err" role="alert" style={{ marginTop: 0 }}>
      <p style={{ color: "var(--c-bright)", fontWeight: 600 }}>
        만들 수 없는 전략이에요 — {label}
        {outcome.stage ? ` (검사 ${outcome.stage}단계 ${STAGE_NAMES[outcome.stage] ?? ""})` : ""}
      </p>
      <p style={{ marginTop: 6 }}>{outcome.message}</p>
      {outcome.evidence && outcome.evidence !== outcome.message && (
        <p className="flow-hint" style={{ marginTop: 6 }}>
          문장 속 근거: <span className="mono">{outcome.evidence}</span>
        </p>
      )}
      <p style={{ marginTop: 10 }}>
        <Link href="/compile?retry=1">문장을 고쳐서 다시 요청 — 방금 쓴 문장으로</Link>
      </p>
    </div>
  );
}

const RESULT_BADGES: Record<Check["result"], { text: string; cls: string }> = {
  pass: { text: "통과", cls: "ok" },
  warn: { text: "경고", cls: "warn" },
  ask: { text: "되묻기", cls: "warn" },
  block: { text: "막힘", cls: "err" },
  not_run: { text: "안 돎", cls: "muted" },
};

// 검사 1~4단계 결과. 경고 항목은 그 문장을 그대로 보여 준다.
export function CheckList({ checks }: { checks: Check[] }) {
  const sorted = [...checks].sort((a, b) => a.stage - b.stage);
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 8 }}>
      {sorted.map((c) => {
        const badge = RESULT_BADGES[c.result] ?? RESULT_BADGES.pass;
        return (
          <li key={c.stage}>
            <span className={`flow-badge ${badge.cls}`} style={{ marginRight: 8 }}>
              {badge.text}
            </span>
            검사 {c.stage}단계 · {c.name ?? STAGE_NAMES[c.stage]}
            {c.items.length > 0 && (
              <ul style={{ margin: "4px 0 0 0" }}>
                {c.items.map((item, i) => (
                  <li key={i} className="flow-hint">
                    {itemText(item)}
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function itemText(item: CheckItem): string {
  if (item.code === "retried") return "AI 가 처음 짝지을 때 빠뜨린 말이 있어 한 번 다시 짝지었어요";
  if (item.code === "ignored_by_user") return "되묻기에서 '무시하기' 를 고른 말이 있어요";
  return item.message;
}

// 저장된 검사 기록(GET /v2/strategies/{id})을 검사 결과 모양으로 바꾼다.
export function checksFromLogs(logs: { stage: number; passed: boolean; items: CheckItem[] }[]): Check[] {
  return logs.map((log) => ({
    stage: log.stage,
    result: !log.passed ? "block" : log.items.length > 0 ? "warn" : "pass",
    items: log.items,
  }));
}

// "AI가 이해한 전략". "(기본값)" 은 흐리게, 들여 쓴 줄("  - ")은 하위 항목으로 그린다.
export function Explanation({ lines }: { lines: string[] }) {
  return (
    <dl className="flow-kv">
      {lines.map((line, i) => {
        if (line.startsWith("  - ")) {
          return (
            <div key={i} style={{ display: "contents" }}>
              <dt />
              <dd>· {line.slice(4)}</dd>
            </div>
          );
        }
        const at = line.indexOf(": ");
        const head = at > 0 ? line.slice(0, at) : "";
        const body = at > 0 ? line.slice(at + 2) : line;
        const isDefault = body.endsWith(" (기본값)");
        return (
          <div key={i} style={{ display: "contents" }}>
            <dt>{head}</dt>
            <dd>
              {isDefault ? body.slice(0, -" (기본값)".length) : body}
              {isDefault && (
                <span className="flow-badge muted" style={{ marginLeft: 8 }}>
                  기본값
                </span>
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
