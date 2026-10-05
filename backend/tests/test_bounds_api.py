"""허용범위 미리보기 API (U02R) — postgres 와 시드(기준표 · 하드캡 · 종목 원장)가 필요하다.

이 API 의 약속은 하나다: **전략서 만들기의 후보 판정과 같은 답을 낸다.** 그래서 같은
성향으로 candidates.select 를 직접 불러 종목마다 대조한다.
"""
from __future__ import annotations

import pytest

from app.m1 import candidates
from app.repositories import etf_master, presets


def _auth(client, make_user):
    email, _ = make_user("retail")
    tokens = client.post("/auth/login", json={"username": email}).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_requires_login(client):
    assert client.get("/bounds", params={"risk_level": 3}).status_code == 401


@pytest.mark.parametrize("risk_level", [0, 6])
def test_rejects_out_of_range_level(client, make_user, risk_level):
    resp = client.get("/bounds", params={"risk_level": risk_level}, headers=_auth(client, make_user))

    assert resp.status_code == 422


@pytest.mark.parametrize("risk_level", [1, 3, 5])
def test_matches_compile_candidate_selection(client, make_user, risk_level):
    resp = client.get("/bounds", params={"risk_level": risk_level}, headers=_auth(client, make_user))
    assert resp.status_code == 200
    body = resp.json()

    records = etf_master.list_all(include_leveraged=True)
    expected = candidates.select(
        risk_level=risk_level, requested_tickers=[r.ticker for r in records], target=0
    )
    allowed = {c.ticker: c for c in expected.candidates}
    reasons = {r.ticker: r.reason for r in expected.rejected}

    # 원장 전 종목이 빠짐없이 한 번씩
    assert [item["ticker"] for item in body["items"]] == [r.ticker for r in records]
    for item in body["items"]:
        candidate = allowed.get(item["ticker"])
        assert item["allowed"] is (candidate is not None)
        if candidate:
            assert item["weight_min"] == candidate.weight_min
            assert item["weight_max"] == candidate.weight_max
            assert item["reason"] is None
        else:
            assert item["weight_min"] is None and item["weight_max"] is None
            assert item["reason"] == reasons[item["ticker"]]

    assert body["preset_version"] == expected.preset_version


def test_hardcap_max_is_capped_by_per_asset_limit(client, make_user):
    body = client.get("/bounds", params={"risk_level": 5}, headers=_auth(client, make_user)).json()
    per_asset = presets.get_active_hardcap()["max_weight_per_asset"]

    assert body["max_weight_per_asset"] == per_asset
    for item in body["items"]:
        if item["allowed"]:
            assert item["hardcap_max"] == min(item["weight_max"], per_asset)
        else:
            assert item["hardcap_max"] is None


def test_blocked_etfs_get_reasons(client, make_user, blocked_etfs):
    body = client.get("/bounds", params={"risk_level": 5}, headers=_auth(client, make_user)).json()
    by_ticker = {item["ticker"]: item for item in body["items"]}

    delisted = by_ticker[blocked_etfs["delisted"]]
    assert delisted["allowed"] is False
    assert delisted["reason"] == candidates.REASON_DELISTED

    leveraged = by_ticker[blocked_etfs["leveraged"]]
    assert leveraged["allowed"] is False
    assert leveraged["reason"] == candidates.REASON_LEVERAGE_FORBIDDEN


def test_group_caps_follow_risk_level(client, make_user):
    headers = _auth(client, make_user)
    low = client.get("/bounds", params={"risk_level": 1}, headers=headers).json()["group_caps"]
    high = client.get("/bounds", params={"risk_level": 5}, headers=headers).json()["group_caps"]

    assert low == presets.get_group_caps(1)
    # 성향이 높을수록 주식 상한이 넓다(기준표 v0.1)
    assert high["EQUITY"] > low["EQUITY"]
