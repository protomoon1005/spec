"use client";

import { METRIC_GUIDES } from "@/lib/data";
import { C, MONO, R, SANS } from "./tokens";

export function MetricGuideCards({ highlight }: { highlight: string | null }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 14 }}>
      {METRIC_GUIDES.map((g) => (
        <div
          key={g.id}
          id={`guide-${g.id}`}
          style={{
            background: "#0e141b",
            border: `1px solid ${highlight === g.id ? C.accent : C.border}`,
            borderRadius: R.inner,
            padding: "14px 16px",
            transition: "border-color 0.4s",
          }}
        >
          <div className="flex items-baseline gap-2" style={{ marginBottom: 10 }}>
            <span style={{ fontFamily: SANS, fontSize: 14, fontWeight: 600, color: C.bright }}>{g.title}</span>
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.muted }}>{g.english}</span>
          </div>

          <p style={{ fontFamily: SANS, fontSize: 13, color: C.text, lineHeight: 1.7, marginBottom: 10 }}>
            {g.summary}
          </p>

          <div
            style={{
              fontFamily: MONO,
              fontSize: 11,
              color: C.accent,
              background: "#0b0f14",
              border: `1px solid ${C.border}`,
              borderRadius: 8,
              padding: "8px 10px",
              lineHeight: 1.6,
              marginBottom: 10,
              wordBreak: "keep-all",
            }}
          >
            {g.formula}
          </div>

          <p style={{ fontFamily: SANS, fontSize: 13, color: C.text, lineHeight: 1.7, marginBottom: 8 }}>
            {g.reading}
          </p>

          <p style={{ fontFamily: SANS, fontSize: 12, color: C.muted, lineHeight: 1.7, margin: 0 }}>
            <b style={{ color: C.warn, fontWeight: 600 }}>한계</b> {g.caveat}
          </p>
        </div>
      ))}
    </div>
  );
}
