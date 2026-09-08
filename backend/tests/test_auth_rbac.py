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
