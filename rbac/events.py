"""同步事件总线：监听器先裁决，业务响应在允许后才执行。"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from uuid import uuid4

from werkzeug.security import check_password_hash

from .db import business_state, policy_revision
from .accounting import reconcile_ledger
from .policy import (ADMINISTRATIVE, DomainError, capability_snapshot, check_delegation,
                     check_permission_constraints, check_ssd, direct_roles, ensure_ids,
                     id_list, permissions_for_roles, role_closure, validate_policy)


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


@dataclass
class Event:
    event_type: str
    actor_id: int | None
    target: str
    permission_code: str | None = None
    active_role_ids: list = field(default_factory=list)
    context: dict = field(default_factory=dict, repr=False)
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=timestamp)
    allowed: bool = False
    message: str = '默认拒绝：事件尚未获得监听器裁决'
    logs: list = field(default_factory=list)
    result: dict = field(default_factory=dict)
    actor_permissions: set = field(default_factory=set, repr=False)

    def log(self, step, detail):
        self.logs.append({'step': step, 'detail': detail, 'time': timestamp()})

    def public(self):
        return {'id': self.id, 'event_type': self.event_type, 'target': self.target,
                'allowed': self.allowed, 'message': self.message,
                'created_at': self.created_at, 'logs': self.logs}


class EventBus:
    def __init__(self):
        self._listeners = {}

    def subscribe(self, event_type, listener):
        self._listeners.setdefault(event_type, []).append(listener)

    def publish(self, event, conn):
        event.log('EVENT_EMIT', f'发布 {event.event_type}，目标 {event.target}')
        for listener in self._listeners.get(event.event_type, []):
            event.log('LISTENER_CAPTURED', type(listener).__name__ + ' 接收事件')
            listener.on_event(event, conn)
        if not event.allowed:
            raise DomainError(event.message, 'DEFAULT_DENY', 403)
        return event


class RoyalGuardAccessListener:
    """菜单、页面、动作及策略管理接口共享的权限判定。"""
    def on_event(self, event, conn):
        if event.actor_id is None:
            raise DomainError('请先登录王室大厅', 'AUTHENTICATION_REQUIRED', 401)
        assigned = set(direct_roles(conn, event.actor_id))
        # 每次请求重读当前分配，过期激活角色不能保留已撤销的权力。
        active = assigned & set(event.active_role_ids)
        closure = role_closure(conn, active)
        check_ssd(conn, closure)
        effective_permissions = permissions_for_roles(conn, active)
        check_permission_constraints(conn, effective_permissions)
        event.actor_permissions = effective_permissions
        event.log('ROLE_CLOSURE', f'当前分配 {sorted(assigned)}；激活 {sorted(active)}；继承闭包 {sorted(closure)}')
        permission = conn.execute('SELECT name FROM sys_permission WHERE code=?',
                                  (event.permission_code,)).fetchone()
        if permission is None:
            raise DomainError('未知权限资源，默认拒绝', 'UNKNOWN_PERMISSION', 404)
        if event.permission_code not in effective_permissions:
            raise DomainError(f'当前激活角色没有「{permission["name"]}」权限', 'PERMISSION_DENIED', 403)
        event.allowed = True
        event.message = f'卫队放行：{permission["name"]}'
        event.log('DECISION_ALLOW', f'数据库角色授权包含 {event.permission_code}')


class AuthenticationListener:
    def on_event(self, event, conn):
        username, password = event.context.get('username'), event.context.get('password')
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 80 or len(password) > 200:
            raise DomainError('请输入有效的账号和密码')
        user = conn.execute('SELECT * FROM sys_user WHERE username=?', (username,)).fetchone()
        if user is None or not check_password_hash(user['password_hash'], password):
            raise DomainError('账号或密码不正确', 'LOGIN_FAILED', 401)
        role_ids = direct_roles(conn, user['id'])
        check_ssd(conn, role_closure(conn, role_ids))
        check_permission_constraints(conn, permissions_for_roles(conn, role_ids))
        event.actor_id = user['id']
        event.result['user_id'] = user['id']
        event.result['active_role_ids'] = role_ids
        event.allowed, event.message = True, f'{user["display_name"]}，欢迎来到政令大厅'
        event.log('DECISION_ALLOW', '密码哈希校验通过；创建激活已分配角色的会话')


class LogoutListener:
    def on_event(self, event, conn):
        event.allowed, event.message = True, '已退出当前身份'
        event.log('SESSION_LOGOUT', '撤销当前浏览器会话')


class RoleActivationListener:
    def on_event(self, event, conn):
        requested = id_list(event.context.get('role_ids'), '激活角色')
        assigned = set(direct_roles(conn, event.actor_id))
        if not set(requested) <= assigned:
            raise DomainError('只能激活当前已分配的角色', 'UNASSIGNED_ROLE', 403)
        check_ssd(conn, role_closure(conn, requested))
        check_permission_constraints(conn, permissions_for_roles(conn, requested))
        event.allowed, event.message = True, '当前会话的激活角色已更新'
        event.result['active_role_ids'] = requested
        event.log('ROLE_ACTIVATION', f'会话激活子集：{sorted(requested)}')


def begin_policy_change(event, conn):
    revision = event.context.get('base_revision')
    if type(revision) is not int or revision < 1:
        raise DomainError('请提交管理快照中的整数 base_revision', 'INVALID_REVISION', 400)
    if revision != policy_revision(conn):
        raise DomainError('策略已被其他操作修改，请重新载入后比较候选方案', 'STALE_POLICY', 409)
    return capability_snapshot(conn)


def finish_policy_change(event, conn, before, prior, candidate):
    validate_policy(conn)
    check_delegation(conn, before, event.actor_permissions)
    conn.execute('UPDATE policy_state SET revision=revision+1 WHERE id=1')
    event.result['policy_revision'] = policy_revision(conn)
    event.log('POLICY_DIFF', json.dumps({'before': prior, 'after': candidate,
                                       'revision': policy_revision(conn)}, ensure_ascii=False, sort_keys=True))


class RoleAssignmentListener:
    def on_event(self, event, conn):
        before = begin_policy_change(event, conn)
        user_id = event.context['user_id']
        if conn.execute('SELECT 1 FROM sys_user WHERE id=?', (user_id,)).fetchone() is None:
            raise DomainError('目标臣民不存在', 'NOT_FOUND', 404)
        role_ids = id_list(event.context.get('role_ids'), '册封角色')
        ensure_ids(conn, 'sys_role', role_ids, '册封角色')
        if user_id == event.actor_id:
            raise DomainError('不能修改自己的直接角色分配，请由另一位管理员处理', 'SELF_ASSIGNMENT_FORBIDDEN', 403)
        if not ADMINISTRATIVE <= event.actor_permissions:
            for role_id in role_ids:
                permissions = permissions_for_roles(conn, [role_id])
                if any(p.startswith('admin:') for p in permissions) or not permissions <= event.actor_permissions:
                    raise DomainError('只能委派自身当前有效权限覆盖的非管理角色', 'DELEGATION_SCOPE', 403)
        prior = direct_roles(conn, user_id)
        conn.execute('DELETE FROM sys_user_role WHERE user_id=?', (user_id,))
        conn.executemany('INSERT INTO sys_user_role VALUES (?,?)', [(user_id, rid) for rid in role_ids])
        finish_policy_change(event, conn, before, {'role_ids': prior}, {'role_ids': role_ids})
        event.log('POLICY_VALIDATED', '候选策略通过全体用户的 SSD、权限互斥及管理员保留检查')
        event.log('DB_WRITE', f'sys_user_role 更新臣民 {user_id} 的直接角色为 {role_ids}')
        event.message = '册封圣旨已生效；新角色需在目标会话激活或重新登录'


class PermissionUpdateListener:
    def on_event(self, event, conn):
        before = begin_policy_change(event, conn)
        role_id = event.context['role_id']
        if conn.execute('SELECT 1 FROM sys_role WHERE id=?', (role_id,)).fetchone() is None:
            raise DomainError('目标角色不存在', 'NOT_FOUND', 404)
        permission_ids = id_list(event.context.get('permission_ids'), '授权权限')
        ensure_ids(conn, 'sys_permission', permission_ids, '授权权限')
        prior = [row['permission_id'] for row in conn.execute(
            'SELECT permission_id FROM sys_role_permission WHERE role_id=? ORDER BY permission_id', (role_id,))]
        administrative_ids = {row['id'] for row in conn.execute("SELECT id FROM sys_permission WHERE type='ADMIN'")}
        if not ADMINISTRATIVE <= event.actor_permissions and (set(prior) ^ set(permission_ids)) & administrative_ids:
            raise DomainError('管理权限的直接授予或撤销必须由完整管理员处理', 'ADMIN_DELEGATION_REQUIRED', 403)
        conn.execute('DELETE FROM sys_role_permission WHERE role_id=?', (role_id,))
        conn.executemany('INSERT INTO sys_role_permission VALUES (?,?)', [(role_id, pid) for pid in permission_ids])
        finish_policy_change(event, conn, before, {'permission_ids': prior}, {'permission_ids': permission_ids})
        event.log('POLICY_VALIDATED', '候选授权通过角色闭包、权限互斥及管理员保留检查')
        event.log('DB_WRITE', f'sys_role_permission 更新角色 {role_id} 的直接授权为 {permission_ids}')
        event.message = '权限矩阵已写入数据库，下一次请求立即使用新策略'


class HierarchyUpdateListener:
    def on_event(self, event, conn):
        before = begin_policy_change(event, conn)
        raw = event.context.get('edges')
        if not isinstance(raw, list) or len(raw) > 100:
            raise DomainError('继承边必须为最多100项的数组')
        edges = []
        for edge in raw:
            if not isinstance(edge, dict) or set(edge) != {'senior_id','junior_id'}:
                raise DomainError('每条继承边应包含 senior_id 与 junior_id')
            senior_id, junior_id = edge['senior_id'], edge['junior_id']
            id_list([senior_id], '继承角色')
            id_list([junior_id], '被继承角色')
            if senior_id == junior_id:
                raise DomainError('角色不能继承自身', 'HIERARCHY_CYCLE', 409)
            ensure_ids(conn, 'sys_role', [senior_id, junior_id], '继承边')
            edges.append((senior_id, junior_id))
        if len(set(edges)) != len(edges):
            raise DomainError('继承边不得重复')
        prior = [dict(row) for row in conn.execute('SELECT senior_id,junior_id FROM sys_role_hierarchy ORDER BY senior_id,junior_id')]
        conn.execute('DELETE FROM sys_role_hierarchy')
        conn.executemany('INSERT INTO sys_role_hierarchy VALUES (?,?)', edges)
        if not ADMINISTRATIVE <= event.actor_permissions:
            administrative = {row['code'] for row in conn.execute("SELECT code FROM sys_permission WHERE type='ADMIN'")}
            previous_edges = {(row['senior_id'], row['junior_id']) for row in prior}
            for _, junior_id in previous_edges ^ set(edges):
                old = before.get(f'role:{junior_id}', set())
                new = permissions_for_roles(conn, [junior_id])
                if (old | new) & administrative:
                    raise DomainError('通向管理能力的继承关系须由完整管理员修改', 'ADMIN_DELEGATION_REQUIRED', 403)
        finish_policy_change(event, conn, before, {'edges': prior}, {'edges': raw})
        event.log('POLICY_VALIDATED', '继承图无环；全局 SSD 与权限互斥不变量保持成立')
        event.log('DB_WRITE', f'sys_role_hierarchy 写入 {len(edges)} 条继承边')
        event.message = '角色继承图已更新'


class BusinessActionListener:
    def on_event(self, event, conn):
        slug = event.context['slug']
        operations = {
            'forge_sword': ('swords',1,'一把矿路护卫铁剑已入库'),
            'deploy_cavalry': ('cavalry_deployments',1,'运银护送调令已登记，骑兵调遣次数增加一次'),
            'open_vault': ('gold',-100,'已拨付100格罗申运银经费，支出已记入意大利宫收支密账'),
            'audit_ledger': ('audits',1,'独立审计记录已封存'),
        }
        if not event.allowed:
            raise DomainError('操作未取得授权', 'DEFAULT_DENY', 403)
        column, amount, description = operations[slug]
        if slug == 'audit_ledger':
            report = reconcile_ledger(conn, event)
            event.result['audit_report'] = report
            description = '银库核对报告已保存：' + ('余额一致' if report['consistent'] else f'发现差异 {report["difference"]:+d} 格罗申')
        if slug == 'open_vault' and business_state(conn)['gold'] < 100:
            raise DomainError('银库余额不足100格罗申，无法拨付运银经费', 'INSUFFICIENT_GOLD', 409)
        # column comes from the internal mapping above, not from a request value.
        conn.execute(f'UPDATE kingdom_state SET {column}={column}+? WHERE id=1', (amount,))
        conn.execute('INSERT INTO business_ledger(event_id,actor_id,action,amount,description,created_at) '
                     'VALUES (?,?,?,?,?,?)', (event.id,event.actor_id,slug,amount,description,event.created_at))
        event.log('DB_WRITE', f'kingdom_state.{column} 变化 {amount:+d}；business_ledger 已登记操作者')
        event.message = description
        event.result['business'] = business_state(conn)


class AuditListener:
    """只观察并持久化裁决，不能将拒绝改为允许。"""
    def on_event(self, event, conn):
        conn.execute('INSERT INTO audit_event VALUES (?,?,?,?,?,?,?,?)',
                     (event.id,event.actor_id,event.event_type,event.target,int(event.allowed),
                      event.message,json.dumps(event.logs,ensure_ascii=False),event.created_at))


def build_event_bus():
    bus = EventBus()
    guard = RoyalGuardAccessListener()
    for kind in ('ACCESS_CHECK','ADMIN_READ','ACTION_REQUEST','ROLE_ASSIGN_REQUEST',
                 'ROLE_PERMISSION_UPDATE','HIERARCHY_UPDATE'):
        bus.subscribe(kind, guard)
    bus.subscribe('LOGIN_REQUEST', AuthenticationListener())
    bus.subscribe('LOGOUT_REQUEST', LogoutListener())
    bus.subscribe('ROLE_ACTIVATION', RoleActivationListener())
    bus.subscribe('ROLE_ASSIGN_REQUEST', RoleAssignmentListener())
    bus.subscribe('ROLE_PERMISSION_UPDATE', PermissionUpdateListener())
    bus.subscribe('HIERARCHY_UPDATE', HierarchyUpdateListener())
    bus.subscribe('ACTION_REQUEST', BusinessActionListener())
    return bus
