"use client";

// 허용범위 미리보기 — U02R (데모 계획의 시연 1순위 라이브 화면).
//
// 자연어를 넣기 전에 "내 성향이면 무엇을 얼마까지 담을 수 있는지" 를 먼저 보여 준다.
// 전략이 만들어진 뒤에 제약을 설명하면 변명처럼 들리지만, 만들기 전에 보여 주면 약속이
// 된다. 성향을 내리면 위험한 등급부터 한꺼번에 닫히는 장면이 이 시스템의 "개인화" 를 한
// 번에 보여 주는 지점이다.
//
// 숫자는 전부 GET /bounds 에서 온다(backend/app/routers/bounds.py). 서버가 전략서를 만들 때
// 쓰는 후보 판정을 그대로 돌려주므로, 여기 보이는 범위가 AI 양식에 실제로 들어간다.
// 프론트 정책 사본(lib/policy.ts)으로 다시 계산하지 않는다 — 계산 순서가 바뀌면 화면만
// 어긋나기 때문이다. 처음 열 때는 내 성향(GET /profile/me)을 고르고, 없으면 3.

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import RequireLogin from "@/components/require-login";
import ScaffoldShell from "@/components/scaffold-shell";
import { api, ApiError } from "@/lib/api";
import { GROUP_LABEL, PROFILE_LABEL } from "@/lib/policy";

type Item = {
  ticker: string;
  name: string;
  risk_tag: string | null;
  asset_group_id: string | null;
  sector_group_id: string | null;
  country_group_id: string | null;
  is_leveraged: boolean;
  active: boolean;
  allowed: boolean;
  weight_min: number | null;
  weight_max: number | null;
  hardcap_max: number | null;
  reason: string | null;
};

type Bounds = {
  risk_level: number;
  preset_version: string;
  hardcap_version: string | null;
  max_weight_per_asset: number | null;
  leverage_allowed: boolean | null;
  group_caps: Record<string, number>;
  items: Item[];
};

const LEVELS = [1, 2, 3, 4, 5];
const GRADES = ["G1", "G2", "G3", "G4", "G5", "G6"];
const ASSETS = ["EQUITY", "BOND", "COMMODITY"] as const;
const ASSET_LABEL: Record<string, string> = { EQUITY: "주식", BOND: "채권", COMMODITY: "원자재" };

// 막대의 오른쪽 끝. 하드캡(30%)보다 넉넉히 잡아 하드캡 선이 끝에 붙지 않게 한다.
const TRACK = 0.4;

const pct = (v: number) => `${Math.round(v * 1000) / 10}%`;

export default function BoundsPage() {
  return (
    <ScaffoldShell>
      <main>
        <h1>허용범위 미리보기</h1>
        <p>
          이 화면에서 하는 일: 전략을 만들기 전에, 투자 성향에 따라 어떤 종목을 얼마까지 담을 수 있는지 확인한다.
          여기서 정해진 범위를 벗어나는 전략은 애초에 만들어지지 않는다.
        </p>
        <RequireLogin>{() => <Preview />}</RequireLogin>
      </main>
    </ScaffoldShell>
  );
}

function Preview() {
  const [mine, setMine] = useState<number | null | undefined>(undefined);
  const [level, setLevel] = useState<number | null>(null);
  const [data, setData] = useState<Bounds | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  // 처음엔 내 성향으로. 성향이 없으면 가운데(3).
  useEffect(() => {
    api<{ risk_level: number }>("/profile/me")
      .then((p) => {
        setMine(p.risk_level);
        setLevel(p.risk_level);
      })
      .catch((err) => {
        setMine(null);
        setLevel(3);
        if (!(err instanceof ApiError && err.status === 404)) {
          setMessage(`내 성향을 받지 못해 3으로 엽니다: ${err instanceof Error ? err.message : String(err)}`);
        }
      });
  }, []);

  useEffect(() => {
    if (level === null) return;
    let cancelled = false;
    api<Bounds>(`/bounds?risk_level=${level}`)
      .then((b) => {
        if (!cancelled) setData(b);
      })
      .catch((err) => {
        if (!cancelled) setMessage(`허용범위를 받지 못했습니다: ${err instanceof Error ? err.message : String(err)}`);
      });
    return () => {
      cancelled = true;
    };
  }, [level]);

  return (
    <>
      <div className="flow-levels" role="radiogroup" aria-label="투자 성향">
        {LEVELS.map((n) => (
          <button
            key={n}
            type="button"
            role="radio"
            aria-checked={n === level}
            onClick={() => setLevel(n)}
          >
            <span className="mono">성향 {n}</span>
            <b>{PROFILE_LABEL[n]}</b>
            {n === mine && <span className="flow-badge ok">내 성향</span>}
          </button>
        ))}
      </div>
      {mine === null && (
        <p className="flow-hint" style={{ marginTop: 8 }}>
          아직 성향이 없어 가운데(위험중립형)로 열었어요. <Link href="/survey">성향 설문</Link>을 하면 내 성향으로
          열립니다.
        </p>
      )}
      {message && (
        <p className="flow-msg warn" role="alert">
          {message}
        </p>
      )}

      {data ? <Body data={data} /> : !message && <p className="flow-state">불러오는 중…</p>}
    </>
  );
}

function Body({ data }: { data: Bounds }) {
  const [asset, setAsset] = useState<"ALL" | (typeof ASSETS)[number]>("ALL");
  const [query, setQuery] = useState("");

  const level = data.risk_level;
  const hardcap = data.max_weight_per_asset;
  const open = data.items.filter((i) => i.allowed);
  const perAssetMax = Math.max(0, ...open.map((i) => i.hardcap_max ?? 0));

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return data.items.filter(
      (i) =>
        (asset === "ALL" || i.asset_group_id === asset) &&
        (q === "" || i.name.toLowerCase().includes(q) || i.ticker.includes(q)),
    );
  }, [data, asset, query]);

  return (
    <>
      <div className="flow-limits" style={{ marginTop: 20 }}>
        <div>
          <span>담을 수 있는 종목</span>
          <b className="mono">
            {open.length}
            <small> / {data.items.length}</small>
          </b>
        </div>
        <div>
          <span>한 종목 최대</span>
          <b className="mono">{pct(perAssetMax)}</b>
        </div>
        <div>
          <span>주식 합계 최대</span>
          <b className="mono">{pct(data.group_caps.EQUITY ?? 0)}</b>
        </div>
        <div>
          <span>채권 · 원자재 합계 최대</span>
          <b className="mono">
            {pct(data.group_caps.BOND ?? 0)} · {pct(data.group_caps.COMMODITY ?? 0)}
          </b>
        </div>
      </div>

      <h2>위험등급별 상한</h2>
      <p className="flow-hint" style={{ marginBottom: 12 }}>
        성향을 내리면 변동성이 큰 등급부터 닫혀요. 노란 선은 하드캡 종목당 상한
        {hardcap !== null && ` ${pct(hardcap)}`}이고, 기준표가 그보다 높게 줘도 하드캡이 다시 자릅니다.
      </p>
      <GradeTable items={data.items} hardcap={hardcap} />

      <h2>묶음 상한</h2>
      <GroupCaps caps={data.group_caps} />

      <h2>종목별 허용범위</h2>
      <div className="flow-actions" style={{ marginBottom: 12 }}>
        {(["ALL", ...ASSETS] as const).map((a) => (
          <button key={a} type="button" className="flow-chip" aria-pressed={asset === a} onClick={() => setAsset(a)}>
            {a === "ALL" ? "전체" : ASSET_LABEL[a]}
          </button>
        ))}
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="종목명 또는 코드"
          aria-label="종목 검색"
          style={{ flex: "1 1 180px", maxWidth: 280 }}
        />
      </div>
      {rows.length === 0 ? (
        <div className="flow-empty">
          <strong>조건에 맞는 종목이 없습니다.</strong>
        </div>
      ) : (
        <div className="flow-table">
          <table style={{ minWidth: 620 }}>
            <thead>
              <tr>
                <th>종목</th>
                <th>등급</th>
                <th style={{ width: "28%" }}>담을 수 있는 범위</th>
                <th className="num">상한</th>
                <th>근거</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((i) => (
                <tr key={i.ticker} style={i.allowed ? undefined : { color: "var(--c-dim)" }}>
                  <td>
                    {i.name} <span className="mono flow-hint">{i.ticker}</span>
                  </td>
                  <td>
                    <span className={`flow-badge ${i.allowed ? "muted" : "err"}`}>{i.risk_tag ?? "?"}</span>
                  </td>
                  <td>
                    <BoundBar max={i.hardcap_max ?? 0} hardcap={hardcap} />
                  </td>
                  <td className="num" style={{ color: i.allowed ? "var(--c-bright)" : "var(--c-loss)" }}>
                    {i.allowed && i.hardcap_max !== null ? pct(i.hardcap_max) : "—"}
                  </td>
                  <td className="flow-hint" style={{ fontSize: 12 }}>
                    {reasonText(i, level)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="flow-hint" style={{ marginTop: 10, fontSize: 12 }}>
        기준표 {data.preset_version} · 하드캡 {data.hardcap_version ?? "없음"} · 등급은 종목 원장(etf_master)의
        위험등급이에요. 하드캡 v0.1 수치는 아직 팀 확정 전 임시값입니다.
      </p>

      <div className="flow-next" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <Link href="/profile">← 성향 결과</Link>
        <Link href="/compile">이 범위로 전략 만들기 →</Link>
      </div>
    </>
  );
}

// 0 에서 상한까지가 담을 수 있는 구간. 하드캡 선을 같이 그어 무엇이 잘랐는지 보이게 한다.
function BoundBar({ max, hardcap }: { max: number; hardcap: number | null }) {
  const pos = (v: number) => `${Math.min(100, (v / TRACK) * 100)}%`;
  return (
    <span className="flow-bound" aria-hidden="true">
      {max > 0 ? <span style={{ width: pos(max) }} /> : <em>담을 수 없음</em>}
      {hardcap !== null && <i style={{ left: pos(hardcap) }} />}
    </span>
  );
}

// 등급마다: 기준표가 준 상한, 하드캡을 씌운 상한, 담을 수 있는 종목 수.
// 기준표 상한은 그 등급에서 통과한 종목의 weight_max(서버가 준 값) — 통과 종목이 없으면 닫힌 등급.
function GradeTable({ items, hardcap }: { items: Item[]; hardcap: number | null }) {
  return (
    <div className="flow-table">
      <table style={{ minWidth: 520 }}>
        <thead>
          <tr>
            <th>등급</th>
            <th style={{ width: "40%" }}>범위</th>
            <th className="num">상한</th>
            <th className="num">담을 수 있음</th>
          </tr>
        </thead>
        <tbody>
          {GRADES.map((g) => {
            const ofGrade = items.filter((i) => i.risk_tag === g);
            const open = ofGrade.filter((i) => i.allowed);
            const preset = Math.max(0, ...open.map((i) => i.weight_max ?? 0));
            const capped = Math.max(0, ...open.map((i) => i.hardcap_max ?? 0));
            const cut = open.length > 0 && preset > capped;
            return (
              <tr key={g}>
                <td className="mono">{g}</td>
                <td>
                  <BoundBar max={capped} hardcap={hardcap} />
                </td>
                <td
                  className="num"
                  title={cut ? `기준표 ${pct(preset)} → 하드캡 ${pct(capped)}` : undefined}
                  style={{ color: open.length === 0 ? "var(--c-loss)" : cut ? "var(--c-warn)" : "var(--c-bright)" }}
                >
                  {open.length === 0 ? "닫힘" : pct(capped)}
                  {cut && <span className="flow-hint"> (기준표 {pct(preset)})</span>}
                </td>
                <td className="num">
                  {open.length} / {ofGrade.length}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function GroupCaps({ caps }: { caps: Record<string, number> }) {
  const sectors = Object.keys(caps).filter((g) => g.startsWith("SECTOR_") && g !== "SECTOR_OTHER");
  const countries = Object.keys(caps).filter((g) => g.startsWith("COUNTRY_"));
  const line = (ids: readonly string[]) => ids.map((g) => `${GROUP_LABEL[g] ?? g} ${pct(caps[g])}`).join(" · ");
  return (
    <>
      <dl className="flow-kv">
        <dt>자산군 — 성향에 따라 바뀜</dt>
        <dd>{line(ASSETS.filter((a) => a in caps))}</dd>
        <dt>업종 — 성향과 무관</dt>
        <dd>{line(sectors)}</dd>
        <dt>국가 — 성향과 무관</dt>
        <dd>{line(countries)}</dd>
      </dl>
      <p className="flow-hint" style={{ marginTop: 8, fontSize: 12 }}>
        업종 &ldquo;기타&rdquo;는 상한에서 빼요. 시장대표 · 국채 · 원자재가 전부 여기로 들어와 정상 포트폴리오도 늘
        걸리기 때문입니다(팀 확정 전 임시 조치).
      </p>
    </>
  );
}

// 서버 사유는 backend/app/m1/candidates.py 의 REASON_* 문장이다. 성향 등급 사유만 성향 이름으로 바꿔 쓴다.
const REASON_PRESET_ZERO = "이 성향은 담을 수 없는 위험등급이다";

function reasonText(i: Item, level: number): string {
  if (!i.allowed) return i.reason === REASON_PRESET_ZERO ? `${PROFILE_LABEL[level]} 미허용` : (i.reason ?? "");
  if (i.weight_max !== null && i.hardcap_max !== null && i.hardcap_max < i.weight_max) return "하드캡";
  return "기준표";
}
