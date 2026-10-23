"""Display migrations preserve customized installations and historical evidence."""
import sqlite3

from app import create_app
from rbac.db import connect_database, _seed_database, _migrate_v2
from conftest import login, mutate, state


def test_v2_display_upgrade_preserves_customizations_and_history(tmp_path):
    from pathlib import Path
    database = tmp_path / 'old-v2.sqlite3'
    with connect_database(database) as db:
        schema = Path('rbac/schema.sql').read_text(encoding='utf-8')
        db.executescript(schema)
        _seed_database(db)
        _migrate_v2(db)
        db.execute("UPDATE ui_resource SET title='我的银库' WHERE code='menu:treasury'")
        db.execute("UPDATE sys_user SET display_name='自定义人物' WHERE username='hans'")
        db.execute("UPDATE sys_role SET name='自定义审计角色' WHERE code='AUDITOR'")
        db.execute("UPDATE kingdom_document SET content='自定义密账' WHERE slug='royal_ledger'")
        db.execute('UPDATE kingdom_state SET gold=3100 WHERE id=1')
        db.execute('UPDATE policy_state SET revision=7 WHERE id=1')
        # A historical description must remain exactly as originally recorded.
        db.execute("INSERT INTO business_ledger(event_id,actor_id,action,amount,description,created_at) "
                   "VALUES ('old-event',3,'open_vault',-100,'旧版金币支出','2026-01-01')")
        db.execute('INSERT INTO sys_role_permission VALUES (1,8)')
        preserved_tables = ('sys_user_role', 'sys_role_permission', 'sys_role_hierarchy',
                            'kingdom_state', 'business_ledger', 'audit_event',
                            'policy_state', 'accounting_baseline', 'audit_report')
        before = {table: db.execute(f'SELECT * FROM {table} ORDER BY 1,2').fetchall()
                  for table in preserved_tables}
        db.execute('DROP TABLE theme_meta')
    config = {'TESTING':True, 'DATABASE':str(database), 'SECRET_KEY':'migration-test'}
    create_app(config)
    with connect_database(database) as db:
        for table, records in before.items():
            assert db.execute(f'SELECT * FROM {table} ORDER BY 1,2').fetchall() == records
        assert db.execute("SELECT title FROM ui_resource WHERE code='menu:treasury'").fetchone()[0] == '我的银库'
        assert db.execute("SELECT description FROM ui_resource WHERE code='menu:treasury'").fetchone()[0].startswith('意大利宫')
        assert db.execute("SELECT display_name FROM sys_user WHERE username='hans'").fetchone()[0] == '自定义人物'
        assert db.execute("SELECT name FROM sys_role WHERE code='AUDITOR'").fetchone()[0] == '自定义审计角色'
        assert db.execute("SELECT content FROM kingdom_document WHERE slug='royal_ledger'").fetchone()[0] == '自定义密账'
        assert db.execute("SELECT name FROM sys_permission WHERE code='btn:open_vault'").fetchone()[0] == '拨付运银经费'
        assert db.execute('SELECT max(version) FROM schema_meta').fetchone()[0] == 2
        assert db.execute('SELECT version FROM theme_meta').fetchone()[0] == 1
        db.execute("UPDATE ui_resource SET subtitle='迁移后定制' WHERE code='menu:square'")
    create_app(config)
    with connect_database(database) as db:
        assert db.execute("SELECT subtitle FROM ui_resource WHERE code='menu:square'").fetchone()[0] == '迁移后定制'


def test_theme_catalog_preserves_codes_and_business_semantics(app, client):
    token = login(client, 'hans')
    initial = state(client)
    assert initial['user']['display_name'] == '汉斯·卡蓬'
    assert '教学扮演' in initial['user']['title']
    assert len(initial['permissions']) == 13
    actions = {item['slug']:item for item in initial['resources']['actions']}
    assert set(actions) == {'forge_sword','deploy_cavalry','open_vault','audit_ledger'}
    assert actions['open_vault']['title'] == '拨付运银经费'
    paid = mutate(client, 'POST', '/api/actions/open_vault', token, {})
    assert paid.status_code == 200
    assert paid.get_json()['business']['gold'] == initial['business']['gold'] - 100
    assert '100格罗申' in paid.get_json()['message']
    token = login(client, 'agnes')
    audited = mutate(client, 'POST', '/api/actions/audit_ledger', token, {}).get_json()['audit_report']
    assert audited['consistent'] is True
    assert audited['expected_gold'] == audited['actual_gold'] == initial['business']['gold'] - 100
    assert '格罗申' in client.get('/pages/royal_ledger').get_data(as_text=True)


def test_anti_smuggling_decree_rejects_ssd_without_changing_assignment(app, client):
    token = login(client, 'wenceslas')
    rejected = mutate(client, 'PUT', '/api/admin/users/1/roles', token, {'role_ids':[3,5]})
    assert rejected.status_code == 409
    assert rejected.get_json()['code'] == 'SSD_CONFLICT'
    assert '皇家反走私法令' in rejected.get_json()['message']
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT role_id FROM sys_user_role WHERE user_id=1').fetchall() == [(1,)]
