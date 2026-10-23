import pytest

from app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "test.sqlite3"),
        "SECRET_KEY": "isolated-test-secret",
    })


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, username="henry"):
    token = client.get("/api/bootstrap").get_json()["csrf_token"]
    response = client.post("/api/login", json={
        "username": username, "password": "Kingdom@2026"
    }, headers={"X-CSRF-Token": token})
    assert response.status_code == 200, response.get_json()
    return client.get("/api/state").get_json()["csrf_token"]


def mutate(client, method, path, token, data):
    if method == 'PUT' and path.startswith('/api/admin/') and 'base_revision' not in data:
        snapshot = client.get('/api/admin/state')
        if snapshot.status_code == 200:
            data = {**data, 'base_revision':snapshot.get_json()['policy_revision']}
    return client.open(path, method=method, json=data,
                       headers={"X-CSRF-Token": token})


def state(client):
    response = client.get("/api/state")
    assert response.status_code == 200
    return response.get_json()
