"""固定随机种子的性质测试，记录实际产生的例子和 HTTP 次数。"""
import sqlite3

from hypothesis import HealthCheck, given, seed, settings, strategies as st

from conftest import login, request_json, snapshot_database


COMMON = dict(deadline=None, database=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
UNICODE_SCALAR = st.characters(exclude_categories=("Cs",))
SCALARS = st.one_of(st.none(), st.booleans(), st.integers(min_value=-(10**30), max_value=10**30),
                    st.floats(allow_nan=False, allow_infinity=False), st.text(UNICODE_SCALAR, max_size=30))
JSON_VALUE = st.recursive(SCALARS, lambda child: st.one_of(st.lists(child, max_size=6),
                              st.dictionaries(st.text(UNICODE_SCALAR, max_size=10), child, max_size=6)), max_leaves=15)


@seed(2026102801)
@settings(max_examples=300, **COMMON)
@given(value=JSON_VALUE)
def test_Q01_random_role_payload_is_rejected_or_activates_only_assigned(app, client, property_counter, value):
    if not property_counter["examples"]:
        property_counter["token"] = login(client)
    token = property_counter["token"]
    property_counter["examples"] += 1
    property_counter["http_operations"] += 1
    before = snapshot_database(app)
    active_before = client.get("/api/state").get_json()["active_role_ids"]
    response = request_json(client, "POST", "/api/session/roles", token, {"role_ids": value})
    typed_array = (isinstance(value, list) and len(value) <= 100
                   and all(type(item) is int and 0 < item <= 2**63-1 for item in value))
    valid_array = typed_array and len(set(value)) == len(value)
    expected = 400 if not valid_array else 200 if set(value) <= {1} else 403
    assert response.status_code == expected, (value, response.get_json())
    actual = client.get("/api/state").get_json()["active_role_ids"]
    assert actual == (value if expected == 200 else active_before)
    assert snapshot_database(app) == before


@seed(2026102802)
@settings(max_examples=300, **COMMON)
@given(value=JSON_VALUE)
def test_Q02_random_policy_revision_never_changes_data_on_rejection(app, client, property_counter, value):
    if not property_counter["examples"]:
        property_counter["token"] = login(client, "wenceslas")
    token = property_counter["token"]
    property_counter["examples"] += 1
    property_counter["http_operations"] += 1
    before = snapshot_database(app)
    current_revision = before["policy_state"][0][1]
    response = request_json(client, "PUT", "/api/admin/users/1/roles", token,
                            {"role_ids": [1], "base_revision": value})
    expected = (400 if type(value) is not int or value < 1
                else 200 if value == current_revision else 409)
    assert response.status_code == expected, (value, response.get_json())
    if expected == 200:
        assert response.get_json()["policy_revision"] == current_revision + 1
    else:
        assert snapshot_database(app) == before


@seed(2026102803)
@settings(max_examples=300, **COMMON)
@given(value=JSON_VALUE)
def test_Q03_random_root_json_preserves_type_contract(app, client, property_counter, value):
    if not property_counter["examples"]:
        property_counter["token"] = login(client)
    token = property_counter["token"]
    property_counter["examples"] += 1
    property_counter["http_operations"] += 1
    before = snapshot_database(app)
    # data= distinguishes the JSON literal null from no request body.
    import json
    response = client.post("/api/actions/forge_sword", data=json.dumps(value),
                           content_type="application/json", headers={"X-CSRF-Token": token})
    assert response.status_code == (200 if isinstance(value, dict) else 400), (value, response.get_json())
    if not isinstance(value, dict):
        assert snapshot_database(app) == before
    else:
        after = snapshot_database(app)
        assert after["kingdom_state"][0][1] == before["kingdom_state"][0][1] + 1
        assert len(after["business_ledger"]) == len(before["business_ledger"]) + 1


USER_ACTIONS = {
    "henry": {"forge_sword"},
    "radzig": {"forge_sword", "deploy_cavalry"},
    "hans": {"forge_sword", "open_vault"},
    "wenceslas": {"forge_sword", "deploy_cavalry"},
    "agnes": {"forge_sword", "audit_ledger"},
}
OPERATION = st.tuples(st.sampled_from(list(USER_ACTIONS)),
                      st.sampled_from(["forge_sword", "deploy_cavalry", "open_vault", "audit_ledger"]))


@seed(2026102804)
@settings(max_examples=200, **COMMON)
@given(operations=st.lists(OPERATION, min_size=1, max_size=16))
def test_Q04_random_user_operation_sequences_preserve_balance_and_ledger(app, property_counter, operations):
    if not property_counter["examples"]:
        sessions = {}
        for user in USER_ACTIONS:
            browser = app.test_client()
            sessions[user] = (browser, login(browser, user))
        property_counter["sessions"] = sessions
    sessions = property_counter["sessions"]
    property_counter["examples"] += 1
    with sqlite3.connect(app.config["DATABASE"]) as db:
        db.execute("DELETE FROM audit_report")
        db.execute("DELETE FROM business_ledger")
        db.execute("UPDATE kingdom_state SET swords=24,cavalry_deployments=0,gold=3200,audits=0")
    expected = {"swords": 24, "cavalry_deployments": 0, "gold": 3200, "audits": 0}
    success = 0
    for user, slug in operations:
        property_counter["http_operations"] += 1
        browser, token = sessions[user]
        response = request_json(browser, "POST", f"/api/actions/{slug}", token, {})
        allowed = slug in USER_ACTIONS[user]
        assert response.status_code == (200 if allowed else 403), (user, slug, response.get_json())
        if allowed:
            success += 1
            if slug == "forge_sword":
                expected["swords"] += 1
            elif slug == "deploy_cavalry":
                expected["cavalry_deployments"] += 1
            elif slug == "open_vault":
                expected["gold"] -= 100
            else:
                expected["audits"] += 1
                report = response.get_json()["audit_report"]
                assert (report["expected_gold"], report["actual_gold"], report["difference"]) == (expected["gold"], expected["gold"], 0)
            assert response.get_json()["business"] == expected
    with sqlite3.connect(app.config["DATABASE"]) as db:
        assert db.execute("SELECT swords,cavalry_deployments,gold,audits FROM kingdom_state").fetchone() == tuple(expected.values())
        assert db.execute("SELECT COUNT(*) FROM business_ledger").fetchone()[0] == success
        spent = db.execute("SELECT COALESCE(SUM(-amount),0) FROM business_ledger WHERE action='open_vault'").fetchone()[0]
        assert expected["gold"] == 3200 - spent
        assert expected["gold"] >= 0
