// 재현성 비교(/repro) — 같은 전략서를 두 번 돌린 실행 둘을 결정 단위로 맞대 본다.
//
// 재료는 GET /backtest/runs/{run_id} 의 decisions(리밸런싱마다 target · cash)와 series(자산곡선).
// 실행 기록에는 체결 주문이 따로 남지 않는다. 그래서 "주문"은 직전 결정 대비 목표비중 변화(Δ)로 본다 —
// 같은 곡선 · 같은 목표비중이면 같은 주문이 나온다는 것이 러너의 성질이고, 화면에도 그렇게 적는다.
//
// 재현성은 "같다"를 주장하는 것이라 허용오차를 거의 두지 않는다(부동소수 표기 차이만 흡수).
// node 의 타입 제거 실행(scripts/check-demo.ts)이 읽을 수 있게 런타임 import 를 두지 않는다.

export const TOLERANCE = 1e-12;

export type RunForRepro = {
  run_id: number;
  spec_id: string;
  status: string | null;
  data_snapshot_asof: string | null;
  series: { date: string; strategy: number }[] | null;
  decisions: { date: string; target: Record<string, number>; cash: number }[] | null;
};

export type ReproRow = {
  index: number;
  date: string | null;
  dateA: string | null;
  dateB: string | null;
  targetA: Record<string, number> | null;
  targetB: Record<string, number> | null;
  orderA: Record<string, number> | null;
  orderB: Record<string, number> | null;
  cashA: number | null;
  cashB: number | null;
  // 이 행에서 값이 다른 종목(목표비중 또는 주문). 날짜 · 현금이 다르면 "날짜" · "현금" 도 들어간다.
  diffKeys: string[];
  same: boolean;
};

export type ReproComparison = {
  rows: ReproRow[];
  nA: number;
  nB: number;
  diffRows: number;
  sameWeights: boolean;
  sameOrders: boolean;
  curve: { nA: number; nB: number; diffPoints: number; same: boolean };
};

const close = (a: number | undefined, b: number | undefined) =>
  a !== undefined && b !== undefined && Math.abs(a - b) <= TOLERANCE;

// 결정 i 의 주문 = target_i − target_{i−1}. 첫 결정은 빈 포트폴리오(현금 100%)에서 시작한다.
export function ordersOf(decisions: { target: Record<string, number> }[]): Record<string, number>[] {
  return decisions.map((d, i) => {
    const prev = i === 0 ? {} : decisions[i - 1].target;
    const keys = new Set([...Object.keys(prev), ...Object.keys(d.target)]);
    const out: Record<string, number> = {};
    for (const k of [...keys].sort()) out[k] = (d.target[k] ?? 0) - (prev[k] ?? 0);
    return out;
  });
}

function diffRecord(a: Record<string, number> | null, b: Record<string, number> | null): string[] {
  if (!a || !b) return ["행 없음"];
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...keys].filter((k) => !close(a[k], b[k])).sort();
}

export function compareRuns(a: RunForRepro, b: RunForRepro): ReproComparison {
  const da = a.decisions ?? [];
  const db = b.decisions ?? [];
  const oa = ordersOf(da);
  const ob = ordersOf(db);
  const n = Math.max(da.length, db.length);
  const rows: ReproRow[] = [];
  let weightDiff = false;
  let orderDiff = false;
  for (let i = 0; i < n; i++) {
    const x = da[i];
    const y = db[i];
    const diff = new Set<string>();
    if (!x || !y) diff.add("행 없음");
    if (x && y && x.date !== y.date) diff.add("날짜");
    if (x && y && !close(x.cash, y.cash)) diff.add("현금");
    const wd = diffRecord(x?.target ?? null, y?.target ?? null);
    const od = diffRecord(oa[i] ?? null, ob[i] ?? null);
    if (wd.length) weightDiff = true;
    if (od.length) orderDiff = true;
    for (const k of [...wd, ...od]) diff.add(k);
    rows.push({
      index: i + 1,
      date: x?.date ?? y?.date ?? null,
      dateA: x?.date ?? null,
      dateB: y?.date ?? null,
      targetA: x?.target ?? null,
      targetB: y?.target ?? null,
      orderA: oa[i] ?? null,
      orderB: ob[i] ?? null,
      cashA: x?.cash ?? null,
      cashB: y?.cash ?? null,
      diffKeys: [...diff],
      same: diff.size === 0,
    });
  }

  const sa = a.series ?? [];
  const sb = b.series ?? [];
  let diffPoints = Math.abs(sa.length - sb.length);
  for (let i = 0; i < Math.min(sa.length, sb.length); i++) {
    if (sa[i].date !== sb[i].date || !close(sa[i].strategy, sb[i].strategy)) diffPoints++;
  }

  const diffRows = rows.filter((r) => !r.same).length;
  return {
    rows,
    nA: da.length,
    nB: db.length,
    diffRows,
    sameWeights: !weightDiff && da.length === db.length,
    sameOrders: !orderDiff && da.length === db.length,
    curve: { nA: sa.length, nB: sb.length, diffPoints, same: diffPoints === 0 && sa.length > 0 },
  };
}

// sample_orders 한 줄을 두 실행 열로 나눈다. 파일 모양이 정해지지 않아 흔한 짝 이름을 차례로 본다.
// 짝이 없으면 null — 화면은 한 목록으로 그리고 "두 실행 공통" 이라고 적는다.
const PAIRS: [string, string][] = [
  ["a", "b"],
  ["run_a", "run_b"],
  ["left", "right"],
  ["first", "second"],
];

export function splitSampleOrder(
  item: unknown,
  runIds: (number | null)[] | null,
): { common: Record<string, unknown>; a: unknown; b: unknown } | null {
  if (!item || typeof item !== "object" || Array.isArray(item)) return null;
  const rec = item as Record<string, unknown>;
  const candidates = [...PAIRS];
  if (runIds?.[0] != null && runIds?.[1] != null) candidates.unshift([String(runIds[0]), String(runIds[1])]);
  for (const [ka, kb] of candidates) {
    if (ka in rec && kb in rec) {
      const common = Object.fromEntries(Object.entries(rec).filter(([k]) => k !== ka && k !== kb));
      return { common, a: rec[ka], b: rec[kb] };
    }
  }
  return null;
}

// 확정 모양(2026-10-27): sample_orders = [{date, a: {티커: 비중변화}, b: {티커: 비중변화}}].
// a · b 가 둘 다 숫자 사전이면 종목별로 맞대 다른 종목을 돌려준다. 아니면 null(일반 표시로 넘긴다).
function numRecord(v: unknown): Record<string, number> | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const entries = Object.entries(v as Record<string, unknown>);
  if (!entries.length || !entries.every(([, x]) => typeof x === "number" && Number.isFinite(x))) return null;
  return Object.fromEntries(entries) as Record<string, number>;
}

export function deltaPair(
  a: unknown,
  b: unknown,
): { a: Record<string, number>; b: Record<string, number>; diffKeys: string[] } | null {
  const x = numRecord(a);
  const y = numRecord(b);
  if (!x || !y) return null;
  return { a: x, b: y, diffKeys: diffRecord(x, y) };
}
