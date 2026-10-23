"""Display migrations preserve customized installations and historical evidence."""
import sqlite3

from app import create_app
from rbac.db import connect_database, _seed_database, _migrate_v2, _migrate_kuttenberg_v1
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
        before = {table: db.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()
                  for table in preserved_tables}
        db.execute('DROP TABLE theme_meta')
    config = {'TESTING':True, 'DATABASE':str(database), 'SECRET_KEY':'migration-test'}
    create_app(config)
    with connect_database(database) as db:
        for table, records in before.items():
            assert db.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall() == records
        assert db.execute("SELECT title FROM ui_resource WHERE code='menu:treasury'").fetchone()[0] == '我的银库'
        assert db.execute("SELECT description FROM ui_resource WHERE code='menu:treasury'").fetchone()[0].startswith('意大利宫')
        assert db.execute("SELECT display_name FROM sys_user WHERE username='hans'").fetchone()[0] == '自定义人物'
        assert db.execute("SELECT name FROM sys_role WHERE code='AUDITOR'").fetchone()[0] == '自定义审计角色'
        assert db.execute("SELECT content FROM kingdom_document WHERE slug='royal_ledger'").fetchone()[0] == '自定义密账'
        assert db.execute("SELECT name FROM sys_permission WHERE code='btn:open_vault'").fetchone()[0] == '拨付运银经费'
        assert db.execute('SELECT max(version) FROM schema_meta').fetchone()[0] == 2
        assert db.execute('SELECT version FROM theme_meta').fetchone()[0] == 2
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


def test_theme_v1_translation_upgrade_changes_only_known_defaults(tmp_path):
    from pathlib import Path
    import json

    database = tmp_path / 'theme-v1.sqlite3'
    with connect_database(database) as db:
        db.executescript(Path('rbac/schema.sql').read_text(encoding='utf-8'))
        _seed_database(db)
        _migrate_v2(db)
        _migrate_kuttenberg_v1(db)
        assert db.execute('SELECT version FROM theme_meta').fetchone()[0] == 1
        db.execute("UPDATE ui_resource SET description='自定义库滕贝格市政说明' WHERE code='menu:square'")
        db.execute("UPDATE ui_resource SET title='私人库滕贝格档案' WHERE code='menu:armory'")
        db.execute("UPDATE sys_permission SET description='保留库滕贝格教学注释' WHERE code='page:tax_scroll'")
        db.execute("UPDATE kingdom_document SET content='自定义库滕贝格护送路线' WHERE slug='defense_map'")
        db.execute("UPDATE kingdom_document SET title='定制库滕贝格税单' WHERE slug='tax_scroll'")
        db.execute('UPDATE policy_state SET revision=9 WHERE id=1')
        db.execute('UPDATE kingdom_state SET gold=3100 WHERE id=1')
        db.execute("INSERT INTO business_ledger(event_id,actor_id,action,amount,description,created_at) "
                   "VALUES ('v1-business',3,'open_vault',-100,'库滕贝格旧版金币支出','2026-01-01')")
        db.execute("INSERT INTO audit_event VALUES ('v1-audit',3,'ACTION_REQUEST','btn:open_vault',1,"
                   "'库滕贝格旧审计结论','[]','2026-01-01')")
        protected = ('sys_user', 'sys_role', 'sys_user_role', 'sys_role_permission',
                     'sys_role_hierarchy', 'kingdom_state', 'business_ledger',
                     'audit_event', 'policy_state', 'accounting_baseline',
                     'audit_report', 'schema_meta')
        before = {table: db.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()
                  for table in protected}
        permission_ids = db.execute('SELECT id,code,type FROM sys_permission ORDER BY id').fetchall()
        resource_ids = db.execute('SELECT code,kind,slug,territory FROM ui_resource ORDER BY code').fetchall()
    config = {'TESTING':True, 'DATABASE':str(database), 'SECRET_KEY':'translation-test'}
    create_app(config)
    with connect_database(database) as db:
        assert db.execute('SELECT version FROM theme_meta').fetchone()[0] == 2
        for table, previous in before.items():
            assert db.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall() == previous
        assert db.execute('SELECT id,code,type FROM sys_permission ORDER BY id').fetchall() == permission_ids
        assert db.execute('SELECT code,kind,slug,territory FROM ui_resource ORDER BY code').fetchall() == resource_ids
        assert db.execute("SELECT title FROM ui_resource WHERE code='menu:square'").fetchone()[0] == '库滕堡市政广场'
        assert db.execute("SELECT description FROM ui_resource WHERE code='menu:square'").fetchone()[0] == '自定义库滕贝格市政说明'
        assert db.execute("SELECT title FROM ui_resource WHERE code='menu:armory'").fetchone()[0] == '私人库滕贝格档案'
        assert db.execute("SELECT name FROM sys_permission WHERE code='menu:square'").fetchone()[0] == '库滕堡市政广场'
        assert db.execute("SELECT description FROM sys_permission WHERE code='page:tax_scroll'").fetchone()[0] == '保留库滕贝格教学注释'
        tax = db.execute("SELECT title,content FROM kingdom_document WHERE slug='tax_scroll'").fetchone()
        assert tax['title'] == '定制库滕贝格税单'
        assert tax['content'].startswith('库滕堡市政公开告示')
        defense = db.execute("SELECT content,records_json FROM kingdom_document WHERE slug='defense_map'").fetchone()
        assert defense['content'] == '自定义库滕贝格护送路线'
        records = json.loads(defense['records_json'])
        assert records[1]['据点'] == '库滕堡外城交接点（教学）'
        after = {table: db.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()
                 for table in ('ui_resource', 'sys_permission', 'kingdom_document', 'theme_meta')}
    create_app(config)
    with connect_database(database) as db:
        for table, previous in after.items():
            assert db.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall() == previous


def test_fresh_theme_and_real_page_templates_use_kuttenburg_translation(client):
    login(client, 'wenceslas')
    current = state(client)
    menu = next(item for item in current['resources']['menus'] if item['slug'] == 'square')
    assert menu['title'] == '库滕堡市政广场'
    for path in ('/', '/pages/tax_scroll', '/pages/defense_map'):
        response = client.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert '库滕堡' in html
        assert '库滕贝格' not in html


def test_completed_theme_v2_does_not_rewrite_later_customization(app):
    with connect_database(app.config['DATABASE']) as db:
        db.execute("UPDATE ui_resource SET title='库滕贝格市政广场' WHERE code='menu:square'")
        db.execute("UPDATE kingdom_document SET content='用户保留库滕贝格译名' WHERE slug='tax_scroll'")
        previous_revision = db.execute('SELECT revision FROM policy_state').fetchone()[0]
    create_app({'TESTING':True, 'DATABASE':app.config['DATABASE'], 'SECRET_KEY':'repeat-theme'})
    with connect_database(app.config['DATABASE']) as db:
        assert db.execute("SELECT title FROM ui_resource WHERE code='menu:square'").fetchone()[0] == '库滕贝格市政广场'
        assert db.execute("SELECT content FROM kingdom_document WHERE slug='tax_scroll'").fetchone()[0] == '用户保留库滕贝格译名'
        assert db.execute('SELECT revision FROM policy_state').fetchone()[0] == previous_revision
        assert db.execute('SELECT version FROM theme_meta').fetchone()[0] == 2
        assert db.execute('SELECT max(version) FROM schema_meta').fetchone()[0] == 2
