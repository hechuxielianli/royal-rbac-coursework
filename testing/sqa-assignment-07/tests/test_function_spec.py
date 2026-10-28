"""按用户可见能力逐项验证，再用独立规则检查权限交互。"""
import sqlite3

import pytest

from conftest import login, policy_put, request_json, snapshot_database


def test_F01_login_logout_and_csrf_rotation(app, client):
    bootstrap = client.get("/api/bootstrap").get_json()
    assert bootstrap["authenticated"] is False
    assert len(bootstrap["users"]) == 5
    assert "password_hash" not in str(bootstrap)
    old_token = bootstrap["csrf_token"]
    failed = request_json(client, "POST", "/api/login", old_token,
                          {"username": "henry", "password": "wrong"})
    assert (failed.status_code, failed.get_json()["code"]) == (401, "LOGIN_FAILED")
    assert client.get("/api/state").status_code == 401
    token = login(client)
    assert token != old_token
    assert request_json(client, "POST", "/api/actions/forge_sword", old_token, {}).status_code == 403
    logged_out = request_json(client, "POST", "/api/logout", token, {})
    assert logged_out.status_code == 200
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/bootstrap").get_json()["authenticated"] is False


@pytest.mark.parametrize("username,slug,column,delta", [
    pytest.param("henry", "forge_sword", "swords", 1, id="F02-forge"),
    pytest.param("radzig", "deploy_cavalry", "cavalry_deployments", 1, id="F03-deploy"),
    pytest.param("hans", "open_vault", "gold", -100, id="F04-pay"),
    pytest.param("agnes", "audit_ledger", "audits", 1, id="F05-audit"),
])
def test_F02_F05_business_operation_has_one_effect_and_saved_actor(app, client, username, slug, column, delta):
    token = login(client, username)
    before = client.get("/api/state").get_json()["business"]
    response = request_json(client, "POST", f"/api/actions/{slug}", token, {})
    assert response.status_code == 200
    data = response.get_json()
    expected = {**before, column: before[column] + delta}
    assert data["business"] == expected
    with sqlite3.connect(app.config["DATABASE"]) as db:
        records = db.execute("SELECT u.username,b.action,b.amount FROM business_ledger b "
                             "JOIN sys_user u ON u.id=b.actor_id WHERE b.event_id=?",
                             (data["event"]["id"],)).fetchall()
        assert records == [(username, slug, delta)]
        assert db.execute("SELECT allowed FROM audit_event WHERE id=?",
                          (data["event"]["id"],)).fetchone() == (1,)
    if slug == "audit_ledger":
        report = data["audit_report"]
        assert (report["expected_gold"], report["actual_gold"], report["difference"], report["consistent"]) == (3200, 3200, 0, True)


@pytest.mark.parametrize("username,allowed_pages,allowed_actions", [
    pytest.param("henry", {"tax_scroll"}, {"forge_sword"}, id="S01-peasant"),
    pytest.param("radzig", {"tax_scroll", "defense_map"}, {"forge_sword", "deploy_cavalry"}, id="S02-captain"),
    pytest.param("hans", {"tax_scroll", "royal_ledger"}, {"forge_sword", "open_vault"}, id="S03-treasurer"),
    pytest.param("wenceslas", {"tax_scroll", "defense_map", "royal_ledger"}, {"forge_sword", "deploy_cavalry"}, id="S04-king"),
    pytest.param("agnes", {"tax_scroll", "royal_ledger"}, {"forge_sword", "audit_ledger"}, id="S05-auditor"),
])
def test_S01_S05_fixed_access_matrix(app, client, username, allowed_pages, allowed_actions):
    """允许集合来自本次规格表，不调用被测角色闭包函数作为预期。"""
    token = login(client, username)
    for slug in ("tax_scroll", "defense_map", "royal_ledger"):
        response = client.get(f"/api/pages/{slug}")
        assert response.status_code == (200 if slug in allowed_pages else 403), (username, slug, response.get_json())
        if slug not in allowed_pages:
            assert "records" not in response.get_json()
    for slug in ("forge_sword", "deploy_cavalry", "open_vault", "audit_ledger"):
        before = snapshot_database(app)
        response = request_json(client, "POST", f"/api/actions/{slug}", token, {})
        assert response.status_code == (200 if slug in allowed_actions else 403), (username, slug, response.get_json())
        if slug not in allowed_actions:
            assert snapshot_database(app) == before


def test_F06_event_feed_separates_user_and_administrator(app):
    henry, hans, king = [app.test_client() for _ in range(3)]
    token = login(henry)
    login(hans, "hans")
    login(king, "wenceslas")
    event_id = request_json(henry, "POST", "/api/actions/forge_sword", token, {}).get_json()["event"]["id"]
    assert event_id in {e["id"] for e in henry.get("/api/events").get_json()["events"]}
    assert event_id not in {e["id"] for e in hans.get("/api/events").get_json()["events"]}
    assert event_id in {e["id"] for e in king.get("/api/events").get_json()["events"]}


def test_S06_new_role_requires_activation_and_revocation_is_immediate(app):
    king, target = app.test_client(), app.test_client()
    admin_token, user_token = login(king, "wenceslas"), login(target)
    changed = policy_put(king, admin_token, "/api/admin/users/1/roles", {"role_ids": [1, 2]})
    assert changed.status_code == 200
    assert target.get("/api/state").get_json()["active_role_ids"] == [1]
    assert request_json(target, "POST", "/api/actions/deploy_cavalry", user_token, {}).status_code == 403
    assert request_json(target, "POST", "/api/session/roles", user_token, {"role_ids": [2]}).status_code == 200
    assert request_json(target, "POST", "/api/actions/deploy_cavalry", user_token, {}).status_code == 200
    assert policy_put(king, admin_token, "/api/admin/users/1/roles", {"role_ids": [1]}).status_code == 200
    before = snapshot_database(app)
    denied = request_json(target, "POST", "/api/actions/deploy_cavalry", user_token, {})
    assert (denied.status_code, denied.get_json()["code"]) == (403, "PERMISSION_DENIED")
    assert target.get("/api/state").get_json()["active_role_ids"] == []
    assert snapshot_database(app) == before


@pytest.mark.parametrize("permissions,menu_status,page_status,action_status", [
    pytest.param([2], 200, 403, 403, id="S07-menu-only"),
    pytest.param([5], 403, 200, 403, id="S08-page-only"),
    pytest.param([8], 403, 403, 200, id="S09-button-only"),
])
def test_S07_S09_menu_page_button_are_independent(app, permissions, menu_status, page_status, action_status):
    king, target = app.test_client(), app.test_client()
    token = login(king, "wenceslas")
    assert policy_put(king, token, "/api/admin/roles/1/permissions", {"permission_ids": permissions}).status_code == 200
    user_token = login(target)
    assert target.get("/api/navigation/armory").status_code == menu_status
    assert target.get("/api/pages/defense_map").status_code == page_status
    assert request_json(target, "POST", "/api/actions/deploy_cavalry", user_token, {}).status_code == action_status


@pytest.mark.parametrize("kind", ["assignment", "permissions", "hierarchy"], ids=["S10-assignment", "S11-permissions", "S12-hierarchy"])
def test_S10_S12_stale_policy_rejected_without_lost_update(app, client, kind):
    token = login(client, "wenceslas")
    old = client.get("/api/admin/state").get_json()["policy_revision"]
    first = policy_put(client, token, "/api/admin/users/1/roles", {"role_ids": [2]}, revision=old)
    assert first.status_code == 200
    before = snapshot_database(app)
    paths = {
        "assignment": ("/api/admin/users/1/roles", {"role_ids": [1]}),
        "permissions": ("/api/admin/roles/1/permissions", {"permission_ids": []}),
        "hierarchy": ("/api/admin/hierarchy", {"edges": []}),
    }
    path, data = paths[kind]
    denied = policy_put(client, token, path, data, revision=old)
    assert (denied.status_code, denied.get_json()["code"]) == (409, "STALE_POLICY")
    assert denied.get_json()["policy_revision"] == old + 1
    assert snapshot_database(app) == before


def test_S13_mutually_exclusive_roles_rollback(app, client):
    token = login(client, "wenceslas")
    before = snapshot_database(app)
    denied = policy_put(client, token, "/api/admin/users/1/roles", {"role_ids": [3, 5]})
    assert (denied.status_code, denied.get_json()["code"]) == (409, "SSD_CONFLICT")
    assert snapshot_database(app) == before


def test_S14_cycle_rollback(app, client):
    token = login(client, "wenceslas")
    before = snapshot_database(app)
    denied = policy_put(client, token, "/api/admin/hierarchy", {"edges": [{"senior_id": 1, "junior_id": 2}, {"senior_id": 2, "junior_id": 1}]})
    assert (denied.status_code, denied.get_json()["code"]) == (409, "HIERARCHY_CYCLE")
    assert snapshot_database(app) == before
