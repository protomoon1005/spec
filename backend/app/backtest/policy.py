"""운용 정책 — 백테스트가 쓰는 오프라인 사본.

**정본은 db/seeds 이고, 서버는 app/repositories/presets.py 로 읽는다** (2026-09-20,
M1 2단계). 이 파일을 남겨 둔 이유는 백테스트가 데이터베이스 없이 돌아야 하기
때문이다 — 고정된 표 파일만 있으면 결과가 항상 같고, 팀원이 postgres 를 띄우지
않아도 돌릴 수 있다. lib/policy.ts(타입스크립트 러너·프론트)도 같은 이유로 남아 있다.

어긋나면 자동으로 잡힌다.
  데이터베이스 ↔ 이 파일   tests/test_presets_repo.py 의 사본 대조
  이 파일 ↔ policy.ts      tests/test_backtest_parity.py (값이 틀리면 주문이 달라진다)

세 벌을 한 곳으로 합칠지, 합친다면 파일을 생성해서 둘지 런타임에 읽을지는
백테스트 담당과 정할 문제다. 브라우저와 오프라인 스크립트는 데이터베이스를 직접
읽을 수 없어서 "전부 DB 조회" 가 자명한 답은 아니다.
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
