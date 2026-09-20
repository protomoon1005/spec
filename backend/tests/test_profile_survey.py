"""투자 성향 설문 — 점수 계산(순수 함수)과 API 흐름 (M1 1단계).

앞쪽 절반은 DB 없이 돈다. 뒤쪽은 postgres 와 시드(기준표 v0.1)가 필요하다.
"""
from __future__ import annotations

import pytest

from app.profile import survey

ALL_QUESTIONS = [q.code for q in survey.QUESTIONS]


def _answers(picker) -> dict[str, str]:
    return {q.code: picker(q) for q in survey.QUESTIONS}


LOWEST = _answers(lambda q: min(q.choices, key=lambda c: c[2])[0])
HIGHEST = _answers(lambda q: max(q.choices, key=lambda c: c[2])[0])
MIDDLE = _answers(lambda q: next(c[0] for c in q.choices if c[2] == 3))


# --- 점수 계산 -------------------------------------------------------------


def test_lowest_answers_give_most_conservative_level():
    result = survey.evaluate(LOWEST)

    assert result.risk_level == 1
    assert result.normalized_score == 0.0


def test_highest_answers_give_most_aggressive_level():
    result = survey.evaluate(HIGHEST)

    assert result.risk_level == 5
    assert result.normalized_score == 100.0


def test_middle_answers_land_in_the_middle():
    assert survey.evaluate(MIDDLE).risk_level == 3


def test_conservative_but_not_lowest_still_lands_on_level_one():
    """환산식 회귀 방지.

    선택지 점수가 1부터라 단순히 만점으로 나누면 최저가 20 이 되고, 20/40/60/80
    경계에서 1등급 구간이 한 점밖에 안 남는다. 아래 답은 그 식에서 22.2 가 나와
    안정추구형으로 올라갔었다.
    """
    conservative = {
        "AGE": "A5",
        "HORIZON": "A1",
        "EXPERIENCE": "A1",
        "KNOWLEDGE": "A1",
        "INCOME_SOURCE": "A2",
        "ASSET_RATIO": "A5",
        "LOSS_TOLERANCE": "A1",
    }

    assert survey.evaluate(conservative).risk_level == 1


def test_every_level_is_reachable():
    """다섯 등급이 모두 나올 수 있어야 한다 — 한 등급이라도 구간이 비면
    그 성향의 사용자는 영영 나오지 않는다."""
    reachable = {survey.risk_level_of(score) for score in range(0, 101)}

    assert reachable == {1, 2, 3, 4, 5}


def test_missing_question_is_rejected():
    partial = dict(LOWEST)
    del partial["LOSS_TOLERANCE"]

    with pytest.raises(ValueError, match="LOSS_TOLERANCE"):
        survey.evaluate(partial)


def test_unknown_choice_is_rejected():
    with pytest.raises(ValueError, match="없는 선택지"):
        survey.score_of("AGE", "A9")


def test_unknown_question_is_rejected():
    with pytest.raises(ValueError, match="없는 문항"):
        survey.score_of("NOPE", "A1")


def test_same_answers_give_same_result():
    assert survey.evaluate(MIDDLE) == survey.evaluate(dict(reversed(list(MIDDLE.items()))))


# --- API 흐름 (postgres + 시드 필요) ----------------------------------------


def _auth(client, make_user, test_password):
    email, user_id = make_user("retail")
    tokens = client.post("/auth/login", json={"email": email, "password": test_password}).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}, user_id


def _submit(client, headers, answers: dict[str, str]):
    payload = [{"question_code": code, "answer_code": answer} for code, answer in answers.items()]
    return client.post("/profile/survey", json=payload, headers=headers)


def _cleanup(engine, user_id: int) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM risk_profiles WHERE user_id = :u"), {"u": user_id})
        conn.execute(text("DELETE FROM survey_responses WHERE user_id = :u"), {"u": user_id})


def test_questions_are_served_without_a_profile(client, make_user, test_password):
    headers, _ = _auth(client, make_user, test_password)

    resp = client.get("/profile/survey/questions", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert [q["question_code"] for q in body["questions"]] == ALL_QUESTIONS


def test_profile_flow_end_to_end(client, engine, make_user, test_password):
    headers, user_id = _auth(client, make_user, test_password)
    try:
        # 확정 전에는 볼 성향이 없다.
        assert client.get("/profile/me", headers=headers).status_code == 404

        assert _submit(client, headers, LOWEST).status_code == 201

        created = client.post("/profile", headers=headers)
        assert created.status_code == 201, created.text
        assert created.json()["risk_level"] == 1
        assert created.json()["preset_version"] == "v0.1"

        mine = client.get("/profile/me", headers=headers)
        assert mine.status_code == 200
        assert mine.json()["profile_id"] == created.json()["profile_id"]
    finally:
        _cleanup(engine, user_id)


def test_profile_requires_all_questions(client, engine, make_user, test_password):
    headers, user_id = _auth(client, make_user, test_password)
    try:
        partial = dict(LOWEST)
        del partial["LOSS_TOLERANCE"]
        assert _submit(client, headers, partial).status_code == 201

        resp = client.post("/profile", headers=headers)

        assert resp.status_code == 400
        assert "LOSS_TOLERANCE" in resp.json()["detail"]
    finally:
        _cleanup(engine, user_id)


def test_latest_answer_wins(client, engine, make_user, test_password):
    """답을 고치면 마지막 답으로 판정한다."""
    headers, user_id = _auth(client, make_user, test_password)
    try:
        _submit(client, headers, LOWEST)
        _submit(client, headers, HIGHEST)

        resp = client.post("/profile", headers=headers)

        assert resp.json()["risk_level"] == 5
    finally:
        _cleanup(engine, user_id)


def test_reassessment_appends_instead_of_overwriting(client, engine, make_user, test_password):
    """다시 진단하면 새 행이 쌓인다 — 전략서가 가리키는 과거 성향이 바뀌면 안 된다."""
    headers, user_id = _auth(client, make_user, test_password)
    try:
        _submit(client, headers, LOWEST)
        first = client.post("/profile", headers=headers).json()

        _submit(client, headers, HIGHEST)
        second = client.post("/profile", headers=headers).json()

        assert second["profile_id"] != first["profile_id"]
        assert client.get("/profile/me", headers=headers).json()["profile_id"] == second["profile_id"]

        from sqlalchemy import text

        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT risk_level FROM risk_profiles WHERE user_id = :u ORDER BY profile_id"),
                {"u": user_id},
            ).all()
        assert [row[0] for row in rows] == [1, 5]
    finally:
        _cleanup(engine, user_id)


def test_profile_copies_defaults_and_keeps_reasoning(client, engine, make_user, test_password):
    """성향별 기본값이 복사되고, 어떤 답이 몇 점이어서 그 등급이 됐는지가 남는다."""
    headers, user_id = _auth(client, make_user, test_password)
    try:
        _submit(client, headers, LOWEST)
        profile_id = client.post("/profile", headers=headers).json()["profile_id"]

        from sqlalchemy import text

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT cash_min_default, max_drawdown_default, max_loss_per_trade_default,
                           provenance
                      FROM risk_profiles WHERE profile_id = :p
                    """
                ),
                {"p": profile_id},
            ).one()

        # 기준표 v0.1 의 성향 1 기본값과 같아야 한다.
        assert float(row.cash_min_default) == 0.20
        assert float(row.max_drawdown_default) == 0.10
        assert float(row.max_loss_per_trade_default) == 0.02

        provenance = row.provenance
        assert provenance["survey_version"] == survey.SURVEY_VERSION
        assert provenance["normalized_score"] == 0.0
        assert [item["question_code"] for item in provenance["answers"]] == ALL_QUESTIONS
    finally:
        _cleanup(engine, user_id)


def test_survey_rejects_unknown_choice(client, make_user, test_password):
    headers, _ = _auth(client, make_user, test_password)

    resp = client.post(
        "/profile/survey",
        json=[{"question_code": "AGE", "answer_code": "A9"}],
        headers=headers,
    )

    assert resp.status_code == 422
