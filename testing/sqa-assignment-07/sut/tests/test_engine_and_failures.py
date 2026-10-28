from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from conftest import login, mutate, state
from rbac.events import Event, EventBus
from rbac.policy import DomainError


def test_event_without_listener_is_denied():
    event = Event("UNREGISTERED_EVENT", actor_id=1, target="unknown")
    with pytest.raises(DomainError):
        EventBus().publish(event, None)
    assert event.allowed is False


@pytest.mark.parametrize("role_ids", [[True], ["1"], [1, 1], [-1], None, "1"])
def test_invalid_ids_do_not_mutate_policy(client, role_ids):
    token = login(client, "wenceslas")
    before = client.get("/api/admin/state").get_json()["user_role_map"]
    response = mutate(client, "PUT", "/api/admin/users/1/roles", token, {"role_ids": role_ids})
    assert response.status_code == 400, response.get_json()
    assert client.get("/api/admin/state").get_json()["user_role_map"] == before


def test_audit_failure_rolls_back_business(app, client):
    token = login(client)
    before = state(client)["business"]
    with sqlite3.connect(app.config["DATABASE"]) as db:
        db.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON audit_event "
                   "BEGIN SELECT RAISE(ABORT, 'simulated audit failure'); END")
    response = mutate(client, "POST", "/api/actions/forge_sword", token, {})
    assert response.status_code == 503, response.get_json()
    assert state(client)["business"] == before
    with sqlite3.connect(app.config["DATABASE"]) as db:
        assert db.execute("SELECT count(*) FROM business_ledger").fetchone()[0] == 0


def test_concurrent_payments_do_not_overdraw_treasury(app):
    clients = [app.test_client(), app.test_client()]
    tokens = [login(client, "hans") for client in clients]
    with sqlite3.connect(app.config["DATABASE"]) as db:
        db.execute("UPDATE kingdom_state SET gold=100 WHERE id=1")

    def pay(index):
        return mutate(clients[index], "POST", "/api/actions/open_vault", tokens[index], {}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(pay, [0, 1]))
    assert statuses == [200, 409]
    with sqlite3.connect(app.config["DATABASE"]) as db:
        assert db.execute("SELECT gold FROM kingdom_state WHERE id=1").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM business_ledger WHERE action='open_vault'").fetchone()[0] == 1
