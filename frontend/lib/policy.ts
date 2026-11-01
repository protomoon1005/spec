// 운용 정책 단일 출처.
//
// 프리셋 등급표·현금 하한·그룹 상한·하드캡은 원래 현서님 db/seeds 에 있는 값이다.
// 백테스트 러너와 화면이 각자 베껴 쓰면 한쪽만 고쳤을 때 조용히 어긋나므로
// (러너는 A 로 계산했는데 화면은 B 로 설명하는 상황) 여기 한 곳에만 적는다.
//
// 나중에 DB 에서 읽어오게 되면 이 파일의 상수만 조회 함수로 바꾸면 된다.

export type Grade = "G1" | "G2" | "G3" | "G4" | "G5" | "G6";
export type AssetGroup = "EQUITY" | "BOND" | "COMMODITY";

export type Profile = {
  label: string;
  riskLevel: number;
  cashMin: number;
  gradeCap: Record<Grade, number>;
};

export type Caps = {
  /** group_id -> 합계 상한 */
  group: Record<string, number>;
  /** 캡을 적용하지 않을 group_id (미분류 버킷 등) */
  exempt: string[];
};

export type Bound = { min: number; max: number; clampedBy: string | null };

/** resolveBounds 가 필요로 하는 최소 정보. Holding 이 이 모양을 만족한다. */
export type BoundInput = {
  ticker: string;
  grade: Grade;
  weightMinRaw: number;
  weightMaxRaw: number;
};

// --- db/seeds/01_hardcap_v0_1.sql · hardcap_versions ------------------------
// 주의: 시드 파일에 "팀 확정 전 보수적 기본값 / TODO" 로 적혀 있는 값이다.
export const HARDCAP = {
  version: "v0.1",
  maxWeightPerAsset: 0.3,
  cashMin: 0.05,
  maxLossPerTrade: 0.05,
  maxDrawdown: 0.25,
  minIntervalDays: 5,
  leverageAllowed: false,
};

// --- db/seeds/02_preset_v0_1.sql · asset_bound_presets ----------------------
export const PRESET_GRADE_CAP: Record<number, Record<Grade, number>> = {
  1: { G1: 0, G2: 0, G3: 0, G4: 0.1, G5: 0.3, G6: 1 },
  2: { G1: 0, G2: 0, G3: 0.1, G4: 0.25, G5: 0.4, G6: 1 },
  3: { G1: 0, G2: 0.1, G3: 0.25, G4: 0.35, G5: 0.5, G6: 1 },
  4: { G1: 0.1, G2: 0.25, G3: 0.35, G4: 0.4, G5: 0.6, G6: 1 },
  5: { G1: 0.25, G2: 0.35, G3: 0.4, G4: 0.5, G5: 0.7, G6: 1 },
};

// --- db/seeds/02_preset_v0_1.sql · risk_profile_defaults --------------------
export const PROFILE_LABEL: Record<number, string> = {
  1: "안정투자형",
  2: "안정추구형",
  3: "위험중립형",
  4: "성장투자형",
  5: "공격투자형",
};

export const CASH_MIN: Record<number, number> = { 1: 0.2, 2: 0.15, 3: 0.1, 4: 0.05, 5: 0.05 };

// --- db/seeds/03_asset_groups.sql · group_caps ------------------------------
/** 레벨1 자산군 — 성향별 상한 */
export const ASSET_CAP: Record<AssetGroup, Record<number, number>> = {
  EQUITY: { 1: 0.25, 2: 0.4, 3: 0.6, 4: 0.75, 5: 0.85 },
  BOND: { 1: 0.7, 2: 0.6, 3: 0.5, 4: 0.35, 5: 0.25 },
  COMMODITY: { 1: 0.05, 2: 0.1, 3: 0.15, 4: 0.2, 5: 0.25 },
};

/** 레벨2 섹터·국가 — 성향 무관 고정(risk_level=0) */
export const SECTOR_CAP = 0.3;
export const COUNTRY_CAP = 0.5;

export const SECTOR_GROUPS = [
  "SECTOR_SEMICONDUCTOR",
  "SECTOR_BATTERY",
  "SECTOR_BIOHEALTH",
  "SECTOR_FINANCE",
  "SECTOR_INTERNET_PLATFORM",
  "SECTOR_CONSUMER",
] as const;

export const COUNTRY_GROUPS = ["COUNTRY_KR", "COUNTRY_US", "COUNTRY_OTHER"] as const;

// SECTOR_OTHER 는 산업 섹터가 아니라 미분류 버킷이다. 시장대표·국채·원자재가
// 전부 여기로 들어오기 때문에 30% 캡을 그대로 걸면 정상 포트폴리오가 항상
// 걸린다. 팀 확정 전까지 캡 대상에서 뺀다.
export const CAP_EXEMPT_GROUPS = ["SECTOR_OTHER"];

export const GROUP_LABEL: Record<string, string> = {
  EQUITY: "주식계",
  BOND: "채권계",
  COMMODITY: "원자재계",
  SECTOR_SEMICONDUCTOR: "반도체",
  SECTOR_BATTERY: "2차전지",
  SECTOR_BIOHEALTH: "바이오·헬스케어",
  SECTOR_FINANCE: "금융",
  SECTOR_INTERNET_PLATFORM: "인터넷·플랫폼",
  SECTOR_CONSUMER: "소비재",
  SECTOR_OTHER: "기타",
  COUNTRY_KR: "한국",
  COUNTRY_US: "미국",
  COUNTRY_OTHER: "기타",
};

// --- 조립 -------------------------------------------------------------------

export function profileFor(riskLevel: number): Profile {
  return {
    label: PROFILE_LABEL[riskLevel],
    riskLevel,
    cashMin: CASH_MIN[riskLevel],
    gradeCap: PRESET_GRADE_CAP[riskLevel],
  };
}

export function capsFor(riskLevel: number): Caps {
  const group: Record<string, number> = {
    EQUITY: ASSET_CAP.EQUITY[riskLevel],
    BOND: ASSET_CAP.BOND[riskLevel],
    COMMODITY: ASSET_CAP.COMMODITY[riskLevel],
  };
  for (const g of SECTOR_GROUPS) group[g] = SECTOR_CAP;
  for (const g of COUNTRY_GROUPS) group[g] = COUNTRY_CAP;
  return { group, exempt: [...CAP_EXEMPT_GROUPS] };
}

/**
 * Validator 2단(허용범위 프리셋)과 4단(하드캡 클램프)을 순서대로 적용해
 * 종목별 확정 밴드를 낸다.
 *
 * 러너와 화면이 같은 밴드를 보게 하려고 여기 한 번만 구현한다 — 화면이
 * 다른 밴드를 그리면 "허용 범위가 이랬고 신호가 이래서 그 안 이 지점"이라는
 * 설명 자체가 거짓말이 된다.
 */
export function resolveBounds(
  items: BoundInput[],
  profile: Profile,
  hardcapMaxPerAsset: number = HARDCAP.maxWeightPerAsset,
): Record<string, Bound> {
  const out: Record<string, Bound> = {};
  for (const it of items) {
    const presetCap = profile.gradeCap[it.grade] ?? 1;
    const cap = Math.min(it.weightMaxRaw, presetCap, hardcapMaxPerAsset);
    const clampedBy =
      cap < it.weightMaxRaw - 1e-9 ? (presetCap <= hardcapMaxPerAsset ? "프리셋" : "하드캡") : null;
    out[it.ticker] = { min: Math.min(it.weightMinRaw, cap), max: cap, clampedBy };
  }
  return out;
}
