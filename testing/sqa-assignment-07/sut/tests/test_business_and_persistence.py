import sqlite3

from app import create_app
from conftest import login, mutate, state


def test_allowed_actions_change_real_business_data(client):
    token = login(client)
    before = state(client)["business"]
    forged = mutate(client, "POST", "/api/actions/forge_sword", token, {})
    assert forged.status_code == 200, forged.get_json()
    assert state(client)["business"]["swords"] == before["swords"] + 1
    token = login(client, "hans")
    before = state(client)["business"]
    paid = mutate(client, "POST", "/api/actions/open_vault", token, {})
    assert paid.status_code == 200, paid.get_json()
    assert state(client)["business"]["gold"] == before["gold"] - 100
    assert mutate(client, "POST", "/api/actions/audit_ledger", token, {}).status_code == 403
    token = login(client, "agnes")
    before = state(client)["business"]
    assert mutate(client, "POST", "/api/actions/audit_ledger", token, {}).status_code == 200
    assert state(client)["business"]["audits"] == before["audits"] + 1


def test_denied_action_has_persistent_trace_but_no_business_write(client):
    token = login(client)
    before = state(client)["business"]
    result = mutate(client, "POST", "/api/actions/open_vault", token, {})
    event = result.get_json()["event"]
    assert result.status_code == 403
    assert state(client)["business"] == before
    audit = client.get("/api/events").get_json()["events"]
    saved = next(e for e in audit if e["id"] == event["id"])
    assert saved["allowed"] in (False, 0)
    assert len(saved["logs"]) >= 3


def test_audit_scope_is_actor_only_unless_admin(app, client):
    token = login(client)
    event = mutate(client, "POST", "/api/actions/forge_sword", token, {}).get_json()["event"]
    other = app.test_client()
    login(other, "hans")
    assert event["id"] not in {e["id"] for e in other.get("/api/events").get_json()["events"]}
    login(other, "wenceslas")
    assert event["id"] in {e["id"] for e in other.get("/api/events").get_json()["events"]}


def test_restarting_factory_preserves_policy_business_and_audit(app, client):
    token = login(client, "wenceslas")
    response = mutate(client, "PUT", "/api/admin/users/1/roles", token, {"role_ids": [2]})
    assert response.status_code == 200, response.get_json()
    before_policy = client.get("/api/admin/state").get_json()
    token = login(client)
    result = mutate(client, "POST", "/api/actions/deploy_cavalry", token, {})
    assert result.status_code == 200, result.get_json()
    event_id = result.get_json()["event"]["id"]
    before_business = state(client)["business"]
    restarted = create_app({"TESTING": True, "DATABASE": app.config["DATABASE"], "SECRET_KEY": "isolated-test-secret"})
    new_client = restarted.test_client()
    login(new_client)
    assert state(new_client)["business"] == before_business
    assert state(new_client)["direct_role_ids"] == [2]
    assert event_id in {e["id"] for e in new_client.get("/api/events").get_json()["events"]}
    login(new_client, "wenceslas")
    assert new_client.get("/api/admin/state").get_json()["role_permission_map"] == before_policy["role_permission_map"]


def test_database_integrity_and_foreign_keys(app):
    with sqlite3.connect(app.config["DATABASE"]) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
