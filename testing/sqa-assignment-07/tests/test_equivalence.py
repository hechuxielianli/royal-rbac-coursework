"""弱健壮等价类用例每次只改变一个输入条件。"""
import sqlite3

import pytest

from conftest import login, request_json, snapshot_database


ROLE_CASES = [
    pytest.param([], 200, None, id="E01-empty-subset"),
    pytest.param([1], 200, None, id="E02-assigned-subset"),
    pytest.param([2], 403, "UNASSIGNED_ROLE", id="E03-unassigned-existing-role"),
    pytest.param([999], 403, "UNASSIGNED_ROLE", id="E04-unknown-role"),
    pytest.param(None, 400, "INVALID_REQUEST", id="E05-null"),
    pytest.param("1", 400, "INVALID_REQUEST", id="E06-string"),
    pytest.param({}, 400, "INVALID_REQUEST", id="E07-object"),
    pytest.param([0], 400, "INVALID_REQUEST", id="E08-zero-id"),
    pytest.param([-1], 400, "INVALID_REQUEST", id="E09-negative-id"),
    pytest.param([1.0], 400, "INVALID_REQUEST", id="E10-float-id"),
    pytest.param([True], 400, "INVALID_REQUEST", id="E11-bool-is-not-id"),
    pytest.param(["1"], 400, "INVALID_REQUEST", id="E12-string-id"),
    pytest.param([[1]], 400, "INVALID_REQUEST", id="E13-nested-id"),
    pytest.param([1, 1], 400, "INVALID_REQUEST", id="E14-duplicate"),
    pytest.param([1, 2, 3, 4, 5], 403, "UNASSIGNED_ROLE", id="E15-five-existing-ids"),
    pytest.param(list(range(1, 101)), 403, "UNASSIGNED_ROLE", id="E16-length-100"),
    pytest.param(list(range(1, 102)), 400, "INVALID_REQUEST", id="E17-length-101"),
]


@pytest.mark.parametrize("roles,status,code", ROLE_CASES)
def test_E01_E17_role_array_partition_and_boundaries(app, client, roles, status, code):
    token = login(client)
    before = snapshot_database(app)
    prior_active = client.get("/api/state").get_json()["active_role_ids"]
    response = request_json(client, "POST", "/api/session/roles", token, {"role_ids": roles})
    assert response.status_code == status, response.get_json()
    if code:
        assert response.get_json()["code"] == code
        assert client.get("/api/state").get_json()["active_role_ids"] == prior_active
    else:
        assert client.get("/api/state").get_json()["active_role_ids"] == roles
    assert snapshot_database(app) == before


@pytest.mark.parametrize("gold,status,remaining", [
    pytest.param(0, 409, 0, id="E18-balance-0"),
    pytest.param(99, 409, 99, id="E19-balance-99"),
    pytest.param(100, 200, 0, id="E20-balance-100"),
    pytest.param(101, 200, 1, id="E21-balance-101"),
])
def test_E18_E21_payment_threshold(app, client, gold, status, remaining):
    token = login(client, "hans")
    with sqlite3.connect(app.config["DATABASE"]) as db:
        db.execute("UPDATE kingdom_state SET gold=? WHERE id=1", (gold,))
        db.execute("UPDATE accounting_baseline SET opening_gold=? WHERE id=1", (gold,))
    before = snapshot_database(app)
    response = request_json(client, "POST", "/api/actions/open_vault", token, {})
    assert response.status_code == status
    with sqlite3.connect(app.config["DATABASE"]) as db:
        assert db.execute("SELECT gold FROM kingdom_state").fetchone()[0] == remaining
        assert db.execute("SELECT COUNT(*) FROM business_ledger").fetchone()[0] == int(status == 200)
    if status == 409:
        assert response.get_json()["code"] == "INSUFFICIENT_GOLD"
        assert snapshot_database(app) == before


@pytest.mark.parametrize("revision,status,code", [
    pytest.param(None, 400, "INVALID_REVISION", id="E22-revision-null"),
    pytest.param(False, 400, "INVALID_REVISION", id="E23-revision-bool"),
    pytest.param(0, 400, "INVALID_REVISION", id="E24-revision-zero"),
    pytest.param(-1, 400, "INVALID_REVISION", id="E25-revision-negative"),
    pytest.param("1", 400, "INVALID_REVISION", id="E26-revision-string"),
    pytest.param(1.0, 400, "INVALID_REVISION", id="E27-revision-float"),
    pytest.param(1, 200, None, id="E28-revision-current"),
    pytest.param(2, 409, "STALE_POLICY", id="E29-revision-future"),
])
def test_E22_E29_revision_partition(app, client, revision, status, code):
    token = login(client, "wenceslas")
    before = snapshot_database(app)
    response = request_json(client, "PUT", "/api/admin/users/1/roles", token,
                            {"role_ids": [2], "base_revision": revision})
    assert response.status_code == status, response.get_json()
    if code:
        assert response.get_json()["code"] == code
        assert snapshot_database(app) == before
    else:
        assert response.get_json()["policy_revision"] == 2


@pytest.mark.parametrize("payload,content_type,status", [
    pytest.param("{}", "application/json", 200, id="E30-object"),
    pytest.param("null", "application/json", 400, id="E31-root-null"),
    pytest.param("[]", "application/json", 400, id="E32-root-array"),
    pytest.param('"abc"', "application/json", 400, id="E33-root-string"),
    pytest.param("{broken", "application/json", 400, id="E34-malformed"),
    pytest.param("{}", "text/plain", 400, id="E35-wrong-media"),
])
def test_E30_E35_json_envelope(app, client, payload, content_type, status):
    token = login(client)
    before = snapshot_database(app)
    response = client.post("/api/actions/forge_sword", data=payload,
                           content_type=content_type, headers={"X-CSRF-Token": token})
    assert response.status_code == status
    if status == 400:
        assert snapshot_database(app) == before


@pytest.mark.parametrize("field,value,status", [
    pytest.param("username", "h" * 80, 401, id="E36-username-80"),
    pytest.param("username", "h" * 81, 400, id="E37-username-81"),
    pytest.param("password", "x" * 200, 401, id="E38-password-200"),
    pytest.param("password", "x" * 201, 400, id="E39-password-201"),
    pytest.param("username", 1, 400, id="E40-username-number"),
    pytest.param("password", None, 400, id="E41-password-null"),
])
def test_E36_E41_credential_boundaries(app, client, field, value, status):
    token = client.get("/api/bootstrap").get_json()["csrf_token"]
    data = {"username": "henry", "password": "Kingdom@2026", field: value}
    before = snapshot_database(app)
    response = request_json(client, "POST", "/api/login", token, data)
    assert response.status_code == status
    assert client.get("/api/state").status_code == 401
    assert snapshot_database(app) == before
