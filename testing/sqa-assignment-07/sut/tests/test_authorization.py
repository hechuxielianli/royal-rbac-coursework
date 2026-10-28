import pytest

from conftest import login, mutate, state


@pytest.mark.parametrize("path", ["/api/state", "/api/events", "/api/admin/state",
                                      "/api/pages/defense_map", "/api/navigation/treasury"])
def test_anonymous_cannot_read_protected_resources(client, path):
    assert client.get(path).status_code == 401


def test_password_required_and_no_hash_disclosure(client):
    bootstrap = client.get("/api/bootstrap").get_json()
    assert len(bootstrap["users"]) == 5
    assert "password_hash" not in str(bootstrap)
    response = mutate(client, "POST", "/api/login", bootstrap["csrf_token"],
                      {"username": "wenceslas", "password": "wrong"})
    assert response.status_code == 401
    assert client.get("/api/state").status_code == 401


def test_csrf_is_required_for_mutations(client):
    token = login(client)
    before = state(client)["business"]
    for headers in ({}, {"X-CSRF-Token": "forged"}):
        response = client.post("/api/actions/forge_sword", json={}, headers=headers)
        assert response.status_code == 403
    assert state(client)["business"] == before
    assert mutate(client, "POST", "/api/actions/forge_sword", token, {}).status_code == 200


def test_client_actor_cannot_impersonate_king(client):
    token = login(client)
    before = state(client)["business"]
    response = mutate(client, "POST", "/api/actions/deploy_cavalry", token,
                      {"user_id": 4, "actor_id": 4, "username": "wenceslas"})
    assert response.status_code == 403
    assert response.get_json()["event"]["allowed"] is False
    assert state(client)["business"] == before


def test_non_ascii_csrf_header_returns_denial_without_server_error(client):
    login(client)
    before = state(client)["business"]
    response = client.post("/api/actions/forge_sword", json={},
                           headers={"X-CSRF-Token": "é"})
    assert response.status_code == 403
    assert response.get_json()["code"] == "CSRF_FAILED"
    assert state(client)["business"] == before


@pytest.mark.parametrize("method,path,payload", [
    ("PUT", "/api/admin/users/1/roles", {"role_ids": [4]}),
    ("PUT", "/api/admin/roles/1/permissions", {"permission_ids": []}),
    ("PUT", "/api/admin/hierarchy", {"edges": []}),
])
def test_administration_rejects_peasant(client, method, path, payload):
    token = login(client)
    assert mutate(client, method, path, token, payload).status_code == 403
    assert state(client)["direct_role_ids"] == [1]


def test_role_inheritance_and_no_universal_king(client):
    token = login(client, "wenceslas")
    snapshot = state(client)
    assert {r["code"] for r in snapshot["effective_roles"]} == {"KING", "CAPTAIN", "PEASANT"}
    assert {"btn:forge_sword", "btn:deploy_cavalry", "admin:policy"} <= set(snapshot["permission_codes"])
    assert "btn:open_vault" not in snapshot["permission_codes"]
    assert "btn:audit_ledger" not in snapshot["permission_codes"]
    assert mutate(client, "POST", "/api/actions/open_vault", token, {}).status_code == 403


def test_real_page_routes_are_protected_and_do_not_leak(client):
    login(client)
    assert client.get("/api/pages/tax_scroll").status_code == 200
    denied_api = client.get("/api/pages/defense_map")
    assert denied_api.status_code == 403
    assert "records" not in denied_api.get_json()
    denied_page = client.get("/pages/defense_map")
    assert denied_page.status_code == 403
    assert client.get("/pages/tax_scroll").status_code == 200


def test_menu_permission_is_independent_from_page_permission(client):
    token = login(client, "wenceslas")
    policy = client.get("/api/admin/state").get_json()
    page_id = next(p["id"] for p in policy["permissions"] if p["code"] == "page:defense_map")
    current = policy["role_permission_map"]["1"]
    response = mutate(client, "PUT", "/api/admin/roles/1/permissions", token,
                      {"permission_ids": current + [page_id]})
    assert response.status_code == 200, response.get_json()
    login(client, "henry")
    assert client.get("/api/navigation/armory").status_code == 403
    assert client.get("/api/pages/defense_map").status_code == 200


def test_session_can_only_activate_assigned_roles(client):
    token = login(client)
    denied = mutate(client, "POST", "/api/session/roles", token, {"role_ids": [4]})
    assert denied.status_code in (400, 403)
    assert state(client)["active_role_ids"] == [1]
    assert mutate(client, "POST", "/api/session/roles", token, {"role_ids": []}).status_code == 200
    assert state(client)["permission_codes"] == []
    assert mutate(client, "POST", "/api/actions/forge_sword", token, {}).status_code == 403
    assert mutate(client, "POST", "/api/session/roles", token, {"role_ids": [1]}).status_code == 200
    assert mutate(client, "POST", "/api/actions/forge_sword", token, {}).status_code == 200


def test_logout_invalidates_authenticated_access(client):
    token = login(client)
    assert mutate(client, "POST", "/api/logout", token, {}).status_code == 200
    assert client.get("/api/state").status_code == 401


@pytest.mark.parametrize("path", ["/api/pages/unknown", "/api/navigation/unknown"])
def test_unknown_resources_fail_closed(client, path):
    login(client)
    assert client.get(path).status_code == 404
