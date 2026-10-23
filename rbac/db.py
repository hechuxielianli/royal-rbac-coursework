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
    conn.execute("UPDATE sys_permission SET name='边疆布防机密图' WHERE code='page:defense_map'")
    conn.execute("UPDATE kingdom_document SET title='边疆布防机密图' WHERE slug='defense_map'")
    conn.execute('INSERT INTO schema_meta VALUES (2)')


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
