"use client";

import { C, R } from "./tokens";

export const TRACK = 0.4;

export function WeightBand({
  min,
  max,
  policyCap,
  raw,
  final,
}: {
  min: number;
  max: number;
  policyCap: number;
  raw: number;
  final: number;
}) {
  const p = (v: number) => `${Math.min(100, (v / TRACK) * 100)}%`;
  return (
    <div
      style={{
        position: "relative",
        height: 24,
        minWidth: 190,
        background: "#0e141b",
        border: `1px solid ${C.border}`,
        borderRadius: R.band,
        overflow: "hidden",
      }}
    >
      {/* Spec 허용밴드 */}
      <div
        style={{
          position: "absolute",
          left: p(min),
          width: `calc(${p(max)} - ${p(min)})`,
          top: 0,
          bottom: 0,
          background: "rgba(91,157,240,0.12)",
          borderLeft: `1px solid ${C.accent}55`,
          borderRight: `1px solid ${C.accent}55`,
        }}
      />
      {/* 성향 상한 */}
      {policyCap <= TRACK && (
        <div style={{ position: "absolute", left: p(policyCap), top: 0, bottom: 0, width: 1, background: C.warn, opacity: 0.85 }} />
      )}
      {/* 신호 기준 비중 (캡 적용 전) */}
      <div style={{ position: "absolute", left: p(raw), top: 3, bottom: 3, width: 1, background: C.muted }} />
      {/* 최종 목표비중 */}
      <div
        style={{
          position: "absolute",
          left: 0,
          width: p(final),
          top: 8,
          bottom: 8,
          background: C.accent,
          borderRadius: R.pill,
        }}
      />
      <div style={{ position: "absolute", left: `calc(${p(final)} - 1px)`, top: 1, bottom: 1, width: 2, background: C.bright }} />
    </div>
  );
}

