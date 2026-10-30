// "요청했지만 담지 못한 종목" 칸. 전략 완료 화면과 /samples 펼침 행이 같이 쓴다.
// 목록이 비면 아무것도 그리지 않는다(칸 숨김).

import type { ExcludedItem } from "@/lib/excluded";

export default function ExcludedList({ items }: { items: ExcludedItem[] }) {
  if (!items.length) return null;
  return (
    <span style={{ display: "grid", gap: 6 }}>
      {items.map((x, i) => (
        <span key={`${x.ticker ?? x.name}-${i}`} style={{ display: "grid", gap: 1 }}>
          <span>
            {x.ticker ? (
              <span className="mono" style={{ color: "var(--c-muted)", marginRight: 8 }}>
                {x.ticker}
              </span>
            ) : (
              <span className="flow-badge muted" style={{ marginRight: 8 }}>
                자산군
              </span>
            )}
            <b style={{ fontWeight: 600 }}>{x.name}</b>
            {x.reason_code && (
              <span className="mono" style={{ color: "var(--c-dim)", marginLeft: 8, fontSize: 11 }}>
                {x.reason_code}
              </span>
            )}
          </span>
          <span className="flow-hint" style={{ fontSize: 12 }}>
            {x.reason}
          </span>
        </span>
      ))}
    </span>
  );
}
