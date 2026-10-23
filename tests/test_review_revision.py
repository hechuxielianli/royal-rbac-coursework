"""Regression cases for the 2026-10-23 review contract.

These tests use isolated fixture databases. Policy requests deliberately carry
the revision read from their own management snapshot; stale-update tests retain
the old value rather than relying on the shared helper to refresh it.
"""
import json
import sqlite3

import pytest

from app import create_app
from conftest import login, state
from rbac.policy import DomainError


def snapshot(client):
    response = client.get('/api/admin/state')
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def request_json(client, method, path, token, data):
    return client.open(path, method=method, json=data,
                       headers={'X-CSRF-Token': token})


def policy_put(client, path, token, data, revision=None):
    if revision is None:
        revision = snapshot(client)['policy_revision']
    return request_json(client, 'PUT', path, token, {**data, 'base_revision': revision})


def partial_actor(app, capability, *, role_id=2):
    """The trusted king delegates one management ability through CAPTAIN."""
    king = app.test_client()
    token = login(king, 'wenceslas')
    before = snapshot(king)
    permission_id = next(p['id'] for p in before['permissions'] if p['code'] == capability)
    result = policy_put(king, f'/api/admin/roles/{role_id}/permissions', token,
                        {'permission_ids': before['role_permission_map'][str(role_id)] + [permission_id]})
    assert result.status_code == 200, result.get_json()
    result = policy_put(king, '/api/admin/users/1/roles', token, {'role_ids': [role_id]})
    assert result.status_code == 200, result.get_json()
    actor = app.test_client()
    return actor, login(actor), king, token


@pytest.mark.parametrize('username,user_id', [('henry', 1), ('wenceslas', 4)])
def test_even_trusted_admin_cannot_assign_own_roles(app, username, user_id):
    if username == 'henry':
        actor, token, _, _ = partial_actor(app, 'admin:assign')
    else:
        actor, token = app.test_client(), None
        token = login(actor, username)
    before = snapshot(actor)
    result = policy_put(actor, f'/api/admin/users/{user_id}/roles', token, {'role_ids': [1, 4]})
    assert result.status_code == 403, result.get_json()
    after = snapshot(actor)
    assert after['user_role_map'] == before['user_role_map']
    assert after['policy_revision'] == before['policy_revision']


def test_original_assign_self_coronation_attack_stops_before_activation(app):
    actor, token, _, _ = partial_actor(app, 'admin:assign', role_id=1)
    before = state(actor)
    result = policy_put(actor, '/api/admin/users/1/roles', token, {'role_ids': [1, 4]})
    assert result.status_code == 403, result.get_json()
    activation = request_json(actor, 'POST', '/api/session/roles', token, {'role_ids': [1, 4]})
    assert activation.status_code == 403, activation.get_json()
    assert state(actor)['direct_role_ids'] == before['direct_role_ids']
    assert set(snapshot(actor)['capabilities']) == {'admin:assign'}


@pytest.mark.parametrize('role_ids', [[4], [3], [2]])
def test_partial_assign_cannot_delegate_management_or_uncovered_business(app, role_ids):
    actor, token, _, _ = partial_actor(app, 'admin:assign')
    before = snapshot(actor)
    result = policy_put(actor, '/api/admin/users/3/roles', token, {'role_ids': role_ids})
    assert result.status_code == 403, result.get_json()
    assert snapshot(actor)['user_role_map'] == before['user_role_map']


def test_partial_assign_can_delegate_covered_nonmanagement_role(app):
    actor, token, _, _ = partial_actor(app, 'admin:assign')
    result = policy_put(actor, '/api/admin/users/3/roles', token, {'role_ids': [1]})
    assert result.status_code == 200, result.get_json()
    assert snapshot(actor)['user_role_map']['3'] == [1]


def test_partial_assign_cannot_revoke_another_administrators_capabilities(app):
    actor, token, king, king_token = partial_actor(app, 'admin:assign')
    assert policy_put(king, '/api/admin/users/3/roles', king_token, {'role_ids': [4]}).status_code == 200
    before = snapshot(actor)
    result = policy_put(actor, '/api/admin/users/3/roles', token, {'role_ids': [1]})
    assert result.status_code == 403, result.get_json()
    assert snapshot(actor)['user_role_map'] == before['user_role_map']


@pytest.mark.parametrize('role_id', [3, 5])
def test_trusted_king_can_delegate_finance_without_holding_its_business_power(client, role_id):
    token = login(client, 'wenceslas')
    assert 'btn:open_vault' not in state(client)['permission_codes']
    assert 'btn:audit_ledger' not in state(client)['permission_codes']
    result = policy_put(client, '/api/admin/users/1/roles', token, {'role_ids': [role_id]})
    assert result.status_code == 200, result.get_json()
    assert snapshot(client)['user_role_map']['1'] == [role_id]


@pytest.mark.parametrize('capability', ['admin:assign', 'admin:policy', 'admin:audit'])
def test_admin_snapshot_is_accessible_and_trimmed_by_active_capability(app, capability):
    actor, token, _, _ = partial_actor(app, capability)
    data = snapshot(actor)
    assert set(data['capabilities']) == {capability}
    assert type(data['policy_revision']) is int
    if capability == 'admin:assign':
        assert {'users', 'roles', 'user_role_map'} <= data.keys()
        assert not {'permissions', 'role_permission_map', 'hierarchy', 'constraints'} & data.keys()
    elif capability == 'admin:policy':
        assert {'roles', 'permissions', 'role_permission_map', 'hierarchy', 'constraints'} <= data.keys()
        assert not {'users', 'user_role_map'} & data.keys()
    else:
        assert not {'users', 'roles', 'user_role_map', 'permissions', 'role_permission_map', 'hierarchy', 'constraints'} & data.keys()
        assert actor.get('/api/events').status_code == 200
    assert request_json(actor, 'POST', '/api/session/roles', token, {'role_ids': []}).status_code == 200
    assert actor.get('/api/admin/state').status_code == 403


@pytest.mark.parametrize('change', ['self_admin_grant', 'other_role_admin_grant', 'uncovered_business_grant'])
def test_partial_policy_cannot_mint_capabilities_or_uncovered_permissions(app, change):
    actor, token, _, _ = partial_actor(app, 'admin:policy')
    before = snapshot(actor)
    target = 2 if change == 'self_admin_grant' else 1
    # A read permission avoids an unrelated finance/audit mutex rejection.
    code = 'page:royal_ledger' if change == 'uncovered_business_grant' else 'admin:assign'
    permission_id = next(p['id'] for p in before['permissions'] if p['code'] == code)
    result = policy_put(actor, f'/api/admin/roles/{target}/permissions', token,
                        {'permission_ids': before['role_permission_map'][str(target)] + [permission_id]})
    assert result.status_code == 403, result.get_json()
    after = snapshot(actor)
    assert after['role_permission_map'] == before['role_permission_map']
    assert after['policy_revision'] == before['policy_revision']


def test_partial_policy_cannot_remove_management_power(app):
    actor, token, _, _ = partial_actor(app, 'admin:policy')
    before = snapshot(actor)
    codes = {p['id']: p['code'] for p in before['permissions']}
    requested = [pid for pid in before['role_permission_map']['2'] if not codes[pid].startswith('admin:')]
    result = policy_put(actor, '/api/admin/roles/2/permissions', token, {'permission_ids': requested})
    assert result.status_code == 403, result.get_json()
    assert snapshot(actor)['role_permission_map'] == before['role_permission_map']


@pytest.mark.parametrize('operation', ['add', 'remove'])
def test_partial_policy_cannot_change_redundant_direct_admin_grant(app, operation):
    king = app.test_client()
    king_token = login(king, 'wenceslas')
    initial = snapshot(king)
    policy_id = next(p['id'] for p in initial['permissions'] if p['code'] == 'admin:policy')
    # CAPTAIN already receives this capability through PEASANT. Changing a
    # duplicate direct grant would alter persistence after future revocation,
    # even though today's effective permission sets would remain identical.
    assert policy_put(king, '/api/admin/roles/1/permissions', king_token,
                      {'permission_ids': initial['role_permission_map']['1'] + [policy_id]}).status_code == 200
    if operation == 'remove':
        configured = snapshot(king)
        assert policy_put(king, '/api/admin/roles/2/permissions', king_token,
                          {'permission_ids': configured['role_permission_map']['2'] + [policy_id]}).status_code == 200
    actor = app.test_client()
    token = login(actor, 'radzig')
    before = snapshot(actor)
    assert set(before['capabilities']) == {'admin:policy'}
    direct = before['role_permission_map']['2']
    candidate = direct + [policy_id] if operation == 'add' else [pid for pid in direct if pid != policy_id]
    result = policy_put(actor, '/api/admin/roles/2/permissions', token, {'permission_ids': candidate})
    assert result.status_code == 403, result.get_json()
    after = snapshot(actor)
    assert after['role_permission_map'] == before['role_permission_map']
    assert after['policy_revision'] == before['policy_revision']


@pytest.mark.parametrize('operation', ['add', 'remove'])
def test_partial_policy_cannot_change_redundant_admin_inheritance_edge(app, operation):
    king = app.test_client()
    king_token = login(king, 'wenceslas')
    initial = snapshot(king)
    policy_id = next(p['id'] for p in initial['permissions'] if p['code'] == 'admin:policy')
    assert policy_put(king, '/api/admin/roles/1/permissions', king_token,
                      {'permission_ids': initial['role_permission_map']['1'] + [policy_id]}).status_code == 200
    redundant = {'senior_id': 5, 'junior_id': 2}
    if operation == 'remove':
        configured = snapshot(king)
        assert policy_put(king, '/api/admin/hierarchy', king_token,
                          {'edges': configured['hierarchy'] + [redundant]}).status_code == 200
    actor = app.test_client()
    token = login(actor, 'radzig')
    before = snapshot(actor)
    assert set(before['capabilities']) == {'admin:policy'}
    edges = before['hierarchy']
    candidate = edges + [redundant] if operation == 'add' else [edge for edge in edges if edge != redundant]
    # AUDITOR and CAPTAIN both already inherit policy from PEASANT. There is
    # no SSD conflict: CAPTAIN has military duties and no treasury payment.
    result = policy_put(actor, '/api/admin/hierarchy', token, {'edges': candidate})
    assert result.status_code == 403, result.get_json()
    after = snapshot(actor)
    assert after['hierarchy'] == before['hierarchy']
    assert after['policy_revision'] == before['policy_revision']


def test_partial_policy_cannot_inherit_trusted_admin(app):
    actor, token, _, _ = partial_actor(app, 'admin:policy')
    before = snapshot(actor)
    # Remove KING->CAPTAIN to avoid a cycle; the candidate remains a DAG.
    edges = [e for e in before['hierarchy'] if e != {'senior_id': 4, 'junior_id': 2}]
    edges.append({'senior_id': 2, 'junior_id': 4})
    result = policy_put(actor, '/api/admin/hierarchy', token, {'edges': edges})
    assert result.status_code == 403, result.get_json()
    assert snapshot(actor)['hierarchy'] == before['hierarchy']


def test_partial_policy_cannot_change_unused_role_admin_closure(app):
    actor, token, king, king_token = partial_actor(app, 'admin:policy')
    assert policy_put(king, '/api/admin/users/5/roles', king_token, {'role_ids': [1]}).status_code == 200
    before = snapshot(actor)
    # AUDITOR is now unassigned. Acquiring CAPTAIN through it would still
    # change a role's management closure and must require the trusted root.
    edges = before['hierarchy'] + [{'senior_id': 5, 'junior_id': 2}]
    result = policy_put(actor, '/api/admin/hierarchy', token, {'edges': edges})
    assert result.status_code == 403, result.get_json()
    assert snapshot(actor)['hierarchy'] == before['hierarchy']


def test_partial_policy_can_distribute_already_held_business_permission(app):
    actor, token, _, _ = partial_actor(app, 'admin:policy')
    before = snapshot(actor)
    military = next(p['id'] for p in before['permissions'] if p['code'] == 'btn:deploy_cavalry')
    result = policy_put(actor, '/api/admin/roles/1/permissions', token,
                        {'permission_ids': before['role_permission_map']['1'] + [military]})
    assert result.status_code == 200, result.get_json()


def test_trusted_delegation_requires_all_management_powers_to_be_active(app):
    actor, _, king, king_token = partial_actor(app, 'admin:assign')
    assert policy_put(king, '/api/admin/users/1/roles', king_token, {'role_ids': [2, 4]}).status_code == 200
    token = login(actor)
    assert {'admin:assign', 'admin:policy', 'admin:audit'} <= set(snapshot(actor)['capabilities'])
    assert request_json(actor, 'POST', '/api/session/roles', token, {'role_ids': [2]}).status_code == 200
    assert set(snapshot(actor)['capabilities']) == {'admin:assign'}
    result = policy_put(actor, '/api/admin/users/2/roles', token, {'role_ids': [4]})
    assert result.status_code == 403, result.get_json()


@pytest.mark.parametrize('revision', [None, True, -1, '0', 1.5])
def test_policy_revision_must_be_explicit_nonnegative_integer(client, revision):
    token = login(client, 'wenceslas')
    before = snapshot(client)
    data = {'role_ids': [2]}
    if revision is not None:
        data['base_revision'] = revision
    result = request_json(client, 'PUT', '/api/admin/users/1/roles', token, data)
    assert result.status_code == 400, result.get_json()
    after = snapshot(client)
    assert after['policy_revision'] == before['policy_revision']
    assert after['user_role_map'] == before['user_role_map']


@pytest.mark.parametrize('kind', ['assignment', 'permissions', 'hierarchy'])
def test_stale_management_snapshot_cannot_overwrite_any_policy_kind(client, kind):
    token = login(client, 'wenceslas')
    stale = snapshot(client)
    first = policy_put(client, '/api/admin/users/1/roles', token, {'role_ids': [2]}, stale['policy_revision'])
    assert first.status_code == 200, first.get_json()
    current = snapshot(client)
    assert current['policy_revision'] == stale['policy_revision'] + 1
    if kind == 'assignment':
        path, data = '/api/admin/users/1/roles', {'role_ids': [1]}
    elif kind == 'permissions':
        path, data = '/api/admin/roles/1/permissions', {'permission_ids': []}
    else:
        path, data = '/api/admin/hierarchy', {'edges': []}
    result = policy_put(client, path, token, data, stale['policy_revision'])
    assert result.status_code == 409, result.get_json()
    assert result.get_json()['code'] == 'STALE_POLICY'
    assert result.get_json()['policy_revision'] == current['policy_revision']
    after = snapshot(client)
    for key in ('user_role_map', 'role_permission_map', 'hierarchy', 'policy_revision'):
        assert after[key] == current[key]


def test_audit_report_reconciles_currency_only_and_tracks_ledger_cutoff(app, client):
    token = login(client)
    assert request_json(client, 'POST', '/api/actions/forge_sword', token, {}).status_code == 200
    token = login(client, 'hans')
    assert request_json(client, 'POST', '/api/actions/open_vault', token, {}).status_code == 200
    token = login(client, 'agnes')
    result = request_json(client, 'POST', '/api/actions/audit_ledger', token, {})
    assert result.status_code == 200, result.get_json()
    report = result.get_json()['audit_report']
    assert {'id', 'opening_gold', 'total_credits', 'total_debits', 'expected_gold', 'actual_gold',
            'difference', 'consistent', 'ledger_count', 'through_ledger_id', 'created_at'} <= report.keys()
    assert report['opening_gold'] == 3200
    assert report['total_credits'] == 0
    assert report['total_debits'] == 100
    assert report['expected_gold'] == report['actual_gold'] == 3100
    assert report['difference'] == 0
    assert report['consistent'] is True
    assert report['ledger_count'] >= 2
    assert report['through_ledger_id'] >= 2


def test_audit_difference_is_a_saved_finding_not_transaction_failure(app, client):
    token = login(client, 'agnes')
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('UPDATE kingdom_state SET gold=gold-17 WHERE id=1')
    before = state(client)['business']
    result = request_json(client, 'POST', '/api/actions/audit_ledger', token, {})
    assert result.status_code == 200, result.get_json()
    report = result.get_json()['audit_report']
    assert report['consistent'] is False
    assert abs(report['difference']) == 17
    assert report['expected_gold'] == 3200
    assert report['actual_gold'] == 3183
    assert state(client)['business']['audits'] == before['audits'] + 1
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT count(*) FROM audit_report WHERE id=?', (report['id'],)).fetchone()[0] == 1


@pytest.mark.parametrize('failing_table', ['audit_report', 'audit_event'])
def test_audit_storage_failure_rolls_back_report_counter_and_ledger(app, client, failing_table):
    token = login(client, 'agnes')
    before = state(client)['business']
    with sqlite3.connect(app.config['DATABASE']) as db:
        counts = {table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                  for table in ('audit_report', 'business_ledger')}
        db.execute(f'CREATE TRIGGER fail_report BEFORE INSERT ON {failing_table} '
                   "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
    result = request_json(client, 'POST', '/api/actions/audit_ledger', token, {})
    assert result.status_code == 503, result.get_json()
    assert result.get_json()['ok'] is False
    assert 'audit_report' not in result.get_json()
    assert state(client)['business'] == before
    with sqlite3.connect(app.config['DATABASE']) as db:
        for table, count in counts.items():
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == count


@pytest.mark.parametrize('corruption', ['permission_conflict', 'foreign_key'])
def test_startup_refuses_manually_corrupted_policy(app, corruption):
    with sqlite3.connect(app.config['DATABASE']) as db:
        if corruption == 'permission_conflict':
            db.execute('INSERT INTO sys_role_permission VALUES (3,10)')
        else:
            db.execute('PRAGMA foreign_keys=OFF')
            db.execute('INSERT INTO sys_user_role VALUES (999,1)')
    with pytest.raises((DomainError, RuntimeError)):
        create_app({'TESTING': True, 'DATABASE': app.config['DATABASE'], 'SECRET_KEY': 'test'})


def test_existing_session_refuses_newly_corrupted_permission_mutex(app, client):
    token = login(client, 'hans')
    before = state(client)['business']
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('INSERT INTO sys_role_permission VALUES (3,10)')
    result = request_json(client, 'POST', '/api/actions/open_vault', token, {})
    assert result.status_code in (403, 409), result.get_json()
    assert result.get_json()['ok'] is False
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT gold FROM kingdom_state WHERE id=1').fetchone()[0] == before['gold']


@pytest.mark.parametrize('endpoint,protected_field', [('/api/events', 'events'),
                                                     ('/api/state', 'permission_codes')])
def test_protected_metadata_read_refuses_runtime_corrupted_permission_mutex(app, client, endpoint, protected_field):
    login(client, 'wenceslas')
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.executemany('INSERT INTO sys_role_permission VALUES (4,?)', [(9,), (10,)])
    result = client.get(endpoint)
    assert result.status_code in (403, 409), result.get_json()
    assert result.get_json()['ok'] is False
    assert protected_field not in result.get_json()


def test_v1_incremental_migration_preserves_existing_policy_business_and_audit(app, client):
    token = login(client)
    assert request_json(client, 'POST', '/api/actions/forge_sword', token, {}).status_code == 200
    token = login(client, 'hans')
    assert request_json(client, 'POST', '/api/actions/open_vault', token, {}).status_code == 200
    database = app.config['DATABASE']
    # Reproduce the v1 table set with real historical rows. The v2 contract is
    # additive; no original table is removed or recreated by this downgrade.
    with sqlite3.connect(database) as db:
        db.execute('INSERT INTO sys_role_permission VALUES (1,8)')
        for table in ('audit_report', 'ui_resource', 'policy_state', 'accounting_baseline'):
            db.execute(f'DROP TABLE {table}')
        db.execute('DELETE FROM schema_meta WHERE version>1')
        before = {table: db.execute(f'SELECT * FROM {table} ORDER BY 1,2').fetchall()
                  for table in ('sys_user', 'sys_user_role', 'sys_role_permission',
                                'sys_role_hierarchy', 'kingdom_state', 'business_ledger', 'audit_event')}
    migrated = create_app({'TESTING': True, 'DATABASE': database, 'SECRET_KEY': 'migration-test'})
    with sqlite3.connect(database) as db:
        for table, records in before.items():
            assert db.execute(f'SELECT * FROM {table} ORDER BY 1,2').fetchall() == records
        assert db.execute('SELECT max(version) FROM schema_meta').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM ui_resource').fetchone()[0] == 10
        assert db.execute('SELECT count(*) FROM accounting_baseline').fetchone()[0] == 1
    auditor = migrated.test_client()
    token = login(auditor, 'agnes')
    result = request_json(auditor, 'POST', '/api/actions/audit_ledger', token, {})
    assert result.status_code == 200, result.get_json()
    assert result.get_json()['audit_report']['consistent'] is True
    before_restart = state(auditor)
    create_app({'TESTING': True, 'DATABASE': database, 'SECRET_KEY': 'migration-test'})
    assert state(auditor)['business'] == before_restart['business']
    assert state(auditor)['policy_revision'] == before_restart['policy_revision']
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT count(*) FROM ui_resource').fetchone()[0] == 10
        assert db.execute('SELECT count(*) FROM accounting_baseline').fetchone()[0] == 1


def test_resource_catalog_has_territories_and_never_carries_protected_body(client):
    login(client)
    data = state(client)
    resources = data['resources']
    assert set(resources) == {'menus', 'pages', 'actions'}
    assert [len(resources[k]) for k in ('menus', 'pages', 'actions')] == [3, 3, 4]
    for kind, records in resources.items():
        for resource in records:
            assert {'slug', 'code', 'territory', 'title', 'description', 'icon'} <= resource.keys()
            assert resource['territory'] in ('square', 'armory', 'treasury')
            assert not {'content', 'records', 'records_json', 'password_hash'} & resource.keys()
            if kind == 'menus':
                assert {'eyebrow', 'subtitle', 'number'} <= resource.keys()
    assert '晨星堡' not in json.dumps(resources, ensure_ascii=False)
    assert 'page:defense_map' in {r['code'] for r in resources['pages']}
    assert client.get('/api/pages/defense_map').status_code == 403


def test_resource_catalog_reads_database_metadata_without_granting_access(app, client):
    login(client)
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("UPDATE ui_resource SET title=?,description=? WHERE code='page:defense_map'",
                   ('目录名称来自数据库', '这只是资源说明，不是军事正文'))
    pages = state(client)['resources']['pages']
    defense = next(item for item in pages if item['code'] == 'page:defense_map')
    assert defense['title'] == '目录名称来自数据库'
    assert defense['description'] == '这只是资源说明，不是军事正文'
    assert client.get('/api/pages/defense_map').status_code == 403


def test_navigation_catalog_is_scoped_without_imposing_menu_on_pages(client):
    token = login(client, 'wenceslas')
    result = client.get('/api/navigation/armory')
    assert result.status_code == 200
    resources = result.get_json()['resources']
    assert all(r['territory'] == 'armory' for group in resources.values() for r in group)
    assert {p['slug'] for p in resources['pages']} == {'defense_map'}
    assert {a['slug'] for a in resources['actions']} == {'deploy_cavalry'}
    before = snapshot(client)
    defense = next(p['id'] for p in before['permissions'] if p['code'] == 'page:defense_map')
    assert policy_put(client, '/api/admin/roles/1/permissions', token,
                      {'permission_ids': before['role_permission_map']['1'] + [defense]}).status_code == 200
    login(client)
    assert client.get('/api/navigation/armory').status_code == 403
    assert client.get('/api/pages/defense_map').status_code == 200


@pytest.mark.parametrize('slug,columns', [('tax_scroll', ['税目', '标准', '周期']),
                                        ('defense_map', ['据点', '驻军', '换防'])])
def test_api_document_column_order_matches_persisted_source(client, slug, columns):
    login(client, 'wenceslas')
    result = client.get(f'/api/pages/{slug}')
    assert result.status_code == 200
    assert list(result.get_json()['records'][0]) == columns


def test_low_cost_security_headers_trusted_hosts_and_healthz(client):
    response = client.get('/healthz')
    assert response.status_code == 200
    assert response.is_json
    home = client.get('/')
    csp = home.headers['Content-Security-Policy']
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert 'camera=()' in home.headers['Permissions-Policy']
    assert client.get('/api/bootstrap', headers={'Host': 'untrusted.example'}).status_code == 400
