"""The logout change protects every entry point and retains independent sessions."""
import sqlite3
import pytest

from conftest import login, request_json


@pytest.mark.parametrize('method,path', [
    ('GET','/api/state'), ('GET','/api/events'), ('GET','/api/admin/state'),
    ('GET','/api/navigation/square'), ('GET','/api/pages/tax_scroll'),
    ('GET','/pages/tax_scroll'), ('POST','/api/actions/forge_sword'),
    ('POST','/api/session/roles'), ('POST','/api/logout'),
])
def test_R01_revoked_session_denied_on_all_entry_points(app, client, method, path):
    token = login(client, 'wenceslas')
    cookie = client.get_cookie('session').value
    assert request_json(client,'POST','/api/logout',token,{}).status_code == 200
    replay = app.test_client()
    replay.set_cookie('session',cookie)
    result = request_json(replay,method,path,token,{'role_ids':[4]})
    assert result.status_code == 401


def test_R02_expired_server_session_denied_even_with_valid_signed_cookie(app, client):
    login(client)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute('UPDATE auth_session SET expires_at=0')
    assert client.get('/api/bootstrap').get_json()['authenticated'] is False
    assert client.get('/api/state').status_code == 401


def test_R03_session_registry_stores_hash_and_relogin_replaces_current_session(app, client):
    login(client)
    first = client.get_cookie('session').value
    with sqlite3.connect(app.config['DATABASE']) as conn:
        key = conn.execute('SELECT session_id FROM auth_session').fetchone()[0]
        assert len(key) == 64 and all(c in '0123456789abcdef' for c in key)
    login(client)
    replay = app.test_client()
    replay.set_cookie('session',first)
    assert replay.get('/api/state').status_code == 401
    assert client.get('/api/state').status_code == 200
    with sqlite3.connect(app.config['DATABASE']) as conn:
        assert conn.execute('SELECT COUNT(*) FROM auth_session').fetchone()[0] == 1
