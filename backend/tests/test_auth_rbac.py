"""JWT 발급 + 3역할(retail/pro/admin) RBAC 테스트 (docs/infra-spec.md 7단계).

라우터 본체는 501이어도 되지만, 그 앞의 인증/인가 체인은 실제로 걸려야
한다는 것이 이번 단계의 요구사항이다:
  - 토큰 없음 -> 401
  - 역할 불일치 -> 403
  - 정상 토큰 + 허용된 역할 -> (본체 미구현이므로) 501
  - refresh 토큰으로 access 토큰을 재발급
"""
from __future__ import annotations


def _login(client, email: str, password: str) -> dict:
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_login_rejects_wrong_password(client, make_user, test_password):
    email, _ = make_user("retail")

    resp = client.post("/auth/login", json={"email": email, "password": "wrong-password"})

    assert resp.status_code == 401


def test_login_rejects_unknown_email(client):
    resp = client.post("/auth/login", json={"email": "nobody@example.com", "password": "x"})

    assert resp.status_code == 401


def test_login_issues_access_and_refresh_tokens(client, make_user, test_password):
    email, _ = make_user("retail")

    tokens = _login(client, email, test_password)

    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"]
    assert tokens["refresh_token"]


def test_refresh_issues_new_access_token(client, make_user, test_password):
    email, _ = make_user("retail")
    tokens = _login(client, email, test_password)

    resp = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert resp.status_code == 200
    assert resp.json()["access_token"]


def test_refresh_rejects_access_token(client, make_user, test_password):
    """refresh 엔드포인트에 access 토큰을 넣으면 거부되어야 한다 (type 혼용 방지)."""
    email, _ = make_user("retail")
    tokens = _login(client, email, test_password)

    resp = client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})

    assert resp.status_code == 401


def test_protected_endpoint_without_token_is_401(client):
    resp = client.get("/specs/spec-does-not-exist")

    assert resp.status_code == 401


def test_protected_endpoint_with_garbage_token_is_401(client):
    resp = client.get("/specs/spec-does-not-exist", headers={"Authorization": "Bearer not-a-jwt"})

    assert resp.status_code == 401


def test_any_authenticated_role_reaches_stub_body(client, make_user, test_password):
    """retail/pro/admin 모두 spec 라우터를 통과해 501(미구현)까지 도달해야 한다."""
    for role in ("retail", "pro", "admin"):
        email, _ = make_user(role)
        tokens = _login(client, email, test_password)

        resp = client.get(
            "/specs/spec-does-not-exist",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

        assert resp.status_code == 501, f"role={role} 는 spec 라우터를 통과해야 한다"


def test_admin_router_rejects_retail_role(client, make_user, test_password):
    email, _ = make_user("retail")
    tokens = _login(client, email, test_password)

    resp = client.get(
        "/admin/hardcap-versions",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert resp.status_code == 403


def test_admin_router_accepts_admin_role(client, make_user, test_password):
    email, _ = make_user("admin")
    tokens = _login(client, email, test_password)

    resp = client.get(
        "/admin/hardcap-versions",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert resp.status_code == 501  # 인가는 통과했고, 본체가 미구현이라 501


# --- 회원가입 -------------------------------------------------------------
# 계정을 만드는 경로가 없어서 한동안 DB 에 직접 INSERT 해야 했다. 여기서 보는 것은
# "만들어진 계정이 곧바로 쓸 수 있는가" 와 "권한을 요청으로 고를 수 없는가" 둘이다.


def _signup_email() -> str:
    import uuid

    return f"signup-test-{uuid.uuid4().hex[:8]}@example.com"


def _cleanup(engine, email: str) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})


def test_signup_creates_usable_account(client, engine):
    email = _signup_email()
    try:
        resp = client.post("/auth/signup", json={"email": email, "password": "test1234"})

        assert resp.status_code == 201, resp.text
        assert resp.json()["access_token"]

        # 발급받은 토큰이 실제로 인증을 통과한다(본체가 없으므로 501 이면 통과한 것이다).
        token = resp.json()["access_token"]
        me = client.get("/profile/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 501

        # 같은 자격으로 로그인도 된다 — 해시가 제대로 저장됐다는 뜻이다.
        assert _login(client, email, "test1234")["access_token"]
    finally:
        _cleanup(engine, email)


def test_signup_rejects_duplicate_email(client, engine):
    email = _signup_email()
    try:
        first = client.post("/auth/signup", json={"email": email, "password": "test1234"})
        assert first.status_code == 201

        second = client.post("/auth/signup", json={"email": email, "password": "another12"})

        assert second.status_code == 409
    finally:
        _cleanup(engine, email)


def test_signup_rejects_short_password(client):
    resp = client.post("/auth/signup", json={"email": _signup_email(), "password": "short"})

    assert resp.status_code == 422


def test_signup_ignores_requested_role(client, engine):
    """요청에 role 을 실어도 무시하고 retail 로 만든다 — 아무나 관리자가 되면 안 된다."""
    email = _signup_email()
    try:
        resp = client.post(
            "/auth/signup",
            json={"email": email, "password": "test1234", "role": "admin"},
        )
        assert resp.status_code == 201

        token = resp.json()["access_token"]
        forbidden = client.get(
            "/admin/hardcap-versions", headers={"Authorization": f"Bearer {token}"}
        )
        assert forbidden.status_code == 403
    finally:
        _cleanup(engine, email)
