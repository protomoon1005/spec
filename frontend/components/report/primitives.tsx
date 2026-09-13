"use client";

import type { ReactNode } from "react";
import { C, MONO, R, SANS } from "./tokens";

export function Panel({
  title,
  sub,
  source,
  children,
}: {
  title: string;
  sub?: string;
  source?: string;
  children: ReactNode;
}) {
  return (
    <section style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: R.panel, overflow: "hidden" }}>
      <div
        style={{ padding: "14px 20px", borderBottom: `1px solid ${C.border}` }}
        className="flex items-baseline gap-3 flex-wrap"
      >
        <h2
          style={{
            fontFamily: MONO,
            fontSize: 12,
            fontWeight: 600,
            color: C.bright,
            letterSpacing: "0.01em",
            margin: 0,
          }}
        >
          {title}
        </h2>
        {sub && <span style={{ fontFamily: SANS, fontSize: 12, color: C.muted }}>{sub}</span>}
      </div>
      <div style={{ padding: "16px 20px" }}>{children}</div>
      {source && (
        <div
          style={{
            padding: "8px 20px",
            borderTop: `1px solid ${C.border}`,
            fontFamily: MONO,
            fontSize: 10,
            color: C.muted,
            letterSpacing: "0.03em",
          }}
        >
          {source}
        </div>
      )}
    </section>
  );
}

// 지표 이름 옆의 물음표. 누르면 성과 개요 탭의 "지표 읽는 법" 으로 데려간다.
// 떠 있는 말풍선 대신 이동을 택한 이유: Panel 에 overflow: hidden 이 걸려 있어
// 패널 안에서 띄운 말풍선은 잘리고, 좁은 화면에서는 위치 계산도 어긋난다.
export function ExplainButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`${label} 설명 보기`}
      title={`${label} 설명 보기`}
      style={{
        width: 15,
        height: 15,
        lineHeight: "13px",
        fontSize: 10,
        fontWeight: 700,
        fontFamily: SANS,
        color: C.muted,
        background: "transparent",
        border: `1px solid ${C.dim}`,
        borderRadius: R.pill,
        cursor: "pointer",
        padding: 0,
        flexShrink: 0,
      }}
    >
      ?
    </button>
  );
}

export function Kpi({
  label,
  value,
  sub,
  color,
  onExplain,
}: {
  label: string;
  value: ReactNode;
  sub?: string;
  color?: string;
  onExplain?: () => void;
}) {
  return (
    <div
      style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: R.card }}
      className="p-4 flex flex-col gap-1"
    >
      <span
        style={{ fontFamily: SANS, color: C.muted, fontSize: 11, letterSpacing: "0.01em" }}
        className="flex items-center gap-1.5"
      >
        {label}
        {onExplain && <ExplainButton label={label} onClick={onExplain} />}
      </span>
      <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 600, color: color ?? C.bright, lineHeight: 1.15 }}>
        {value}
      </span>
      {sub && <span style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}>{sub}</span>}
    </div>
  );
}

export type Col = { key: string; label: string; align: "left" | "right" };

export function DataTable({ cols, children, minWidth = 620 }: { cols: Col[]; children: ReactNode; minWidth?: number }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", minWidth }}>
        <thead>
          <tr style={{ borderBottom: `1px solid ${C.border}` }}>
            {cols.map((c) => (
              <th
                key={c.key}
                scope="col"
                style={{
                  fontFamily: MONO,
                  fontSize: 10,
                  color: C.muted,
                  letterSpacing: "0.02em",
                  padding: "10px 14px",
                  textAlign: c.align,
                  fontWeight: 500,
                  whiteSpace: "nowrap",
                }}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Cel({
  children,
  align = "right",
  color,
  bold,
}: {
  children: ReactNode;
  align?: "left" | "right";
  color?: string;
  bold?: boolean;
}) {
  return (
    <td
      style={{
        padding: "11px 14px",
        textAlign: align,
        fontFamily: MONO,
        fontSize: 13,
        fontWeight: bold ? 600 : 400,
        color: color ?? C.text,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </td>
  );
}

export function Tag({ text, color }: { text: string; color: string }) {
  return (
    <span
      style={{
        fontFamily: MONO,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: "0.02em",
        padding: "2px 9px",
        color,
        background: `${color}14`,
        border: `1px solid ${color}40`,
        borderRadius: R.pill,
        whiteSpace: "nowrap",
      }}
    >
      {text}
    </span>
  );
}


export function KeyValue({ rows }: { rows: [string, string, string?][] }) {
  return (
    <div className="flex flex-col gap-2">
      {rows.map(([k, v, color]) => (
        <div key={k} className="flex items-center justify-between gap-4">
          <span style={{ fontFamily: SANS, fontSize: 13, color: C.muted }}>{k}</span>
          <span style={{ fontFamily: MONO, fontSize: 13, fontWeight: 500, color: color ?? C.text }}>{v}</span>
        </div>
      ))}
    </div>
  );
}

