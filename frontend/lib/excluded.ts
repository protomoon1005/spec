// 전략 컴파일이 "요청했지만 담지 못한" 종목 · 자산군과 그 사유.
// 서버(M1)가 completed 결과에 excluded: [{ticker, name, reason_code, reason}] 로 싣는다.
// 자산군을 통째로 뺀 경우는 ticker 가 null 이고 name 이 "주식형" 같은 묶음 이름이다.
// 모양을 서버가 보장하지만 옛 결과 · 샘플 파일에는 없을 수 있어 방어적으로 읽는다.
// node 의 타입 제거 실행이 읽을 수 있게 런타임 import 를 두지 않는다.

export type ExcludedItem = { ticker: string | null; name: string; reason_code: string | null; reason: string };

export function readExcluded(v: unknown): ExcludedItem[] {
  if (!Array.isArray(v)) return [];
  const out: ExcludedItem[] = [];
  for (const x of v) {
    if (!x || typeof x !== "object") continue;
    const r = x as Record<string, unknown>;
    const ticker = typeof r.ticker === "string" && r.ticker ? r.ticker : null;
    const name = typeof r.name === "string" && r.name ? r.name : ticker;
    if (!name) continue;
    out.push({
      ticker,
      name,
      reason_code: typeof r.reason_code === "string" && r.reason_code ? r.reason_code : null,
      reason: typeof r.reason === "string" && r.reason ? r.reason : "사유가 기록되지 않았습니다",
    });
  }
  return out;
}
