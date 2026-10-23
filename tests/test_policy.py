from conftest import login, mutate, state


def admin_state(client):
    response = client.get("/api/admin/state")
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_direct_ssd_conflict_rolls_back(client):
    token = login(client, "wenceslas")
    before = admin_state(client)
    response = mutate(client, "PUT", "/api/admin/users/1/roles", token, {"role_ids": [3, 5]})
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["event"]["allowed"] is False
    assert admin_state(client)["user_role_map"] == before["user_role_map"]


def test_inherited_ssd_conflict_rolls_back_hierarchy(client):
    token = login(client, "wenceslas")
    before = admin_state(client)
    edges = before["hierarchy"] + [{"senior_id": 4, "junior_id": 3}, {"senior_id": 4, "junior_id": 5}]
    response = mutate(client, "PUT", "/api/admin/hierarchy", token, {"edges": edges})
    assert response.status_code == 409, response.get_json()
    assert admin_state(client)["hierarchy"] == before["hierarchy"]


def test_cycle_is_rejected_and_original_policy_remains(client):
    token = login(client, "wenceslas")
    before = admin_state(client)
    edges = before["hierarchy"] + [{"senior_id": 1, "junior_id": 4}]
    response = mutate(client, "PUT", "/api/admin/hierarchy", token, {"edges": edges})
    assert response.status_code == 409, response.get_json()
    assert admin_state(client)["hierarchy"] == before["hierarchy"]


def test_valid_diamond_hierarchy_deduplicates_roles(client):
    token = login(client, "wenceslas")
    before = admin_state(client)
    # KING reaches PEASANT through both CAPTAIN and TREASURER.
    edges = before["hierarchy"] + [{"senior_id": 4, "junior_id": 3}]
    response = mutate(client, "PUT", "/api/admin/hierarchy", token, {"edges": edges})
    assert response.status_code == 200, response.get_json()
    roles = state(client)["effective_roles"]
    assert {r["id"] for r in roles} == {1, 2, 3, 4}
    assert len(roles) == 4


def test_permission_matrix_cannot_bypass_duty_separation(client):
    token = login(client, "wenceslas")
    before = admin_state(client)
    audit_id = next(p["id"] for p in before["permissions"] if p["code"] == "btn:audit_ledger")
    ids = before["role_permission_map"]["3"] + [audit_id]
    response = mutate(client, "PUT", "/api/admin/roles/3/permissions", token, {"permission_ids": ids})
    assert response.status_code == 409, response.get_json()
    assert admin_state(client)["role_permission_map"] == before["role_permission_map"]


def test_last_administrator_cannot_be_removed(client):
    token = login(client, "wenceslas")
    before = admin_state(client)
    candidate = [pid for pid in before['role_permission_map']['4'] if pid != 11]
    response = mutate(client, "PUT", "/api/admin/roles/4/permissions", token, {"permission_ids": candidate})
    assert response.status_code == 409, response.get_json()
    assert response.get_json()['code'] == 'LAST_ADMINISTRATOR'
    assert admin_state(client)['role_permission_map'] == before['role_permission_map']
    assert admin_state(client)["user_role_map"]["4"] == [4]


def test_permission_changes_apply_to_existing_session_immediately(app, client):
    peasant = app.test_client()
    peasant_token = login(peasant)
    token = login(client, "wenceslas")
    policy = admin_state(client)
    assert mutate(peasant, "POST", "/api/actions/forge_sword", peasant_token, {}).status_code == 200
    ids = [p["id"] for p in policy["permissions"]
           if p["id"] in policy["role_permission_map"]["1"] and p["code"] != "btn:forge_sword"]
    assert mutate(client, "PUT", "/api/admin/roles/1/permissions", token, {"permission_ids": ids}).status_code == 200
    assert mutate(peasant, "POST", "/api/actions/forge_sword", peasant_token, {}).status_code == 403


def test_assignment_revocation_removes_stale_active_role(app, client):
    captain = app.test_client()
    captain_token = login(captain, "radzig")
    token = login(client, "wenceslas")
    assert mutate(client, "PUT", "/api/admin/users/2/roles", token, {"role_ids": [1]}).status_code == 200
    assert mutate(captain, "POST", "/api/actions/deploy_cavalry", captain_token, {}).status_code == 403
    assert 2 not in state(captain)["active_role_ids"]


def test_invalid_foreign_key_does_not_partially_write(client):
    token = login(client, "wenceslas")
    before = admin_state(client)
    response = mutate(client, "PUT", "/api/admin/users/1/roles", token, {"role_ids": [1, 999]})
    assert response.status_code == 400, response.get_json()
    assert admin_state(client)["user_role_map"] == before["user_role_map"]
