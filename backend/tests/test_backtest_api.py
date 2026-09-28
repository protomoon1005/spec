"""POST /backtest/runs · GET /backtest/runs/{run_id} 와 run_backtest 태스크.

라우터 테스트는 service 의 러너 호출을 바꿔 끼워 vectorbt 없이 돈다. conftest 가
Celery 를 eager 로 켜므로 POST 안에서 태스크가 그 자리에서 끝난다.
재현성 테스트만 실제 러너를 돌린다(requires_backtest · requires_backfill).
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from app.backtest import service
from app.repositories import backtests, presets, profiles, specs
from app.workers import tasks

MONTHLY_FIRST = {"trigger": {"type": "calendar", "freq": "monthly", "day": 1}, "min_interval_days": 20}
DEMO_UNIVERSE = [
    ("069500", 0.05, 0.35),
    ("091160", 0.00, 0.25),
    ("360750", 0.05, 0.30),
    ("133690", 0.00, 0.25),
    ("273130", 0.10, 0.40),
    ("357870", 0.00, 0.30),
    ("411060", 0.00, 0.15),
]
BODY = {"period_start": "2023-01-01", "period_end": "2025-12-31"}

FAKE_RESULT = {
    "data_snapshot_asof": "2025-12-30",
    "metrics": {"cagr": 0.2, "mdd": -0.06, "sharpe": 1.5, "sortino": 2.0, "win_rate": None,
                "benchmark_cagr": 0.28},
    "window_results": {
        "weight_path": "M4 러너 임시 경로 (RiskSizer 없음, M2 WeightMapper 대기)",
        "scorer_sources": {"market": "mock", "sentiment": "neutral", "regime": "mock"},
        "schedule": {"rule": MONTHLY_FIRST, "note": "근사", "rebalance_dates": ["2023-01-06"],
                     "skipped_rebalance_dates": []},
        "summary": {name: {"total": 0.5, "mdd": -0.1} for name in ("strategy", "control", "market")},
        "series": [{"date": "2023-01-06", "strategy": 1.0, "control": 1.0, "market": 1.0}],
    },
}


def _auth(client, username: str) -> dict:
    token = client.post("/auth/login", json={"username": username}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def owner(engine, make_user):
    """성향 4 사용자 하나와 그 사람의 draft 전략서 하나. (email, spec_id)."""
    email, user_id = make_user("retail")
    version = presets.active_preset_version()
    profile = profiles.insert_profile(
        user_id=user_id, risk_level=4, preset_version=version,
        defaults=profiles.get_profile_defaults(version, 4), provenance={},
    )
    bounds = presets.get_asset_bounds(4)
    tickers = [t for t, _lo, _hi in DEMO_UNIVERSE]
    with engine.connect() as conn:
        tags = dict(conn.execute(
            text("SELECT ticker, risk_tag FROM etf_master WHERE ticker = ANY(:t)"), {"t": tickers}
        ).all())
    spec_id = specs.insert_spec(
        spec_id=specs.new_spec_id(), user_id=user_id, profile_id=profile.profile_id,
        hardcap_version=presets.get_active_hardcap()["hardcap_version"], spec_version="0.1",
        name="백테스트 API 테스트", input_prompt=None, rebalance=MONTHLY_FIRST,
        signal_rules={}, constraint={},
        universe=[
            {"ticker": t, "preset_id": bounds[tags[t]].preset_id, "weight_min": lo, "weight_max": hi,
             "weight_min_raw": lo, "weight_max_raw": hi, "was_adjusted": False}
            for t, lo, hi in DEMO_UNIVERSE
        ],
    ).spec_id

    yield email, spec_id

    with engine.begin() as conn:
        runs = "(SELECT run_id FROM backtest_runs WHERE spec_id = :s)"
        conn.execute(text(f"DELETE FROM backtest_metrics WHERE run_id IN {runs}"), {"s": spec_id})
        for table in ("backtest_runs", "spec_universe", "strategy_specs"):
            conn.execute(text(f"DELETE FROM {table} WHERE spec_id = :s"), {"s": spec_id})
        conn.execute(text("DELETE FROM risk_profiles WHERE user_id = :u"), {"u": user_id})


@pytest.fixture
def fake_runner(monkeypatch):
    """러너 대신 고정 결과를 돌려준다. 받은 인자를 calls 에 쌓는다."""
    calls: list[dict] = []

    def _fake(spec_id, **kwargs):
        calls.append({"spec_id": spec_id, **kwargs})
        return FAKE_RESULT

    monkeypatch.setattr(service, "run_conditions", lambda: {"fee_rate": 0.00015, "tax_rate": 0.0,
                                                            "slippage_bp": 5.0})
    monkeypatch.setattr(service, "run_spec_backtest", _fake)
    return calls


def _post(client, email, spec_id, **extra):
    return client.post("/backtest/runs", json={"spec_id": spec_id, **BODY, **extra},
                       headers=_auth(client, email))


# ── 202 → done ─────────────────────────────────────────────────────


def test_post_queues_then_get_shows_done_with_metrics_and_curve(client, owner, fake_runner):
    email, spec_id = owner

    resp = _post(client, email, spec_id)

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued" and body["spec_id"] == spec_id
    assert fake_runner == [{"spec_id": spec_id, "period_start": date(2023, 1, 1),
                            "period_end": date(2025, 12, 31), "seed_money": 10_000_000}]

    got = client.get(f"/backtest/runs/{body['run_id']}", headers=_auth(client, email)).json()
    assert got["status"] == "done"
    assert got["metrics"] == FAKE_RESULT["metrics"]
    assert got["series"] == FAKE_RESULT["window_results"]["series"]
    assert got["scorer_sources"] == {"market": "mock", "sentiment": "neutral", "regime": "mock"}
    assert got["data_snapshot_asof"] == "2025-12-30"
    assert got["reason"] is None
    # 재현 조건
    assert got["feature_set_version"] == "v0.1-ta9"
    assert (got["seed_money"], got["fee_rate"], got["tax_rate"], got["slippage_bp"]) == (
        10_000_000, 0.00015, 0.0, 5.0
    )
    assert got["started_at"] is not None


def test_period_start_defaults_to_demo_plan(client, owner, fake_runner):
    email, spec_id = owner
    resp = client.post("/backtest/runs", json={"spec_id": spec_id, "period_end": "2025-12-31"},
                       headers=_auth(client, email))

    assert resp.status_code == 202
    assert resp.json()["period_start"] == "2023-01-01"


# ── 404 ────────────────────────────────────────────────────────────


def test_cannot_run_or_read_someone_elses(client, owner, make_user, fake_runner):
    email, spec_id = owner
    other, _ = make_user("retail")
    run_id = _post(client, email, spec_id).json()["run_id"]

    assert _post(client, other, spec_id).status_code == 404
    assert _post(client, email, "STR-없는전략서").status_code == 404
    assert client.get(f"/backtest/runs/{run_id}", headers=_auth(client, other)).status_code == 404
    assert client.get("/backtest/runs/999999999", headers=_auth(client, email)).status_code == 404


# ── failed ─────────────────────────────────────────────────────────


def test_input_failure_ends_failed_with_the_reason(client, owner, fake_runner, monkeypatch):
    email, spec_id = owner

    def _raise(spec_id, **kwargs):
        raise service.BacktestFailed("러너가 지원하지 않는 리밸런싱 규칙: {...}")

    monkeypatch.setattr(service, "run_spec_backtest", _raise)
    run_id = _post(client, email, spec_id).json()["run_id"]

    got = client.get(f"/backtest/runs/{run_id}", headers=_auth(client, email)).json()
    assert got["status"] == "failed"
    assert got["reason"] == "러너가 지원하지 않는 리밸런싱 규칙: {...}"
    assert got["metrics"] is None and got["series"] is None


def test_unexpected_error_does_not_leak_the_stack(client, owner, fake_runner, monkeypatch):
    email, spec_id = owner

    def _boom(spec_id, **kwargs):
        raise ZeroDivisionError("secret internal detail")

    monkeypatch.setattr(service, "run_spec_backtest", _boom)
    run_id = _post(client, email, spec_id).json()["run_id"]

    got = client.get(f"/backtest/runs/{run_id}", headers=_auth(client, email)).json()
    assert got["status"] == "failed"
    assert got["reason"] == tasks.UNEXPECTED_FAILURE
    assert "secret" not in str(got)


# ── 지표 정의 (frontend/lib/data.ts computeMetrics 와 같다) ──────────


def test_metrics_follow_the_frontend_definition():
    # 주간 수익률 +10%, −10%, +10%. 손으로 계산한 값과 대조한다.
    values = [100.0, 110.0, 99.0, 108.9]
    m = service.compute_metrics(values, years=1.0)

    rets = [0.1, -0.1, 0.1]
    mean = sum(rets) / 3
    vol = (sum((r - mean) ** 2 for r in rets) / 2) ** 0.5 * 52**0.5
    downs = [0.0, -0.1, 0.0]
    dmean = sum(downs) / 3
    down = (sum((r - dmean) ** 2 for r in downs) / 2) ** 0.5 * 52**0.5
    assert m["total"] == pytest.approx(0.089)
    assert m["cagr"] == pytest.approx(0.089)  # 1년이라 누적과 같다
    assert m["mdd"] == pytest.approx(-0.1)
    assert m["vol"] == pytest.approx(vol)
    assert m["sharpe"] == pytest.approx((0.089 - 0.025) / vol)
    assert m["sortino"] == pytest.approx((0.089 - 0.025) / down)
    assert m["final"] == 108.9


# ── 재현성: 같은 spec_id 를 두 번 돌리면 지표와 목표 비중이 같다 ────────


@pytest.mark.requires_backtest
@pytest.mark.requires_backfill
def test_same_spec_twice_gives_the_same_metrics_and_targets(owner):
    _email, spec_id = owner
    runs = [
        service.run_spec_backtest(spec_id, period_start=date(2023, 1, 1), period_end=date(2025, 12, 31),
                                  seed_money=service.SEED_MONEY)
        for _ in range(2)
    ]

    assert runs[0]["metrics"] == runs[1]["metrics"]
    assert runs[0]["data_snapshot_asof"] == runs[1]["data_snapshot_asof"]
    targets = [[d["target"] for d in r["window_results"]["decisions"]] for r in runs]
    assert targets[0] == targets[1]
    assert len(targets[0]) == 36
    assert runs[0]["window_results"]["scorer_sources"] == {
        "market": "mock", "sentiment": "neutral", "regime": "mock"
    }


@pytest.mark.requires_backtest
@pytest.mark.requires_backfill
def test_task_runs_the_real_runner_to_done(owner):
    _email, spec_id = owner
    row = backtests.create_run(spec_id, period_start=date(2023, 1, 1), period_end=date(2025, 12, 31),
                               seed_money=service.SEED_MONEY, feature_set_version=service.FEATURE_SET_VERSION)

    assert tasks.run_backtest(row["run_id"])["status"] == "done"
    got = backtests.get_run(row["run_id"])
    assert got["status"] == "done"
    assert got["data_snapshot_asof"] == date(2025, 12, 30)
    assert got["win_rate"] is None
    assert len(got["window_results"]["series"]) == 158
