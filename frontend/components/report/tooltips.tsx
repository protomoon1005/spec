"use client";

import type { ReactNode } from "react";
import { monthlyReturns, SNAPSHOT, won } from "@/lib/data";
import { C, MONO, R, SANS, sign } from "./tokens";

export function TipShell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      style={{
        background: C.surface2,
        border: `1px solid ${C.grid}`,
        borderRadius: R.inner,
        padding: "9px 13px",
      }}
    >
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted, marginBottom: 5 }}>{label}</div>
      {children}
    </div>
  );
}

export type TipProps = { active?: boolean; label?: string | number; payload?: { name?: string; value?: number; color?: string }[] };

export function EquityTip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;

  // 그 시점에 앞서 있는 쪽이 위로 온다. 목록 순서가 실제 우열과 어긋나면
  // 어느 쪽이 이기고 있는지 툴팁만 보고는 알 수 없다.
  const ranked = [...payload].sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  const lead = (ranked[0]?.value ?? 0) - (ranked[ranked.length - 1]?.value ?? 0);

  return (
    <TipShell label={String(label)}>
      {ranked.map((p, i) => (
        <div
          key={p.name}
          style={{
            fontFamily: MONO,
            fontSize: 12,
            color: p.color,
            fontWeight: i === 0 ? 600 : 400,
            marginBottom: 2,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <span style={{ width: 6, height: 6, borderRadius: R.pill, background: p.color, flexShrink: 0 }} />
          {p.name}: {won(p.value ?? 0)}
        </div>
      ))}
      {ranked.length > 1 && lead > 0 && (
        <div
          style={{
            fontFamily: MONO,
            fontSize: 11,
            color: C.muted,
            marginTop: 5,
            paddingTop: 5,
            borderTop: `1px solid ${C.border}`,
          }}
        >
          {ranked[0].name} 우세 · {won(lead)}
        </div>
      )}
    </TipShell>
  );
}

export function DdTip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <TipShell label={String(label)}>
      <div style={{ fontFamily: MONO, fontSize: 12, color: C.loss }}>
        낙폭 {(payload[0].value ?? 0).toFixed(2)}%
      </div>
    </TipShell>
  );
}

export function MonthTip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;
  const v = payload[0].value ?? 0;
  // 워크포워드 차트와 공유하는 툴팁이다. 거기 라벨은 "WF-1" 이라 찾히지 않는다.
  const partial = monthlyReturns.find((m) => m.month === label)?.partial ?? false;
  return (
    <TipShell label={String(label)}>
      <div style={{ fontFamily: MONO, fontSize: 12, color: sign(v) }}>
        {v >= 0 ? "+" : "−"}
        {Math.abs(v).toFixed(2)}%
      </div>
      {partial && (
        <div style={{ fontFamily: SANS, fontSize: 11, color: C.warn, marginTop: 4 }}>
          진행 중 · {SNAPSHOT} 까지
        </div>
      )}
    </TipShell>
  );
}

export function ViewTip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <TipShell label={String(label)}>
      {payload.map((p) => (
        <div key={p.name} style={{ fontFamily: MONO, fontSize: 12, color: p.color, marginBottom: 2 }}>
          {p.name}: {((p.value ?? 0) * 100).toFixed(1)}%
        </div>
      ))}
    </TipShell>
  );
}

export const axisTick = { fontFamily: MONO, fontSize: 10, fill: C.muted };

