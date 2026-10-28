"""Confirmed original failures, retained as post-fix regression requirements."""
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'sut'))
from app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'regression.sqlite3'),
                       'SECRET_KEY': 'sqa-regression-isolated-secret'})


def _login(app, username='wenceslas'):
    client = app.test_client()
    token = client.get('/api/bootstrap').get_json()['csrf_token']
    result = client.post('/api/login', json={'username': username, 'password': 'Kingdom@2026'},
                         headers={'X-CSRF-Token': token})
    assert result.status_code == 200, result.get_json()
    return client, result.get_json()['csrf_token']


def _record(case_id, expected, actual, **detail):
    target = os.environ.get('SQA_REGRESSION_RECORD')
    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        records = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        records.append({'case_id': case_id, 'expected_status': expected, 'actual_status': actual,
                        'passed': expected == actual, **detail})
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.mark.parametrize('route,payload', [
    ('users', {'role_ids': [1]}),
    ('roles', {'permission_ids': [1, 4, 7]}),
])
@pytest.mark.parametrize('identifier', [2**63, 10**100])
def test_url_identifier_outside_sqlite_range_returns_400(app, route, payload, identifier):
    client, token = _login(app)
    before = client.get('/api/admin/state').get_json()
    suffix = 'roles' if route == 'users' else 'permissions'
    result = client.put(f'/api/admin/{route}/{identifier}/{suffix}',
                        json={**payload, 'base_revision': before['policy_revision']},
                        headers={'X-CSRF-Token': token})
    after = client.get('/api/admin/state').get_json()
    assert after['policy_revision'] == before['policy_revision']
    assert after['user_role_map'] == before['user_role_map']
    assert after['role_permission_map'] == before['role_permission_map']
    _record(f'REG-ID-{route}-{identifier}', 400, result.status_code,
            code=result.get_json().get('code'), policy_unchanged=True)
    assert result.status_code == 400, result.get_json()


@pytest.mark.parametrize('constant', ['NaN', 'Infinity', '-Infinity'])
def test_nonstandard_json_constant_rejected_without_business_write(app, constant):
    client, token = _login(app, 'henry')
    before = client.get('/api/state').get_json()['business']
    result = client.post('/api/actions/forge_sword', data='{"note":' + constant + '}',
                         content_type='application/json', headers={'X-CSRF-Token': token})
    after = client.get('/api/state').get_json()['business']
    _record('REG-JSON-' + constant, 400, result.status_code,
            business_before=before, business_after=after, business_unchanged=before == after)
    assert result.status_code == 400, result.get_json()
    assert after == before


@pytest.mark.parametrize('field,escaped', [
    ('username', '\\ud800'), ('username', '\\udfff'), ('password', '\\ud800'),
])
def test_unpaired_unicode_surrogate_login_returns_400(app, field, escaped):
    client = app.test_client()
    token = client.get('/api/bootstrap').get_json()['csrf_token']
    username = '"' + escaped + '"' if field == 'username' else '"henry"'
    password = '"' + escaped + '"' if field == 'password' else '"Kingdom@2026"'
    raw = '{"username":' + username + ',"password":' + password + '}'
    result = client.post('/api/login', data=raw, content_type='application/json',
                         headers={'X-CSRF-Token': token})
    _record('REG-UNICODE-' + field + '-' + escaped, 400, result.status_code,
            code=result.get_json().get('code'))
    assert client.get('/api/state').status_code == 401
    assert result.status_code == 400, result.get_json()


def test_logged_out_cookie_cannot_be_replayed_and_other_session_remains(app):
    client, token = _login(app)
    other, _ = _login(app)
    cookie = client.get_cookie('session').value
    assert client.post('/api/logout', json={}, headers={'X-CSRF-Token': token}).status_code == 200
    assert client.get('/api/state').status_code == 401
    other_status = other.get('/api/state').status_code
    assert other_status == 200
    replay = app.test_client()
    replay.set_cookie('session', cookie)
    result = replay.get('/api/state')
    _record('REG-SESSION-REPLAY', 401, result.status_code,
            other_session_status=other_status,
            replay_username=result.get_json().get('user', {}).get('username'))
    assert result.status_code == 401, result.get_json()
    assert replay.get('/api/bootstrap').get_json()['authenticated'] is False
