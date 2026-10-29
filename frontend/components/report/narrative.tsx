"use client";

// 리포트 요약 문단. 문장은 lib/report-narrative.ts 의 템플릿이 만들고, 여기는 그리기만 한다.
// LLM 을 쓰지 않는다. 문장에 들어간 값은 "문장에 쓴 값" 에 칸 이름과 함께 그대로 적는다.

import { useMemo } from "react";

import type { Report as ReportData } from "@/lib/data";
import {
  NARRATIVE_FIELDS,
  narrate,
  narrativeValues,
  type NarrativeField,
  type NarrativeSource,
  type NarrativeValues,
} from "@/lib/report-narrative";
import { Panel } from "./primitives";
import { C, MONO, SANS } from "./tokens";

function show(field: NarrativeField, v: NarrativeValues[NarrativeField]): string {
  if (v === null) return "–";
  const kind = NARRATIVE_FIELDS[field].kind;
  if (kind === "ratio" && typeof v === "number") return `${(v * 100).toFixed(2)}%`;
  if (kind === "number" && typeof v === "number") return v.toFixed(3);
  if (kind === "weights" && typeof v === "object") {
    return Object.entries(v)
      .map(([k, w]) => `${k} ${(w * 100).toFixed(1)}%`)
      .join(" · ");
  }
  return String(v);
}

export function NarrativePanel({ report, run }: { report: ReportData; run: NarrativeSource["run"] }) {
  const values = useMemo(() => narrativeValues({ report, run }), [report, run]);
  const lines = useMemo(() => narrate(values), [values]);
  const used = useMemo(() => [...new Set(lines.flatMap((l) => l.uses))], [lines]);
  if (!lines.length) return null;

  return (
    <Panel title="요약" sub="정해진 문장 틀에 실행 값을 넣은 것 — 생성형 모델을 쓰지 않는다" source={`기준시점 ${report.AS_OF} 종가`}>
      <p style={{ fontFamily: SANS, fontSize: 14, lineHeight: 1.8, color: C.bright, maxWidth: "80ch", wordBreak: "keep-all" }}>
        {lines.map((l) => l.text).join(" ")}
      </p>
      <details style={{ marginTop: 10 }}>
        <summary style={{ cursor: "pointer", fontSize: 12, color: C.muted, fontFamily: SANS }}>
          문장에 쓴 값 {used.length}개
        </summary>
        <dl
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, max-content) minmax(0, 1fr)",
            gap: "4px 16px",
            margin: "8px 0 0",
            fontSize: 12,
          }}
        >
          {used.map((f) => (
            <div key={f} style={{ display: "contents" }}>
              <dt style={{ color: C.muted, fontFamily: SANS }}>{NARRATIVE_FIELDS[f].label}</dt>
              <dd style={{ margin: 0, color: C.text, fontFamily: MONO, overflowWrap: "anywhere" }}>{show(f, values[f])}</dd>
            </div>
          ))}
        </dl>
      </details>
    </Panel>
  );
}
