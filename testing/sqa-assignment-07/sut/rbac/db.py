"""SQLite 持久化。初始化幂等，不覆盖已有授权或业务记录。"""
import sqlite3
import json
from pathlib import Path

from flask import current_app, g
from werkzeug.security import generate_password_hash


def connect_database(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 10000')
    return conn


def get_db():
    if 'db' not in g:
        g.db = connect_database(current_app.config['DATABASE'])
    return g.db


def close_db(_error=None):
    conn = g.pop('db', None)
    if conn is not None:
        conn.close()


def rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params)]


def initialize_database(path):
    if str(path) != ':memory:':
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect_database(path)
    try:
        conn.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM schema_meta WHERE version=1').fetchone():
            _seed_database(conn)
        if not conn.execute('SELECT 1 FROM schema_meta WHERE version=2').fetchone():
            _migrate_v2(conn)
        _migrate_kuttenberg_theme(conn)
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('数据库外键损坏，拒绝启动')
        from .policy import validate_policy
        validate_policy(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _seed_database(conn):
    # A version marker is written with seed rows in one transaction.
    password_hash = generate_password_hash('Kingdom@2026')
    conn.executemany('INSERT INTO sys_user VALUES (?,?,?,?,?)', [
        (1, 'henry', '亨利', '斯卡里茨铁匠学徒', password_hash),
        (2, 'radzig', '罗德爵士', '皇家卫队统领', password_hash),
        (3, 'hans', '汉斯', '王国财政大臣', password_hash),
        (4, 'wenceslas', '瓦茨拉夫四世', '波希米亚国王', password_hash),
        (5, 'agnes', '阿格尼丝', '独立皇家审计官', password_hash),
    ])
    conn.executemany('INSERT INTO sys_role VALUES (?,?,?)', [
        (1,'PEASANT','行会平民'), (2,'CAPTAIN','皇家卫队长'),
        (3,'TREASURER','财政大臣'), (4,'KING','至高国王'), (5,'AUDITOR','皇家审计官'),
    ])
    permissions = [
        (1,'menu:square','王城广场','MENU','行会与民生事务入口'),
        (2,'menu:armory','军械与兵营','MENU','军事事务导航入口'),
        (3,'menu:treasury','皇家国库','MENU','财政事务导航入口'),
        (4,'page:tax_scroll','王国赋税告示','PAGE','查阅公开赋税卷轴'),
        (5,'page:defense_map','边疆布防图','PAGE','查阅军事机密卷轴'),
        (6,'page:royal_ledger','皇家国库总账','PAGE','查阅财政账目'),
        (7,'btn:forge_sword','打造行会铁剑','BUTTON','完成一次铁剑生产，库存增加一把'),
        (8,'btn:deploy_cavalry','调遣重装骑兵','BUTTON','记录一次骑兵调遣'),
        (9,'btn:open_vault','拨付国库黄金','BUTTON','拨付100枚金币并记账'),
        (10,'btn:audit_ledger','独立审计账本','BUTTON','生成独立审计记录'),
        (11,'admin:assign','册封臣民角色','ADMIN','管理用户与角色的分配'),
        (12,'admin:policy','修订授权政策','ADMIN','修改角色权限矩阵和继承关系'),
        (13,'admin:audit','查阅全局事件','ADMIN','查阅所有臣民的审计事件'),
    ]
    conn.executemany('INSERT INTO sys_permission VALUES (?,?,?,?,?)', permissions)
    grants = {1:[1,4,7], 2:[2,5,8], 3:[3,6,9], 4:[3,6,11,12,13], 5:[3,6,10]}
    conn.executemany('INSERT INTO sys_role_permission VALUES (?,?)',
                     [(role_id,pid) for role_id,pids in grants.items() for pid in pids])
    conn.executemany('INSERT INTO sys_user_role VALUES (?,?)', [(1,1),(2,2),(3,3),(4,4),(5,5)])
    conn.executemany('INSERT INTO sys_role_hierarchy VALUES (?,?)', [(2,1),(3,1),(4,2),(5,1)])
    conn.execute('INSERT INTO sys_role_mutex VALUES (3,5,?)',
                 ('财政与审计必须由不同臣民承担（静态职责分离 SSD）',))
    conn.execute('INSERT INTO sys_permission_mutex VALUES (9,10,?)',
                 ('拨款与独立审计权限不得集中，防止直接授权绕过职责分离',))
    conn.execute('INSERT INTO kingdom_state(id) VALUES (1)')
    documents = [
        ('tax_scroll','王国赋税告示','王城公开卷轴：行会按季度申报，农户按秋收结算。此卷轴可供所有已获授权的臣民查阅。',
         [{'税目':'行会营业税','标准':'收益的5%','周期':'季度'}, {'税目':'农田什一税','标准':'收成的10%','周期':'秋收'}]),
        ('defense_map','边疆布防机密图','机密军令：北境晨星堡由第七骑兵团驻守，西河关隘执行黎明换防。仅授权军事角色可读取本卷轴。',
         [{'据点':'北境晨星堡','驻军':'第七骑兵团','换防':'卯时'}, {'据点':'西河关隘','驻军':'第三长弓队','换防':'黎明'}]),
        ('royal_ledger','皇家国库总账','本账展示国库当前余额及实际业务流水。拨款与独立审计由不同官职执行，国王仅查阅和管理授权。', []),
    ]
    conn.executemany('INSERT INTO kingdom_document VALUES (?,?,?,?)',
                     [(slug,title,content,json.dumps(records,ensure_ascii=False))
                      for slug,title,content,records in documents])
    conn.execute('INSERT INTO schema_meta VALUES (1)')


def _migrate_v2(conn):
    # v1 has a documented opening balance of 3200; never infer it from a possibly
    # corrupted current balance or overwrite existing grants and business data.
    conn.execute('INSERT OR IGNORE INTO accounting_baseline VALUES (1,3200)')
    conn.execute('INSERT OR IGNORE INTO policy_state VALUES (1,1)')
    entries = [
        ('menu:square','menus','square','square','王城公共广场','行会生产、公开税务与王城日常。','♜',1,'THE GUILD SQUARE','从一柄铁剑，开始王国的一天。','I'),
        ('menu:armory','menus','armory','armory','军械与兵营','军械供给、骑兵调遣与边疆布防。','⚔',2,'THE ROYAL ARMORY','边疆安宁，来自有序调度。','II'),
        ('menu:treasury','menus','treasury','treasury','皇家国库','账目查阅、金币拨付与独立核对。','◈',3,'THE ROYAL TREASURY','每一枚金币，都有它的去向。','III'),
        ('page:tax_scroll','pages','tax_scroll','square','王国赋税告示','公开税目、标准与申报周期。','▤',1,'','',''),
        ('page:defense_map','pages','defense_map','armory','边疆布防机密图','查阅据点、驻军与换防安排。','⚑',2,'','',''),
        ('page:royal_ledger','pages','royal_ledger','treasury','皇家国库总账','读取当前余额与真实业务流水。','▥',3,'','',''),
        ('btn:forge_sword','actions','forge_sword','square','打造行会铁剑','完成生产，铁剑库存增加一柄。','⚒',1,'','',''),
        ('btn:deploy_cavalry','actions','deploy_cavalry','armory','调遣重装骑兵','登记一次骑兵出征。','⚑',2,'','',''),
        ('btn:open_vault','actions','open_vault','treasury','拨付国库黄金','拨付100枚金币并记入账本。','◈',3,'','',''),
        ('btn:audit_ledger','actions','audit_ledger','treasury','独立审计账本','汇总金币流水，保存预期与实际余额核对报告。','⌕',4,'','',''),
    ]
    conn.executemany('INSERT INTO ui_resource VALUES (?,?,?,?,?,?,?,?,?,?,?)', entries)
    conn.execute("UPDATE sys_permission SET name='边疆布防机密图' WHERE code='page:defense_map' "
                 "AND name IN ('边疆布防图','边疆城防机密图')")
    conn.execute("UPDATE kingdom_document SET title='边疆布防机密图' WHERE slug='defense_map' "
                 "AND title IN ('边疆布防图','边疆城防机密图')")
    conn.execute('INSERT INTO schema_meta VALUES (2)')


def _replace_default(conn, table, key_column, key, column, old, new):
    """Only replace a known shipped default, preserving each customized field.

    SQL identifiers below are internal literals. No request data reaches them.
    Display migrations never rewrite permissions, historical events or balances.
    """
    known_values = old if isinstance(old, tuple) else (old,)
    placeholders = ','.join('?' for _ in known_values)
    conn.execute(f'UPDATE {table} SET {column}=? WHERE {key_column}=? '
                 f'AND {column} IN ({placeholders})', (new, key, *known_values))


def _migrate_kuttenberg_theme(conn):
    """Apply display revisions without changing the v1/v2 data schema."""
    row = conn.execute("SELECT version FROM theme_meta WHERE theme='kuttenberg-mint'").fetchone()
    version = row['version'] if row else 0
    if version < 1:
        _migrate_kuttenberg_v1(conn)
    if version < 2:
        _migrate_kuttenberg_v2(conn)


def _migrate_kuttenberg_v1(conn):
    """Retain the originally shipped display defaults as migration history.

    This is a teaching setting inspired by locations in KCD2, not a statement
    that the fictional account holders occupy these offices in the game's plot.
    """
    resource_updates = {
        'menu:square': {
            'title': ('王城公共广场', '库滕贝格市政广场'),
            'description': ('行会生产、公开税务与王城日常。', '库滕贝格的行会生产、税务公告与市政通行。'),
            'eyebrow': ('THE GUILD SQUARE', 'KUTTENBERG · CIVIC SQUARE'),
            'subtitle': ('从一柄铁剑，开始王国的一天。', '银城的秩序，从一纸市政告示开始。'),
        },
        'menu:armory': {
            'title': ('军械与兵营', '特罗斯基城堡军械库'),
            'description': ('军械供给、骑兵调遣与边疆布防。', '特罗斯基军械登记、军事通行与运银护送调令。'),
            'eyebrow': ('THE ROYAL ARMORY', 'TROSKY · CASTLE ARMORY'),
            'subtitle': ('边疆安宁，来自有序调度。', '护送启程之前，先核验军事通行手令。'),
        },
        'menu:treasury': {
            'title': ('皇家国库', '意大利宫皇家铸币内廷'),
            'description': ('账目查阅、金币拨付与独立核对。', '意大利宫的运银经费拨付、收支密账与银库核对。'),
            'eyebrow': ('THE ROYAL TREASURY', 'ITALIAN COURT · ROYAL MINT'),
            'subtitle': ('每一枚金币，都有它的去向。', '每笔运银经费，均须入账并独立核对。'),
        },
        'page:tax_scroll': {
            'title': ('王国赋税告示', '市政税务告示'),
            'description': ('公开税目、标准与申报周期。', '查阅库滕贝格市政税目、教学税则与申报周期。'),
        },
        'page:defense_map': {
            'title': ('边疆布防机密图', '特罗斯基军事通行手令'),
            'description': ('查阅据点、驻军与换防安排。', '核验特罗斯基护送据点、教学驻军与换防手令。'),
        },
        'page:royal_ledger': {
            'title': ('皇家国库总账', '意大利宫收支密账'),
            'description': ('读取当前余额与真实业务流水。', '读取意大利宫银库记账余额与真实业务流水。'),
        },
        'btn:forge_sword': {
            'title': ('打造行会铁剑', '打造矿路护卫铁剑'),
            'description': ('完成生产，铁剑库存增加一柄。', '完成行会铁剑生产，真实库存增加一柄。'),
        },
        'btn:deploy_cavalry': {
            'title': ('调遣重装骑兵', '签发运银护送调令'),
            'description': ('登记一次骑兵出征。', '登记一次骑兵运银护送调令，累计调遣次数增加一次。'),
        },
        'btn:open_vault': {
            'title': ('拨付国库黄金', '拨付运银经费'),
            'description': ('拨付100枚金币并记入账本。', '拨付100格罗申运银经费并记入收支密账。'),
        },
        'btn:audit_ledger': {
            'title': ('独立审计账本', '核对银库账目'),
            'description': ('汇总金币流水，保存预期与实际余额核对报告。', '独立审计格罗申收支，保存预期余额、实际余额与差异报告。'),
        },
    }
    for code, fields in resource_updates.items():
        for field, (old, new) in fields.items():
            _replace_default(conn, 'ui_resource', 'code', code, field, old, new)

    permission_names = {
        'menu:square': (('王城广场', '王城公共广场'), '库滕贝格市政广场'),
        'menu:armory': ('军械与兵营', '特罗斯基城堡军械库'),
        'menu:treasury': ('皇家国库', '意大利宫皇家铸币内廷'),
        'page:tax_scroll': ('王国赋税告示', '市政税务告示'),
        'page:defense_map': (('边疆布防图', '边疆布防机密图', '边疆城防机密图'), '特罗斯基军事通行手令'),
        'page:royal_ledger': ('皇家国库总账', '意大利宫收支密账'),
        'btn:forge_sword': ('打造行会铁剑', '打造矿路护卫铁剑'),
        'btn:deploy_cavalry': ('调遣重装骑兵', '签发运银护送调令'),
        'btn:open_vault': ('拨付国库黄金', '拨付运银经费'),
        'btn:audit_ledger': ('独立审计账本', '核对银库账目'),
    }
    for code, (old, new) in permission_names.items():
        _replace_default(conn, 'sys_permission', 'code', code, 'name', old, new)
    descriptions = {
        'menu:square': ('行会与民生事务入口', '库滕贝格市政与行会事务入口'),
        'menu:armory': ('军事事务导航入口', '特罗斯基城堡军事与护送事务入口'),
        'menu:treasury': ('财政事务导航入口', '意大利宫皇家铸币内廷入口'),
        'page:tax_scroll': ('查阅公开赋税卷轴', '查阅库滕贝格市政税务告示'),
        'page:defense_map': ('查阅军事机密卷轴', '查阅特罗斯基军事通行手令'),
        'page:royal_ledger': ('查阅财政账目', '查阅意大利宫收支密账'),
        'btn:deploy_cavalry': ('记录一次骑兵调遣', '记录一次运银护送的骑兵调遣'),
        'btn:open_vault': ('拨付100枚金币并记账', '拨付100格罗申运银经费并记账'),
        'btn:audit_ledger': ('生成独立审计记录', '核对银库账目并保存独立审计报告'),
    }
    for code, (old, new) in descriptions.items():
        _replace_default(conn, 'sys_permission', 'code', code, 'description', old, new)
    for code, old, new in (
        ('TREASURER', '财政大臣', '铸币司库'),
        ('AUDITOR', '皇家审计官', '皇家银库审计官'),
    ):
        _replace_default(conn, 'sys_role', 'code', code, 'name', old, new)
    for username, old, new in (
        ('henry', '斯卡里茨铁匠学徒', '教学扮演：行会铁匠学徒'),
        ('radzig', '皇家卫队统领', '教学扮演：王室运银护送统领'),
        ('hans', '王国财政大臣', '教学扮演：意大利宫铸币司库'),
        ('wenceslas', '波希米亚国王', '教学扮演：王室特许管理者'),
        ('agnes', '独立皇家审计官', '教学扮演：皇家银库审计官'),
    ):
        _replace_default(conn, 'sys_user', 'username', username, 'title', old, new)
    for username, old, new in (
        ('henry', '亨利', '亨利·斯卡里茨'),
        ('hans', '汉斯', '汉斯·卡蓬'),
        ('radzig', '罗德爵士', '拉德季·科比拉'),
    ):
        _replace_default(conn, 'sys_user', 'username', username, 'display_name', old, new)
    _replace_default(conn, 'sys_role_mutex', 'role_a_id', 3, 'reason',
                     '财政与审计必须由不同臣民承担（静态职责分离 SSD）',
                     '皇家反走私法令：财政与审计必须由不同人员承担，铸币司库不得兼任皇家银库审计官（静态职责分离 SSD）')
    _replace_default(conn, 'sys_permission_mutex', 'permission_a_id', 9, 'reason',
                     '拨款与独立审计权限不得集中，防止直接授权绕过职责分离',
                     '皇家反走私法令：财政拨付与独立审计权限不得集中，防止直接授权绕过职责分离')

    documents = {
        'tax_scroll': (
            '王国赋税告示', '市政税务告示',
            '王城公开卷轴：行会按季度申报，农户按秋收结算。此卷轴可供所有已获授权的臣民查阅。',
            '库滕贝格市政公开告示：行会按季度申报，农户按秋收结算。下列税目与比例仅为课程教学数据，不代表游戏税制或历史税率。',
        ),
        'defense_map': (
            '边疆布防机密图', '特罗斯基军事通行手令',
            '机密军令：北境晨星堡由第七骑兵团驻守，西河关隘执行黎明换防。仅授权军事角色可读取本卷轴。',
            '教学机密手令：特罗斯基城堡出发的运银护送由第七骑兵团负责，经库滕贝格外城交接，凭军令进入意大利宫。此驻军与换防安排为RBAC教学情境，不作为游戏剧情事实。',
        ),
        'royal_ledger': (
            '皇家国库总账', '意大利宫收支密账',
            '本账展示国库当前余额及实际业务流水。拨款与独立审计由不同官职执行，国王仅查阅和管理授权。',
            '本密账展示意大利宫银库余额与实际收支。运银经费由铸币司库拨付，皇家银库审计官独立核对；每笔支出均登记操作者与时间。金额以格罗申（Groschen）记账。',
        ),
    }
    for slug, (old_title, new_title, old_content, new_content) in documents.items():
        _replace_default(conn, 'kingdom_document', 'slug', slug, 'title', old_title, new_title)
        _replace_default(conn, 'kingdom_document', 'slug', slug, 'content', old_content, new_content)
    old_records = [
        {'据点': '北境晨星堡', '驻军': '第七骑兵团', '换防': '卯时'},
        {'据点': '西河关隘', '驻军': '第三长弓队', '换防': '黎明'},
    ]
    new_records = [
        {'据点': '特罗斯基城堡集结点（教学）', '驻军': '第七骑兵团（教学）', '换防': '卯时'},
        {'据点': '库滕贝格外城交接点（教学）', '驻军': '第三护送队（教学）', '换防': '黎明'},
    ]
    _replace_default(conn, 'kingdom_document', 'slug', 'defense_map', 'records_json',
                     json.dumps(old_records, ensure_ascii=False), json.dumps(new_records, ensure_ascii=False))
    conn.execute("INSERT INTO theme_meta VALUES ('kuttenberg-mint',1)")


def _migrate_kuttenberg_v2(conn):
    """Correct only known v1 defaults; user prose and evidence stay intact."""
    defaults = (
        ('ui_resource', 'code', 'menu:square', 'title', '库滕贝格市政广场'),
        ('ui_resource', 'code', 'menu:square', 'description', '库滕贝格的行会生产、税务公告与市政通行。'),
        ('ui_resource', 'code', 'page:tax_scroll', 'description', '查阅库滕贝格市政税目、教学税则与申报周期。'),
        ('sys_permission', 'code', 'menu:square', 'name', '库滕贝格市政广场'),
        ('sys_permission', 'code', 'menu:square', 'description', '库滕贝格市政与行会事务入口'),
        ('sys_permission', 'code', 'page:tax_scroll', 'description', '查阅库滕贝格市政税务告示'),
        ('kingdom_document', 'slug', 'tax_scroll', 'content',
         '库滕贝格市政公开告示：行会按季度申报，农户按秋收结算。下列税目与比例仅为课程教学数据，不代表游戏税制或历史税率。'),
        ('kingdom_document', 'slug', 'defense_map', 'content',
         '教学机密手令：特罗斯基城堡出发的运银护送由第七骑兵团负责，经库滕贝格外城交接，凭军令进入意大利宫。此驻军与换防安排为RBAC教学情境，不作为游戏剧情事实。'),
    )
    for table, key_column, key, column, old in defaults:
        _replace_default(conn, table, key_column, key, column, old, old.replace('库滕贝格', '库滕堡'))
    old_records = json.dumps([
        {'据点': '特罗斯基城堡集结点（教学）', '驻军': '第七骑兵团（教学）', '换防': '卯时'},
        {'据点': '库滕贝格外城交接点（教学）', '驻军': '第三护送队（教学）', '换防': '黎明'},
    ], ensure_ascii=False)
    _replace_default(conn, 'kingdom_document', 'slug', 'defense_map', 'records_json',
                     old_records, old_records.replace('库滕贝格', '库滕堡'))
    conn.execute("UPDATE theme_meta SET version=2 WHERE theme='kuttenberg-mint'")


def resource_catalog(conn, territory=None):
    result = {'menus': [], 'pages': [], 'actions': []}
    condition, params = (' WHERE territory=?', (territory,)) if territory else ('', ())
    for record in rows(conn, 'SELECT * FROM ui_resource' + condition + ' ORDER BY sort_order,code', params):
        kind = record.pop('kind')
        result[kind].append(record)
    return result


def policy_revision(conn):
    return conn.execute('SELECT revision FROM policy_state WHERE id=1').fetchone()['revision']


def business_state(conn):
    return dict(conn.execute('SELECT swords,cavalry_deployments,gold,audits FROM kingdom_state WHERE id=1').fetchone())
