"""전략서 승인 → 포트폴리오 활성화 + FN-409 — POST /specs/{spec_id}/approve, GET /portfolios/{id}.

postgres 와 시드가 적재된 상태여야 한다. 전략서는 LLM 없이 후처리 저장으로 만들고, 검증 기록과
백테스트 실행은 저장소 함수로 직접 넣는다(러너를 돌리지 않는다). 실제 검증기 · 러너를 거치는
끝까지 흐름은 맨 아래 requires_backtest 테스트 하나다.
"""
from __future__ import annotations

import threading
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.m1 import candidates as C
from app.m1 import postprocess
from app.m2 import approval
from app.repositories import backtests, bbl, profiles, specs
from app.repositories.view_weights import get_view_weights
from app.scoring import live

SEED = 10_000_000


@pytest.fixture
def make_owner(engine, make_user):
    """(role) -> (email, profile). 그 사용자가 만든 전략서 · 실행 · 승인 · 포트폴리오를 뒤에서 지운다."""
    users: list[int] = []

    def _make(role: str = "retail"):
        email, user_id = make_user(role)
        version = profiles.active_preset_version()
        record = profiles.insert_profile(
            user_id=user_id, risk_level=5, preset_version=version,
            defaults=profiles.get_profile_defaults(version, 5), provenance={},
        )
        users.append(user_id)
        return email, record

    yield _make

    with engine.begin() as conn:
        for user_id in users:
            mine = "(SELECT spec_id FROM strategy_specs WHERE user_id = :u)"
            pfs = f"(SELECT portfolio_id FROM portfolios WHERE spec_id IN {mine})"
            conn.execute(text(f"DELETE FROM view_weights WHERE portfolio_id IN {pfs}"), {"u": user_id})
            conn.execute(text(f"DELETE FROM portfolios WHERE spec_id IN {mine}"), {"u": user_id})
            conn.execute(text("DELETE FROM approvals WHERE user_id = :u"), {"u": user_id})
            runs = f"(SELECT run_id FROM backtest_runs WHERE spec_id IN {mine})"
            conn.execute(text(f"DELETE FROM backtest_metrics WHERE run_id IN {runs}"), {"u": user_id})
            for table in ("backtest_runs", "spec_universe", "validation_logs"):
                conn.execute(text(f"DELETE FROM {table} WHERE spec_id IN {mine}"), {"u": user_id})
            conn.execute(text("DELETE FROM strategy_specs WHERE user_id = :u"), {"u": user_id})
            conn.execute(text("DELETE FROM risk_profiles WHERE user_id = :u"), {"u": user_id})


def _save_spec(profile) -> str:
    candidate_set = C.select(risk_level=5, target=3)
    payload = {
        "spec_id": "무시됨",
        "spec_version": "0.1",
        "user_id": 999,
        "name": "승인 테스트 전략",
        "created_at": "2026-10-20T09:00:00+09:00",
        "universe": [
            {"ticker": c.ticker, "name": c.name, "weight_min": c.weight_min, "weight_max": c.weight_max}
            for c in candidate_set.candidates
        ],
        "rebalance": bbl.get_block("RB_MONTHLY_FIRST").params_schema,
        "signal_rules": {"market_analysis": {"indicators": ["ret_20"]}},
        "constraint": {
            "max_weight_per_asset": 0.30,
            "min_weight_per_asset": 0.00,
            "cash_min": 0.05,
            "max_loss_per_trade": 0.05,
            "max_drawdown": 0.25,
        },
    }
    compiled = postprocess.parse(payload, candidate_set, spec_id=specs.new_spec_id(), user_id=profile.user_id)
    return postprocess.save(
        compiled, user_id=profile.user_id, profile_id=profile.profile_id, input_prompt="x"
    ).spec_id


def _validate(spec_id: str, *, failed_stage: int | None = None) -> None:
    specs.record_validation(
        spec_id,
        bounds=None,
        logs=[
            {"stage": s, "passed": s != failed_stage, "violations": [], "adjusted_bounds": None,
             "clamped_fields": None}
            for s in (1, 2, 3, 4)
        ],
    )


def _run(spec_id: str, *, status: str = backtests.DONE, validation: dict | None = None,
         universe: list[dict] | None = None, omit_validation: bool = False) -> int:
    row = backtests.create_run(spec_id, period_start=date(2023, 1, 1), period_end=date(2025, 12, 30),
                               seed_money=SEED, feature_set_version="v0.1-ta9")
    if status == backtests.DONE:
        if universe is None:
            universe = _bounds(spec_id)
        results: dict = {"universe": universe}
        if not omit_validation:
            results["validation"] = validation or {"status": "passed"}
        backtests.finish_run(
            row["run_id"], data_snapshot_asof="2025-12-30", metrics={}, window_results=results
        )
    return row["run_id"]


def _bounds(spec_id: str) -> list[dict]:
    return [
        {"ticker": u["ticker"], "weight_min": float(u["weight_min"]), "weight_max": float(u["weight_max"])}
        for u in specs.get_spec_universe(spec_id)
    ]


def _ready(profile) -> tuple[str, int]:
    spec_id = _save_spec(profile)
    _validate(spec_id)
    return spec_id, _run(spec_id)


def _auth(client, username: str) -> dict:
    token = client.post("/auth/login", json={"username": username}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _counts(engine, spec_id: str) -> dict:
    with engine.connect() as conn:
        def one(sql: str):
            return conn.execute(text(sql), {"s": spec_id}).scalar_one()

        return {
            "approvals": one("SELECT count(*) FROM approvals WHERE spec_id = :s"),
            "portfolios": one("SELECT count(*) FROM portfolios WHERE spec_id = :s"),
            "weights": one(
                "SELECT count(*) FROM view_weights WHERE portfolio_id IN "
                "(SELECT portfolio_id FROM portfolios WHERE spec_id = :s)"
            ),
            "status": one("SELECT status FROM strategy_specs WHERE spec_id = :s"),
        }


UNIFORM = pytest.approx({"market": 1 / 3, "sentiment": 1 / 3, "regime": 1 / 3})


# ── 정상 흐름 ──────────────────────────────────────────────────────────

def test_approve_activates_portfolio_with_fn409_weights(engine, client, make_owner):
    email, profile = make_owner()
    spec_id, run_id = _ready(profile)

    resp = client.post(f"/specs/{spec_id}/approve", json={"run_id": run_id, "user_comment": "좋다"},
                       headers=_auth(client, email))

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["spec_id"] == spec_id
    assert (body["mode"], body["status"], body["account_no"]) == ("virtual", "active", None)
    assert body["initial_cash"] == SEED  # 승인 근거 실행의 seed_money
    assert body["approval"]["run_id"] == run_id
    assert body["approval"]["user_comment"] == "좋다"
    assert body["initial_weights"] == UNIFORM
    assert _counts(engine, spec_id) == {"approvals": 1, "portfolios": 1, "weights": 3, "status": "approved"}

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT a.user_id, a.approved, a.decided_at, p.activated_at FROM approvals a "
                 "JOIN portfolios p ON p.spec_id = a.spec_id WHERE a.spec_id = :s"), {"s": spec_id}
        ).one()
    assert row.user_id == profile.user_id and row.approved is True
    assert row.decided_at == row.activated_at  # 같은 트랜잭션의 now()


def test_fn409_as_of_is_activation_date_in_kst(engine, client, make_owner):
    email, profile = make_owner()
    spec_id, run_id = _ready(profile)

    headers = _auth(client, email)
    body = client.post(f"/specs/{spec_id}/approve", json={"run_id": run_id}, headers=headers).json()

    activated = body["activated_at"]
    with engine.connect() as conn:
        activated_at = conn.execute(
            text("SELECT activated_at FROM portfolios WHERE portfolio_id = :p"), {"p": body["portfolio_id"]}
        ).scalar_one()
    kst_day = activated_at.astimezone(ZoneInfo("Asia/Seoul")).date()
    assert activated and date.fromisoformat(body["weights_as_of"]) == kst_day
    # strict < — 활성화 당일 판단은 기록을 못 읽고(FN-409 기본값 1/3 으로 대체), 다음 날부터 읽는다
    assert get_view_weights(body["portfolio_id"], as_of=kst_day) == {}
    assert get_view_weights(body["portfolio_id"], as_of=kst_day + timedelta(days=1)) == UNIFORM


def test_get_portfolio_is_owner_only(client, make_owner):
    email, profile = make_owner()
    other, _ = make_owner()
    admin, _ = make_owner("admin")
    spec_id, run_id = _ready(profile)
    headers = _auth(client, email)
    created = client.post(f"/specs/{spec_id}/approve", json={"run_id": run_id}, headers=headers).json()
    pid = created["portfolio_id"]

    mine = client.get(f"/portfolios/{pid}", headers=_auth(client, email))

    assert mine.status_code == 200
    assert mine.json()["approval"]["approval_id"] == created["approval"]["approval_id"]
    assert {k: mine.json()[k] for k in ("spec_id", "mode", "status", "initial_cash")} == {
        "spec_id": spec_id, "mode": "virtual", "status": "active", "initial_cash": SEED,
    }
    assert client.get(f"/portfolios/{pid}", headers=_auth(client, other)).status_code == 404
    assert client.get(f"/portfolios/{pid}", headers=_auth(client, admin)).status_code == 404
    assert client.get("/portfolios/0", headers=_auth(client, email)).status_code == 404
    assert client.get(f"/portfolios/{pid}").status_code == 401


def test_approved_spec_is_frozen_and_cannot_be_revalidated(engine, client, make_owner):
    email, profile = make_owner()
    spec_id, run_id = _ready(profile)
    headers = _auth(client, email)
    client.post(f"/specs/{spec_id}/approve", json={"run_id": run_id}, headers=headers)

    assert client.post(f"/specs/{spec_id}/validate", headers=headers).status_code == 409
    assert client.delete(f"/specs/{spec_id}", headers=headers).status_code == 409
    with pytest.raises(Exception, match="approved"), engine.begin() as conn:
        conn.execute(text("UPDATE spec_universe SET weight_max = 1 WHERE spec_id = :s"), {"s": spec_id})


# ── 전제조건 ───────────────────────────────────────────────────────────

def test_second_approve_is_409_and_leaves_one_portfolio(engine, client, make_owner):
    email, profile = make_owner()
    spec_id, run_id = _ready(profile)
    headers = _auth(client, email)

    first = client.post(f"/specs/{spec_id}/approve", json={"run_id": run_id}, headers=headers)
    second = client.post(f"/specs/{spec_id}/approve", json={"run_id": run_id}, headers=headers)

    assert (first.status_code, second.status_code) == (201, 409)
    assert second.json()["detail"] == "draft 전략서만 승인할 수 있다"
    assert _counts(engine, spec_id)["portfolios"] == 1


def test_someone_elses_spec_is_404_even_for_admin(engine, client, make_owner):
    _, profile = make_owner()
    admin, _ = make_owner("admin")
    spec_id, run_id = _ready(profile)

    resp = client.post(f"/specs/{spec_id}/approve", json={"run_id": run_id}, headers=_auth(client, admin))

    assert resp.status_code == 404
    assert client.post("/specs/STR-없음/approve", json={"run_id": run_id},
                       headers=_auth(client, admin)).status_code == 404
    assert _counts(engine, spec_id) == {"approvals": 0, "portfolios": 0, "weights": 0, "status": "draft"}


@pytest.mark.parametrize("failed_stage", [None, 3, 4])
def test_spec_must_pass_all_four_stages(engine, client, make_owner, failed_stage):
    email, profile = make_owner()
    spec_id = _save_spec(profile)
    if failed_stage is not None:
        _validate(spec_id, failed_stage=failed_stage)
    run_id = _run(spec_id)

    resp = client.post(f"/specs/{spec_id}/approve", json={"run_id": run_id}, headers=_auth(client, email))

    assert resp.status_code == 409
    assert resp.json()["detail"] == "검증을 4단까지 통과한 전략서만 승인할 수 있다"
    assert _counts(engine, spec_id)["status"] == "draft"


def test_last_validation_wins(client, make_owner):
    # 통과 → 막힘 순서면 막힌 것이 마지막 검증이다
    email, profile = make_owner()
    spec_id, run_id = _ready(profile)
    _validate(spec_id, failed_stage=2)

    resp = client.post(f"/specs/{spec_id}/approve", json={"run_id": run_id}, headers=_auth(client, email))

    assert resp.status_code == 409


def test_run_id_is_required(client, make_owner):
    email, profile = make_owner()
    spec_id, _ = _ready(profile)

    assert client.post(f"/specs/{spec_id}/approve", json={}, headers=_auth(client, email)).status_code == 422


def test_run_checks(engine, client, make_owner):
    email, profile = make_owner()
    other_email, other_profile = make_owner()
    headers = _auth(client, email)
    spec_id = _save_spec(profile)
    _validate(spec_id)
    sibling, sibling_run = _ready(profile)
    _, foreign_run = _ready(other_profile)
    bounds = _bounds(spec_id)
    shifted = [{**bounds[0], "weight_max": bounds[0]["weight_max"] - 0.05}, *bounds[1:]]

    def approve(run_id):
        r = client.post(f"/specs/{spec_id}/approve", json={"run_id": run_id}, headers=headers)
        return r.status_code, r.json().get("detail")

    assert approve(999_999_999) == (404, "실행 기록을 찾을 수 없다")
    assert approve(foreign_run) == (404, "실행 기록을 찾을 수 없다")
    assert approve(sibling_run) == (422, "이 전략서로 돌린 실행이 아니다")
    assert approve(_run(spec_id, status=backtests.QUEUED))[0] == 409
    assert approve(_run(spec_id, validation={"status": "none"}))[0] == 409  # 검증 전에 돌린 실행
    assert approve(_run(spec_id, universe=shifted))[0] == 409  # 지금 확정 범위와 다름
    assert approve(_run(spec_id, universe=bounds[1:]))[0] == 409  # 종목이 다름
    assert _counts(engine, spec_id) == {"approvals": 0, "portfolios": 0, "weights": 0, "status": "draft"}
    # 결과에 검증 요약이 없는 형식(M2 비중 체인)이라도 확정 범위가 같으면 된다
    assert approve(_run(spec_id, omit_validation=True))[0] == 201


# ── 원자성 · 동시성 ─────────────────────────────────────────────────────

def test_weight_write_failure_rolls_back_everything(engine, client, make_owner, monkeypatch):
    email, profile = make_owner()
    spec_id, run_id = _ready(profile)

    def broken(**kwargs):
        raise RuntimeError("주입된 가중치 기록 실패")

    monkeypatch.setattr(live, "insert_view_weights", broken)
    with pytest.raises(RuntimeError, match="주입된"):
        approval.approve(spec_id, user_id=profile.user_id, run_id=run_id)
    assert _counts(engine, spec_id) == {"approvals": 0, "portfolios": 0, "weights": 0, "status": "draft"}

    # 고치고 다시 누르면 처음처럼 된다
    monkeypatch.undo()
    resp = client.post(f"/specs/{spec_id}/approve", json={"run_id": run_id}, headers=_auth(client, email))
    assert resp.status_code == 201
    assert _counts(engine, spec_id) == {"approvals": 1, "portfolios": 1, "weights": 3, "status": "approved"}


def test_concurrent_approvals_create_one_portfolio(engine, make_owner):
    _, profile = make_owner()
    spec_id, run_id = _ready(profile)
    n = 8
    barrier = threading.Barrier(n)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            approval.approve(spec_id, user_id=profile.user_id, run_id=run_id)
            result = "ok"
        except approval.ApprovalError as exc:
            result = exc.reason
        except Exception as exc:  # noqa: BLE001 — 500 이 될 예외도 세어 본다
            result = type(exc).__name__
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["not_draft"] * (n - 1) + ["ok"]
    assert _counts(engine, spec_id) == {"approvals": 1, "portfolios": 1, "weights": 3, "status": "approved"}


# ── 실제 검증기 · 러너로 끝까지 ────────────────────────────────────────

@pytest.mark.requires_backtest
@pytest.mark.requires_backfill
def test_end_to_end_with_real_validator_and_runner(engine, client, make_owner, monkeypatch):
    from app.views import bridge

    monkeypatch.setattr(bridge, "SCORERS", bridge.MOCK_SCORERS)  # LightGBM 없이 결정적으로
    email, profile = make_owner()
    headers = _auth(client, email)
    spec_id = _save_spec(profile)

    def backtest() -> int:  # 접수가 run_backtest.delay 를 부르고, 테스트는 eager 라 그 자리에서 돈다
        body = {"spec_id": spec_id, "period_start": "2025-01-01", "period_end": "2025-12-30"}
        run_id = client.post("/backtest/runs", json=body, headers=headers).json()["run_id"]
        assert backtests.get_run(run_id)["status"] == backtests.DONE
        return run_id

    early = backtest()  # 검증 전 실행 — AI 원래 범위로 돈다
    assert client.post(f"/specs/{spec_id}/validate", headers=headers).json()["passed"] is True
    stale = client.post(f"/specs/{spec_id}/approve", json={"run_id": early}, headers=headers)
    assert stale.status_code == 409

    fresh = client.post(f"/specs/{spec_id}/approve", json={"run_id": backtest()}, headers=headers)
    assert fresh.status_code == 201, fresh.text
