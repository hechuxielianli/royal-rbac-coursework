"""本次作业新增测试共用的独立应用和 HTTP 辅助函数。"""
from collections import Counter
from pathlib import Path
import sqlite3
import sys

import pytest

SUT = Path(__file__).resolve().parents[1] / "sut"
sys.path.insert(0, str(SUT))
from app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "sqa.sqlite3"),
        "SECRET_KEY": "sqa-independent-database-secret",
    })


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, username="henry"):
    token = client.get("/api/bootstrap").get_json()["csrf_token"]
    response = request_json(client, "POST", "/api/login", token,
                            {"username": username, "password": "Kingdom@2026"})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["csrf_token"]


def request_json(client, method, path, token, data):
    return client.open(path, method=method, json=data,
                       headers={"X-CSRF-Token": token})


def snapshot_database(app):
    """只冻结业务与策略；拒绝请求新增的审计记录另行验证。"""
    tables = ("kingdom_state", "business_ledger", "audit_report", "policy_state",
              "sys_user_role", "sys_role_permission", "sys_role_hierarchy")
    with sqlite3.connect(app.config["DATABASE"]) as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
                for table in tables}


def policy_put(client, token, path, data, revision=None):
    if revision is None:
        revision = client.get("/api/admin/state").get_json()["policy_revision"]
    return request_json(client, "PUT", path, token, {**data, "base_revision": revision})


@pytest.fixture
def property_counter(request):
    counter = Counter()
    yield counter
    for key in ("examples", "http_operations"):
        request.node.user_properties.append((key, counter[key]))


@pytest.fixture
def authenticated(client):
    return login(client)
