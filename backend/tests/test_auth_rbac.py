"""JWT 발급 + 3역할(retail/pro/admin) RBAC 테스트 (docs/infra-spec.md 7단계).

라우터 본체는 501이어도 되지만, 그 앞의 인증/인가 체인은 실제로 걸려야
한다는 것이 이번 단계의 요구사항이다:
  - 토큰 없음 -> 401
  - 역할 불일치 -> 403
  - 정상 토큰 + 허용된 역할 -> (본체 미구현이므로) 501
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
    email, _ = make_user("retail")

    tokens = _login(client, email)

    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"]
    assert tokens["refresh_token"]


def test_refresh_issues_new_access_token(client, make_user):
    email, _ = make_user("retail")
    tokens = _login(client, email)

    resp = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert resp.status_code == 200
    assert resp.json()["access_token"]


def test_refresh_rejects_access_token(client, make_user):
    """refresh 엔드포인트에 access 토큰을 넣으면 거부되어야 한다 (type 혼용 방지)."""
    email, _ = make_user("retail")
    tokens = _login(client, email)

    resp = client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})

    assert resp.status_code == 401


def test_protected_endpoint_without_token_is_401(client):
    resp = client.get("/specs/spec-does-not-exist")

    assert resp.status_code == 401


def test_protected_endpoint_with_garbage_token_is_401(client):
    resp = client.get("/specs/spec-does-not-exist", headers={"Authorization": "Bearer not-a-jwt"})

    assert resp.status_code == 401


def test_any_authenticated_role_reaches_stub_body(client, make_user):
    """retail/pro/admin 모두 spec 라우터를 통과해 본체(없는 전략서 → 404)까지 도달해야 한다."""
    for role in ("retail", "pro", "admin"):
        email, _ = make_user(role)
        tokens = _login(client, email)

        resp = client.get(
            "/specs/spec-does-not-exist",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

        assert resp.status_code == 404, f"role={role} 는 spec 라우터를 통과해야 한다"


def test_admin_router_rejects_retail_role(client, make_user):
    email, _ = make_user("retail")
    tokens = _login(client, email)

    resp = client.get(
        "/admin/hardcap-versions",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert resp.status_code == 403


def test_admin_router_accepts_admin_role(client, make_user):
    email, _ = make_user("admin")
    tokens = _login(client, email)

    resp = client.get(
        "/admin/hardcap-versions",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert resp.status_code == 501  # 인가는 통과했고, 본체가 미구현이라 501


# --- 회원가입 -------------------------------------------------------------
# 입력은 형식 제한 없는 username 하나. 보는 것은 "곧바로 쓸 수 있는가",
# "중복을 막는가", "권한을 요청으로 고를 수 없는가" 셋이다.


def _signup_name() -> str:
    import uuid

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
        # 갓 가입한 계정이라 확정된 성향이 없으므로 404 — 401/403 이 아니면 인증은 지나갔다.
        token = resp.json()["access_token"]
        me = client.get("/profile/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 404

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


def test_signup_ignores_requested_role(client, engine):
    """요청에 role 을 실어도 무시하고 retail 로 만든다 — 아무나 관리자가 되면 안 된다."""
    name = _signup_name()
    try:
        resp = client.post("/auth/signup", json={"username": name, "role": "admin"})
        assert resp.status_code == 201

        token = resp.json()["access_token"]
        forbidden = client.get(
            "/admin/hardcap-versions", headers={"Authorization": f"Bearer {token}"}
        )
        assert forbidden.status_code == 403
    finally:
        _cleanup(engine, name)


def test_me_returns_token_owner(client):
    username = f"me-check-{uuid.uuid4().hex[:8]}"
    tokens = client.post("/auth/signup", json={"username": username}).json()

    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})

    assert resp.status_code == 200
    assert resp.json()["username"] == username


def test_me_without_token_is_401(client):
    assert client.get("/auth/me").status_code == 401
