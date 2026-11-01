"""JWT 발급과 로그인 사용자 의존성 테스트.

  - 토큰 없음이나 잘못된 토큰은 401
  - 정상 토큰이면 라우터 본체까지 간다
  - refresh 토큰으로 access 토큰을 재발급
"""
from __future__ import annotations

import uuid


def _login(client, username: str) -> dict:
    resp = client.post("/auth/login", json={"username": username})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_login_rejects_unknown_username(client):
    resp = client.post("/auth/login", json={"username": "nobody-that-does-not-exist"})

    assert resp.status_code == 401


def test_login_issues_access_and_refresh_tokens(client, make_user):
    email, _ = make_user()

    tokens = _login(client, email)

    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"]
    assert tokens["refresh_token"]


def test_refresh_issues_new_access_token(client, make_user):
    email, _ = make_user()
    tokens = _login(client, email)

    resp = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert resp.status_code == 200
    assert resp.json()["access_token"]


def test_refresh_rejects_access_token(client, make_user):
    """refresh 엔드포인트에 access 토큰을 넣으면 거부되어야 한다 (type 혼용 방지)."""
    email, _ = make_user()
    tokens = _login(client, email)

    resp = client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})

    assert resp.status_code == 401


def test_protected_endpoint_without_token_is_401(client):
    resp = client.get("/specs/spec-does-not-exist")

    assert resp.status_code == 401


def test_protected_endpoint_with_garbage_token_is_401(client):
    resp = client.get("/specs/spec-does-not-exist", headers={"Authorization": "Bearer not-a-jwt"})

    assert resp.status_code == 401


def test_authenticated_user_reaches_the_router_body(client, make_user):
    """로그인한 사용자는 spec 라우터를 통과해 본체(없는 전략서이면 404)까지 도달해야 한다."""
    email, _ = make_user()
    tokens = _login(client, email)

    resp = client.get(
        "/specs/spec-does-not-exist",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert resp.status_code == 404


def _signup_name() -> str:
    # 이메일 형식이 아니어도 된다는 것까지 같이 확인한다.
    return f"아무 이름 {uuid.uuid4().hex[:8]}"


def _cleanup(engine, username: str) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE email = :u"), {"u": username})


def test_signup_creates_usable_account(client, engine):
    name = _signup_name()
    try:
        resp = client.post("/auth/signup", json={"username": name})

        assert resp.status_code == 201, resp.text
        token = resp.json()["access_token"]
        me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200

        assert _login(client, f"  {name}  ")["access_token"]
    finally:
        _cleanup(engine, name)


def test_signup_rejects_duplicate_username(client, engine):
    name = _signup_name()
    try:
        assert client.post("/auth/signup", json={"username": name}).status_code == 201
        assert client.post("/auth/signup", json={"username": name}).status_code == 409
    finally:
        _cleanup(engine, name)


def test_signup_rejects_blank_username(client):
    assert client.post("/auth/signup", json={"username": "   "}).status_code == 422


def test_me_returns_token_owner(client, engine):
    username = _signup_name()
    try:
        tokens = client.post("/auth/signup", json={"username": username}).json()

        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})

        assert resp.status_code == 200
        assert resp.json()["username"] == username
    finally:
        _cleanup(engine, username)


def test_me_without_token_is_401(client):
    assert client.get("/auth/me").status_code == 401
