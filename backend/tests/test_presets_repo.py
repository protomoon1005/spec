"""허용범위 프리셋·그룹 상한·하드캡 조회 (M1 2단계).

postgres 와 시드 네 개(기준표 v0.1)가 적재된 상태여야 한다.

## 이 파일의 절반은 "사본이 어긋나지 않았는가" 를 본다

같은 수치가 세 군데에 있다 — db/seeds(정본) · backtest/policy.py · frontend/lib/policy.ts.
백테스트가 데이터베이스 없이 돌게 하려고 일부러 남긴 사본이라 지우지 않는다.
대신 데이터베이스와 파이썬 사본이 같은지를 여기서 고정한다.
타입스크립트 사본은 두 러너 대조 테스트(test_backtest_parity)가 잡는다 —
값이 틀어지면 두 러너의 주문이 달라져 그쪽이 먼저 실패한다.
"""
from __future__ import annotations

import pytest

from app.backtest import policy
from app.repositories import presets

RISK_LEVELS = (1, 2, 3, 4, 5)
GRADES = ("G1", "G2", "G3", "G4", "G5", "G6")


# --- 조회 ------------------------------------------------------------------


def test_active_preset_version_is_seeded(engine):
    assert presets.active_preset_version() == "v0.1"


def test_bounds_cover_every_grade(engine):
    for risk_level in RISK_LEVELS:
        bounds = presets.get_asset_bounds(risk_level)
        assert tuple(sorted(bounds)) == GRADES, risk_level


def test_bounds_carry_preset_id(engine):
    """전략서를 저장할 때 종목마다 '어느 칸을 적용했는지' 를 남겨야 한다."""
    bounds = presets.get_asset_bounds(3)

    assert all(bound.preset_id > 0 for bound in bounds.values())
    assert len({bound.preset_id for bound in bounds.values()}) == len(GRADES)


def test_conservative_profile_cannot_hold_high_risk_products(engine):
    """안정투자형에게 G1~G3 상한이 0 이라는 것이 확정 수치다 —
    레버리지·인버스가 그 성향에게는 생성 자체로 불가능하다는 뜻이다."""
    bounds = presets.get_asset_bounds(1)

    assert bounds["G1"].allowed_max == 0.00
    assert bounds["G2"].allowed_max == 0.00
    assert bounds["G3"].allowed_max == 0.00
    assert bounds["G6"].allowed_max == 1.00


def test_unknown_preset_version_fails_loud(engine):
    """빈 사전을 돌려주면 안 된다 — 호출부가 "상한이 없다 = 제한 없음" 으로 읽으면
    막아야 할 것을 통과시킨다. 안전 상한이라 없으면 없다고 터져야 한다."""
    with pytest.raises(LookupError):
        presets.get_asset_bounds(3, preset_version="v9.9")
    with pytest.raises(LookupError):
        presets.get_group_caps(3, preset_version="v9.9")


def test_unknown_risk_level_fails_loud(engine):
    with pytest.raises(LookupError):
        presets.get_asset_bounds(9)


def test_group_caps_mix_profile_specific_and_fixed(engine):
    caps = presets.get_group_caps(3)

    # 자산군은 성향마다 다르다.
    assert caps["EQUITY"] == 0.60
    assert caps["BOND"] == 0.50
    assert caps["COMMODITY"] == 0.15
    # 업종·국가는 성향과 무관하게 고정이다.
    assert caps["SECTOR_SEMICONDUCTOR"] == 0.30
    assert caps["COUNTRY_KR"] == 0.50


def test_equity_cap_rises_with_risk_level(engine):
    equity = [presets.get_group_caps(level)["EQUITY"] for level in RISK_LEVELS]
    bond = [presets.get_group_caps(level)["BOND"] for level in RISK_LEVELS]

    assert equity == sorted(equity)
    assert bond == sorted(bond, reverse=True)


def test_unclassified_bucket_is_returned_but_flagged(engine):
    """분류되지 않음 묶음도 데이터베이스 값 그대로 돌려준다. 숨기지 않는다 —
    대신 적용하는 쪽이 빼고 써야 한다는 것을 상수로 드러낸다."""
    caps = presets.get_group_caps(3)

    assert "SECTOR_OTHER" in caps
    assert "SECTOR_OTHER" in presets.CAP_EXEMPT_GROUPS


def test_active_hardcap_is_seeded(engine):
    hardcap = presets.get_active_hardcap()

    assert hardcap is not None
    assert hardcap["hardcap_version"] == "v0.1"
    assert hardcap["leverage_allowed"] is False


# --- 사본 대조 --------------------------------------------------------------


@pytest.mark.parametrize("risk_level", RISK_LEVELS)
def test_db_bounds_match_python_copy(engine, risk_level):
    from_db = {
        grade: bound.allowed_max
        for grade, bound in presets.get_asset_bounds(risk_level).items()
    }

    assert from_db == policy.PRESET_GRADE_CAP[risk_level]


@pytest.mark.parametrize("risk_level", RISK_LEVELS)
def test_db_group_caps_match_python_copy(engine, risk_level):
    caps = presets.get_group_caps(risk_level)

    for group, by_level in policy.ASSET_CAP.items():
        assert caps[group] == by_level[risk_level], group
    for group in policy.SECTOR_GROUPS:
        assert caps[group] == policy.SECTOR_CAP, group
    for group in policy.COUNTRY_GROUPS:
        assert caps[group] == policy.COUNTRY_CAP, group


def test_db_hardcap_matches_python_copy(engine):
    hardcap = presets.get_active_hardcap()

    assert hardcap["hardcap_version"] == policy.HARDCAP["version"]
    for key in ("max_weight_per_asset", "cash_min", "max_loss_per_trade", "max_drawdown"):
        assert hardcap[key] == policy.HARDCAP[key], key
    assert hardcap["min_interval_days"] == policy.HARDCAP["min_interval_days"]
    assert hardcap["leverage_allowed"] == policy.HARDCAP["leverage_allowed"]


def test_exempt_groups_match_python_copy(engine):
    assert set(presets.CAP_EXEMPT_GROUPS) == set(policy.CAP_EXEMPT_GROUPS)


def test_profile_defaults_match_python_copy(engine):
    """성향별 현금 하한도 사본이 둘이다 (risk_profile_defaults ↔ policy.CASH_MIN)."""
    from app.repositories import profiles

    version = presets.active_preset_version()
    for risk_level in RISK_LEVELS:
        defaults = profiles.get_profile_defaults(version, risk_level)
        assert defaults["cash_min_default"] == policy.CASH_MIN[risk_level], risk_level
