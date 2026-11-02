"""전략서 v0.2 API(app/routers/strategy_v0_2.py) 테스트.

작업 큐는 바로 실행(eager)되고 LLM 은 가짜로 바꾼다. 대화 상태는 컨테이너의 Redis 를 쓴다.
"""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import text

from app.m1 import pairing
from app.workers import tasks

REPRESENTATIVE = "삼성전자 RSI 30 아래로 떨어지면 사고 5일선 깨지면 팔아줘"


class FakeClient:
    def __init__(self, *answers):
        self.answers = list(answers)

    def generate_json(self, prompt, json_schema, *, system=None):
        return self.answers.pop(0)


@pytest.fixture
def fake_llm(monkeypatch):
    def use(*answers):
        client = FakeClient(*answers)
        monkeypatch.setattr("app.llm.client.get_llm_client", lambda: client)
        return client

    return use


@pytest.fixture
def account(client, make_user, engine):
    email, user_id = make_user()
    token = client.post("/auth/login", json={"username": email}).json()["access_token"]
    yield user_id, {"Authorization": f"Bearer {token}"}
    with engine.begin() as conn:
        mine = "(SELECT strategy_id FROM strategies WHERE user_id = :u)"
        runs = f"(SELECT run_id FROM backtest_runs WHERE strategy_id IN {mine})"
        conn.execute(text(f"DELETE FROM backtest_metrics WHERE run_id IN {runs}"), {"u": user_id})
        for table in ("backtest_runs", "validation_logs", "strategy_assets"):
            conn.execute(text(f"DELETE FROM {table} WHERE strategy_id IN {mine}"), {"u": user_id})
        conn.execute(text("DELETE FROM strategies WHERE user_id = :u"), {"u": user_id})


def _compile(user_id, text_):
    return tasks.compile_strategy_v0_2({"user_id": user_id, "text": text_})


def test_compile_endpoint_accepts_a_job(client, account, fake_llm):
    _, headers = account
    fake_llm(copy.deepcopy(pairing.EXAMPLE_OUTPUT))
    response = client.post("/v2/strategies/compile", json={"text": REPRESENTATIVE}, headers=headers)
    assert response.status_code == 202 and response.json()["job_id"]
    assert client.post("/v2/strategies/compile", json={"text": ""}, headers=headers).status_code == 422


def test_ok_result_has_spec_checks_and_explanation(client, account, fake_llm):
    user_id, headers = account
    fake_llm(copy.deepcopy(pairing.EXAMPLE_OUTPUT))
    result = _compile(user_id, REPRESENTATIVE)
    assert result["status"] == "ok"
    assert [c["stage"] for c in result["checks"]] == [1, 2, 3, 4]
    assert result["explanation"][1] == "살 때: RSI(14일) — 30 아래로 내려가는 순간. 다음 날 시가에 삽니다"

    got = client.get(f"/v2/strategies/{result['strategy_id']}", headers=headers).json()
    assert got["name"] == "삼성전자 · 매매 조건형" and got["spec"] == result["spec"]
    assert sorted(c["stage"] for c in got["checks"]) == [1, 2, 3, 4]
    assert got["explanation"] == result["explanation"]


def test_ask_then_answer_over_the_api(client, account, fake_llm, make_user):
    user_id, headers = account
    raw = copy.deepcopy(pairing.EXAMPLE_OUTPUT)
    raw["rules"][0]["entry"] = {
        "actions": ["5:매수"],
        "join": "one",
        "items": [{"subject": None, "left": "2:RSI", "op": "4:아래로 내려가면", "right": "3:30"}],
    }
    raw["rules"][0]["exit"] = None
    fake_llm(raw)
    asked = _compile(user_id, "삼성전자 RSI 30 아래로 내려가면 매수")
    assert asked["status"] == "ask" and asked["questions"][0]["kind"] == "exit"

    # 남의 대화에는 답할 수 없다
    other_email, _ = make_user()
    other = client.post("/auth/login", json={"username": other_email}).json()["access_token"]
    url = f"/v2/strategies/compile/{asked['session_id']}/answer"
    body = {"answers": [{"question_id": "q1", "choice_id": "c1"}]}
    assert client.post(url, json=body, headers={"Authorization": f"Bearer {other}"}).status_code == 404

    done = client.post(url, json=body, headers=headers).json()
    assert done["status"] == "ok" and done["explanation"]
    assert client.post(url, json=body, headers=headers).status_code == 404  # 끝난 대화는 없다


def test_reject_result(account, fake_llm):
    user_id, _ = account
    fake_llm()
    result = _compile(user_id, "삼성전자 MACD 골든크로스 나면 사")
    assert result == {
        "status": "reject",
        "session_id": result["session_id"],
        "code": "UNKNOWN_INDICATOR",
        "message": result["message"],
        "evidence": "MACD",
        "stage": None,
    }


def test_other_users_strategy_is_not_found(client, account, fake_llm, make_user):
    user_id, _ = account
    fake_llm(copy.deepcopy(pairing.EXAMPLE_OUTPUT))
    strategy_id = _compile(user_id, REPRESENTATIVE)["strategy_id"]
    other_email, _ = make_user()
    other = client.post("/auth/login", json={"username": other_email}).json()["access_token"]
    assert (
        client.get(f"/v2/strategies/{strategy_id}", headers={"Authorization": f"Bearer {other}"}).status_code
        == 404
    )


@pytest.mark.requires_backfill
def test_backtest_runs_and_reports(client, account, fake_llm):
    user_id, headers = account
    fake_llm(copy.deepcopy(pairing.EXAMPLE_OUTPUT))
    strategy_id = _compile(user_id, REPRESENTATIVE)["strategy_id"]
    accepted = client.post(f"/v2/strategies/{strategy_id}/backtest", headers=headers)
    assert accepted.status_code == 202
    run = client.get(f"/v2/backtests/{accepted.json()['run_id']}", headers=headers).json()
    assert run["status"] == "done", run
    assert set(run["summary"]) == {"strategy", "hold_same", "market_proxy"}
    assert run["series"][0].keys() == {"date", "strategy", "hold_same", "market_proxy"}
    assert run["trades"] and run["notes"]
    assert run["metrics"]["benchmark_cagr"] is not None


@pytest.mark.requires_backfill
def test_vix_strategy_backtest_fails_with_a_demo_reason(client, account, fake_llm):
    user_id, headers = account
    raw = {
        "universe": ["4:KODEX 200"],
        "weights": [],
        "rebalance": [],
        "max_weight": None,
        "selection": [],
        "backtest": {"period": [], "capital": [], "dividends": []},
        "quotes": [],
        "rules": [
            {
                "target": "each",
                "entry": {
                    "actions": ["9:다시 사"],
                    "join": "one",
                    "items": [{"subject": None, "left": "6:VIX", "op": "8:아래로 내려오면", "right": "7:20"}],
                },
                "exit": {
                    "actions": ["5:다 팔고"],
                    "join": "one",
                    "items": [{"subject": None, "left": "1:VIX", "op": "3:이상이면", "right": "2:25"}],
                },
            }
        ],
    }
    fake_llm(raw)
    result = _compile(user_id, "VIX가 25 이상이면 KODEX 200 다 팔고, VIX가 20 아래로 내려오면 다시 사")
    assert result["status"] == "ok", result
    assert any(i["code"] == "SUBJECT_DATA_NOT_IN_DEMO" for c in result["checks"] for i in c["items"])
    run_id = client.post(f"/v2/strategies/{result['strategy_id']}/backtest", headers=headers).json()["run_id"]
    run = client.get(f"/v2/backtests/{run_id}", headers=headers).json()
    assert run["status"] == "failed" and "데모에 데이터가 없어" in run["reason"]
