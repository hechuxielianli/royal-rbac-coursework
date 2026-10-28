"""与 HTTP 无关的 RBAC 集合运算及策略不变量。"""


class DomainError(Exception):
    def __init__(self, message, code='INVALID_REQUEST', status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def id_list(value, label='ID列表'):
    if not isinstance(value, list) or len(value) > 100:
        raise DomainError(f'{label}必须是最多100项的整数数组')
    if any(type(item) is not int or not 0 < item <= 2**63-1 for item in value):
        raise DomainError(f'{label}只能包含正整数')
    if len(value) != len(set(value)):
        raise DomainError(f'{label}不得包含重复ID')
    return value


def ensure_ids(conn, table, values, label):
    # Table names are internal literals, never HTTP input.
    existing = {row['id'] for row in conn.execute(f'SELECT id FROM {table}')}
    if not set(values) <= existing:
        raise DomainError(f'{label}包含不存在的ID', 'UNKNOWN_ID', 400)


def direct_roles(conn, user_id):
    return [row['role_id'] for row in conn.execute(
        'SELECT role_id FROM sys_user_role WHERE user_id=? ORDER BY role_id', (user_id,))]


def role_closure(conn, role_ids):
    edges = {}
    for row in conn.execute('SELECT senior_id,junior_id FROM sys_role_hierarchy'):
        edges.setdefault(row['senior_id'], []).append(row['junior_id'])
    found, pending = set(), list(role_ids)
    while pending:
        role_id = pending.pop()
        if role_id not in found:
            found.add(role_id)
            pending.extend(edges.get(role_id, []))
    return found


def permissions_for_roles(conn, role_ids):
    closure = role_closure(conn, role_ids)
    if not closure:
        return set()
    placeholders = ','.join('?' for _ in closure)
    return {row['code'] for row in conn.execute(
        f'SELECT DISTINCT p.code FROM sys_permission p JOIN sys_role_permission rp '
        f'ON rp.permission_id=p.id WHERE rp.role_id IN ({placeholders})', tuple(closure))}


def check_ssd(conn, closure):
    for rule in conn.execute('SELECT * FROM sys_role_mutex'):
        if {rule['role_a_id'], rule['role_b_id']} <= closure:
            raise DomainError(rule['reason'], 'SSD_CONFLICT', 409)


def check_permission_constraints(conn, permissions):
    for rule in conn.execute('SELECT m.*,a.code AS code_a,b.code AS code_b '
                             'FROM sys_permission_mutex m '
                             'JOIN sys_permission a ON a.id=m.permission_a_id '
                             'JOIN sys_permission b ON b.id=m.permission_b_id'):
        if {rule['code_a'], rule['code_b']} <= permissions:
            raise DomainError(rule['reason'], 'PERMISSION_CONFLICT', 409)


def validate_policy(conn):
    """在同一待提交事务中验证全局候选策略，失败由调用者整体回滚。"""
    roles = [row['id'] for row in conn.execute('SELECT id FROM sys_role')]
    edges = {role_id: [] for role_id in roles}
    for row in conn.execute('SELECT * FROM sys_role_hierarchy'):
        edges[row['senior_id']].append(row['junior_id'])
    visiting, visited = set(), set()

    def visit(role_id):
        if role_id in visiting:
            raise DomainError('角色继承不能形成循环', 'HIERARCHY_CYCLE', 409)
        if role_id in visited:
            return
        visiting.add(role_id)
        for junior_id in edges[role_id]:
            visit(junior_id)
        visiting.remove(role_id)
        visited.add(role_id)

    for role_id in roles:
        visit(role_id)
        closure = role_closure(conn, [role_id])
        check_ssd(conn, closure)
        check_permission_constraints(conn, permissions_for_roles(conn, [role_id]))
    administrative = {'admin:assign','admin:policy','admin:audit'}
    has_administrator = False
    for user in conn.execute('SELECT id FROM sys_user'):
        assigned = direct_roles(conn, user['id'])
        check_ssd(conn, role_closure(conn, assigned))
        permissions = permissions_for_roles(conn, assigned)
        check_permission_constraints(conn, permissions)
        has_administrator |= administrative <= permissions
    if not has_administrator:
        raise DomainError('必须保留至少一名拥有全部管理权限的臣民', 'LAST_ADMINISTRATOR', 409)


ADMINISTRATIVE = frozenset({'admin:assign', 'admin:policy', 'admin:audit'})


def capability_snapshot(conn):
    """Include unassigned roles: a dormant administrative bridge is still policy."""
    snapshot = {}
    for role in conn.execute('SELECT id FROM sys_role'):
        snapshot[f'role:{role["id"]}'] = permissions_for_roles(conn, [role['id']])
    for user in conn.execute('SELECT id FROM sys_user'):
        snapshot[f'user:{user["id"]}'] = permissions_for_roles(conn, direct_roles(conn, user['id']))
    return snapshot


def check_delegation(conn, before, actor_permissions):
    if ADMINISTRATIVE <= actor_permissions:
        return
    administrative = {row['code'] for row in conn.execute("SELECT code FROM sys_permission WHERE type='ADMIN'")}
    for subject, after_permissions in capability_snapshot(conn).items():
        prior = before.get(subject, set())
        if (prior ^ after_permissions) & administrative:
            raise DomainError('改变管理能力需要同时激活全部三项管理权限', 'ADMIN_DELEGATION_REQUIRED', 403)
        if (after_permissions - prior) - actor_permissions:
            raise DomainError('不得引入超出自身修改前有效权限的业务能力', 'DELEGATION_SCOPE', 403)
