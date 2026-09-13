"""운용 정책 단일 출처 (파이썬 쪽).

lib/policy.ts 와 같은 값을 담는다. 원본은 현서님 db/seeds 이고, 최종적으로는
DB 에서 읽어야 한다 — 그때는 이 파일의 상수만 조회 함수로 바꾸면 된다.
"""
from __future__ import annotations

# db/seeds/01_hardcap_v0_1.sql
HARDCAP = {
    "version": "v0.1",
    "max_weight_per_asset": 0.30,
    "cash_min": 0.05,
    "max_loss_per_trade": 0.05,
    "max_drawdown": 0.25,
    "min_interval_days": 5,
    "leverage_allowed": False,
}

# db/seeds/02_preset_v0_1.sql · asset_bound_presets
PRESET_GRADE_CAP: dict[int, dict[str, float]] = {
    1: {"G1": 0.00, "G2": 0.00, "G3": 0.00, "G4": 0.10, "G5": 0.30, "G6": 1.00},
    2: {"G1": 0.00, "G2": 0.00, "G3": 0.10, "G4": 0.25, "G5": 0.40, "G6": 1.00},
    3: {"G1": 0.00, "G2": 0.10, "G3": 0.25, "G4": 0.35, "G5": 0.50, "G6": 1.00},
    4: {"G1": 0.10, "G2": 0.25, "G3": 0.35, "G4": 0.40, "G5": 0.60, "G6": 1.00},
    5: {"G1": 0.25, "G2": 0.35, "G3": 0.40, "G4": 0.50, "G5": 0.70, "G6": 1.00},
}

PROFILE_LABEL = {1: "안정투자형", 2: "안정추구형", 3: "위험중립형", 4: "성장투자형", 5: "공격투자형"}
CASH_MIN = {1: 0.20, 2: 0.15, 3: 0.10, 4: 0.05, 5: 0.05}

# db/seeds/03_asset_groups.sql · group_caps
ASSET_CAP = {
    "EQUITY": {1: 0.25, 2: 0.40, 3: 0.60, 4: 0.75, 5: 0.85},
    "BOND": {1: 0.70, 2: 0.60, 3: 0.50, 4: 0.35, 5: 0.25},
    "COMMODITY": {1: 0.05, 2: 0.10, 3: 0.15, 4: 0.20, 5: 0.25},
}
SECTOR_CAP = 0.30
COUNTRY_CAP = 0.50
SECTOR_GROUPS = [
    "SECTOR_SEMICONDUCTOR", "SECTOR_BATTERY", "SECTOR_BIOHEALTH",
    "SECTOR_FINANCE", "SECTOR_INTERNET_PLATFORM", "SECTOR_CONSUMER",
]
COUNTRY_GROUPS = ["COUNTRY_KR", "COUNTRY_US", "COUNTRY_OTHER"]

# SECTOR_OTHER 는 산업 섹터가 아니라 미분류 버킷이다. 시장대표·국채·원자재가
# 전부 여기로 들어와서 30% 캡을 그대로 걸면 정상 포트폴리오가 항상 걸린다.
CAP_EXEMPT_GROUPS = {"SECTOR_OTHER"}


def profile_for(risk_level: int) -> dict:
    return {
        "label": PROFILE_LABEL[risk_level],
        "risk_level": risk_level,
        "cash_min": CASH_MIN[risk_level],
        "grade_cap": PRESET_GRADE_CAP[risk_level],
    }


def caps_for(risk_level: int) -> dict[str, float]:
    caps = {ag: ASSET_CAP[ag][risk_level] for ag in ASSET_CAP}
    caps.update({g: SECTOR_CAP for g in SECTOR_GROUPS})
    caps.update({g: COUNTRY_CAP for g in COUNTRY_GROUPS})
    return caps


def resolve_bounds(holdings: list[dict], grade_cap: dict[str, float]) -> dict[str, tuple[float, float]]:
    """Validator 2단(프리셋) + 4단(하드캡)을 순서대로 적용한 확정 밴드."""
    out = {}
    for h in holdings:
        cap = min(h["max_raw"], grade_cap.get(h["grade"], 1.0), HARDCAP["max_weight_per_asset"])
        out[h["ticker"]] = (min(h["min_raw"], cap), cap)
    return out
