"""HTTP 适配层只选择事件及固定权限码，不在客户端信任身份。"""
import hmac
import json
import math
import secrets

from flask import Blueprint, current_app, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException

from .db import business_state, get_db, policy_revision, resource_catalog, rows
from .events import AuditListener, Event
from .auth_sessions import authenticated_user, issue_session, revoke_session
from .policy import (DomainError, check_permission_constraints, check_ssd, direct_roles,
                     permissions_for_roles, role_closure)

bp = Blueprint('royal', __name__)


def csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']


def users(conn):
    return rows(conn, 'SELECT id,username,display_name,title FROM sys_user ORDER BY id')


def require_user():
    user_id = authenticated_user(get_db())
    if user_id is None:
        raise DomainError('请先登录王室大厅', 'AUTHENTICATION_REQUIRED', 401)
    return user_id


def active_ids(conn, user_id):
    assigned = direct_roles(conn, user_id) if user_id is not None else []
    requested = session.get('active_role_ids', assigned)
    return sorted(set(assigned) & set(requested))


def current_permissions(conn, user_id):
    active = active_ids(conn, user_id)
    check_ssd(conn, role_closure(conn, active))
    permissions = permissions_for_roles(conn, active)
    check_permission_constraints(conn, permissions)
    return permissions


def body():
    if not request.is_json:
        raise DomainError('请求正文必须为 JSON 对象')
    def reject_constant(constant):
        raise ValueError(f'Invalid JSON constant: {constant}')
    try:
        value = json.loads(request.get_data(), parse_constant=reject_constant)
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                item.encode('utf-8', errors='strict')
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError('Non-finite JSON number')
            elif isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
    except (ValueError, UnicodeError, RecursionError):
        raise DomainError('请求正文必须为有效 JSON，字符串需为有效 Unicode，数值必须有限')
    if not isinstance(value, dict):
        raise DomainError('请求正文必须为有效 JSON 对象')
    return value


def run_event(kind, target, permission=None, context=None, result_builder=None):
    """授权、响应及成功审计原子提交；失败回滚后独立记录拒绝。"""
    conn = get_db()
    actor_id = authenticated_user(conn)
    if kind == 'LOGIN_REQUEST':
        actor_id = None
    event = Event(kind, actor_id, target, permission,
                  active_ids(conn, actor_id), context or {})
    status, code = 200, None
    try:
        # Even read checks persist an audit event. One writer transaction also
        # prevents a policy change racing authorization and business execution.
        conn.execute('BEGIN IMMEDIATE')
        if kind != 'LOGIN_REQUEST':
            event.actor_id = authenticated_user(conn)
            if event.actor_id is None:
                raise DomainError('请先登录王室大厅', 'AUTHENTICATION_REQUIRED', 401)
            event.active_role_ids = active_ids(conn, event.actor_id)
        current_app.extensions['event_bus'].publish(event, conn)
        if kind == 'LOGIN_REQUEST':
            revoke_session(conn, session.get('session_nonce'))
            event.result['session_nonce'] = issue_session(conn, event.actor_id)
        elif kind == 'LOGOUT_REQUEST':
            revoke_session(conn, session.get('session_nonce'))
        if result_builder:
            event.result.update(result_builder(conn))
        event.log('DB_COMMIT', '授权响应与审计记录作为同一事务提交')
        event.log('AUDIT_PERSIST', f'事件 {event.id[:12]} 已保存至 audit_event')
        AuditListener().on_event(event, conn)
        conn.commit()
    except Exception as error:
        conn.rollback()
        event.allowed = False
        # Never leave a failed commit looking successful in the recorded trace.
        event.logs = [line for line in event.logs if line['step'] not in ('DB_COMMIT','AUDIT_PERSIST')]
        event.result = {}
        if isinstance(error, DomainError):
            event.message, status, code = error.message, error.status, error.code
        else:
            current_app.logger.exception('Event %s failed', event.id)
            event.message, status, code = '事件处理失败，事务已回滚', 503, 'EVENT_FAILURE'
        event.log('DECISION_DENY', f'{code}: {event.message}')
        event.log('DB_ROLLBACK', '候选政策与业务修改均未生效')
        event.log('AUDIT_PERSIST', f'拒绝事件 {event.id[:12]} 保存至 audit_event')
        try:
            AuditListener().on_event(event, conn)
            conn.commit()
        except Exception:
            conn.rollback()
            current_app.logger.exception('Cannot persist denied event %s', event.id)
            status, code = 503, 'AUDIT_FAILURE'
            event.message = '审计存储不可用，操作未执行'
            event.logs = [line for line in event.logs if line['step'] != 'AUDIT_PERSIST']
            event.log('AUDIT_FAILURE', '审计写入失败，当前事件未持久化')
    payload = {'ok': event.allowed, 'message': event.message, 'event': event.public(), **event.result}
    if code:
        payload['code'] = code
    if code == 'STALE_POLICY':
        payload['policy_revision'] = policy_revision(conn)
    return payload, status


@bp.before_app_request
def protect_mutations():
    if request.path.startswith('/api/') and request.method in ('POST','PUT','PATCH','DELETE'):
        expected, supplied = session.get('csrf_token'), request.headers.get('X-CSRF-Token')
        if (not isinstance(expected, str) or not isinstance(supplied, str)
                or not supplied.isascii() or not hmac.compare_digest(expected, supplied)):
            raise DomainError('CSRF 校验失败，请刷新页面后重试', 'CSRF_FAILED', 403)


@bp.after_app_request
def secure_response(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; img-src 'self' data:; "
        "font-src 'self' https://fonts.gstatic.com; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'")
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    if request.path.startswith('/api/') or request.path.startswith('/pages/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@bp.app_errorhandler(DomainError)
def domain_error(error):
    if request.path.startswith('/api/'):
        return jsonify(ok=False, message=error.message, code=error.code), error.status
    return render_template('resource.html', title='访问被拒绝', content=error.message, records=[], event=None), error.status


@bp.app_errorhandler(HTTPException)
def http_error(error):
    if request.path.startswith('/api/'):
        return jsonify(ok=False, message='请求格式或资源路径不正确', code=error.name.upper().replace(' ','_')), error.code
    return error


@bp.get('/')
def index():
    return render_template('index.html')


@bp.get('/api/bootstrap')
def bootstrap():
    return jsonify(authenticated=authenticated_user(get_db()) is not None, users=users(get_db()), csrf_token=csrf_token())


@bp.get('/healthz')
def health():
    from .policy import validate_policy
    conn = get_db()
    try:
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Database integrity check failed')
        validate_policy(conn)
    except Exception:
        return jsonify(ok=False, message='本地数据库或策略自检失败'), 503
    return jsonify(ok=True, schema_version=2, policy_revision=policy_revision(conn))


@bp.post('/api/login')
def login():
    payload, status = run_event('LOGIN_REQUEST', 'session:login', context=body())
    if payload['ok']:
        user_id, role_ids = payload.pop('user_id'), payload.pop('active_role_ids')
        nonce = payload.pop('session_nonce')
        session.clear()
        session['user_id'], session['active_role_ids'] = user_id, role_ids
        session['session_nonce'] = nonce
        session.permanent = True
        payload['csrf_token'] = csrf_token()
    return jsonify(payload), status


@bp.post('/api/logout')
def logout():
    require_user()
    payload, status = run_event('LOGOUT_REQUEST', 'session:logout')
    if payload['ok']:
        session.clear()
        payload['csrf_token'] = csrf_token()
    return jsonify(payload), status


@bp.get('/api/state')
def state():
    user_id, conn = require_user(), get_db()
    permissions = current_permissions(conn, user_id)
    assigned, active = direct_roles(conn,user_id), active_ids(conn,user_id)
    effective = role_closure(conn, active)
    all_roles = rows(conn,'SELECT * FROM sys_role ORDER BY id')
    return jsonify(ok=True,
        user=dict(conn.execute('SELECT id,username,display_name,title FROM sys_user WHERE id=?',(user_id,)).fetchone()),
        users=users(conn), roles=all_roles,
        permissions=rows(conn,'SELECT * FROM sys_permission ORDER BY id'),
        direct_role_ids=assigned, active_role_ids=active,
        effective_roles=[role for role in all_roles if role['id'] in effective],
        permission_codes=sorted(permissions),
        hierarchy=rows(conn,'SELECT * FROM sys_role_hierarchy ORDER BY senior_id,junior_id'),
        constraints=rows(conn,'SELECT * FROM sys_role_mutex ORDER BY role_a_id'),
        resources=resource_catalog(conn),policy_revision=policy_revision(conn),
        business=business_state(conn),csrf_token=csrf_token())


@bp.post('/api/session/roles')
def activate_roles():
    require_user()
    payload,status = run_event('ROLE_ACTIVATION','session:roles',context=body())
    if payload['ok']:
        session['active_role_ids'] = payload.pop('active_role_ids')
    return jsonify(payload),status


@bp.get('/api/navigation/<slug>')
def navigation(slug):
    if slug not in ('square','armory','treasury'):
        raise DomainError('导航入口不存在','NOT_FOUND',404)
    payload,status = run_event('ACCESS_CHECK',f'menu:{slug}',f'menu:{slug}',
                               result_builder=lambda conn: {'resources':resource_catalog(conn, slug)})
    return jsonify(payload),status


def document(conn, slug):
    row = conn.execute('SELECT * FROM kingdom_document WHERE slug=?',(slug,)).fetchone()
    data = {'title':row['title'],'content':row['content'],'records':json.loads(row['records_json'])}
    if slug == 'royal_ledger':
        data['records'] = rows(conn,
            'SELECT b.id,u.display_name AS 操作者,b.description AS 事项,b.amount AS 数量,b.created_at AS 时间 '
            'FROM business_ledger b JOIN sys_user u ON u.id=b.actor_id ORDER BY b.id DESC LIMIT 30')
        data['content'] += f' 当前银库余额：{business_state(conn)["gold"]} 格罗申。'
        data['audit_reports'] = rows(conn, 'SELECT * FROM audit_report ORDER BY id DESC LIMIT 5')
        for report in data['audit_reports']:
            report['consistent'] = bool(report['consistent'])
    return data


def page_result(slug):
    if slug not in ('tax_scroll','defense_map','royal_ledger'):
        raise DomainError('卷轴页面不存在','NOT_FOUND',404)
    return run_event('ACCESS_CHECK',f'page:{slug}',f'page:{slug}',
                     result_builder=lambda conn: document(conn,slug))


@bp.get('/api/pages/<slug>')
def api_page(slug):
    payload,status = page_result(slug)
    return jsonify(payload),status


@bp.get('/pages/<slug>')
def html_page(slug):
    payload,status = page_result(slug)
    if not payload['ok']:
        return render_template('resource.html',title='访问被拒绝',content=payload['message'],
                               records=[],event=payload['event']),status
    return render_template('resource.html',title=payload['title'],content=payload['content'],
                           records=payload['records'],event=payload['event']),status


@bp.post('/api/actions/<slug>')
def action(slug):
    if slug not in ('forge_sword','deploy_cavalry','open_vault','audit_ledger'):
        raise DomainError('政令动作不存在','NOT_FOUND',404)
    body()  # Reject malformed input; actor IDs and permission strings are ignored.
    payload,status = run_event('ACTION_REQUEST',f'btn:{slug}',f'btn:{slug}',{'slug':slug})
    return jsonify(payload),status


@bp.get('/api/events')
def events():
    user_id,conn = require_user(),get_db()
    can_read_all = 'admin:audit' in current_permissions(conn, user_id)
    where,params = ('',()) if can_read_all else ('WHERE a.actor_id=?',(user_id,))
    records = rows(conn,'SELECT a.*,COALESCE(u.display_name,\'访客\') AS actor_name '
                       'FROM audit_event a LEFT JOIN sys_user u ON u.id=a.actor_id '
                       f'{where} ORDER BY a.created_at DESC,a.rowid DESC LIMIT 40',params)
    for record in records:
        record['allowed'] = bool(record['allowed'])
        record['logs'] = json.loads(record.pop('logs_json'))
    return jsonify(ok=True,events=records)


def admin_data(conn):
    caps = sorted(set(permissions_for_roles(conn, active_ids(conn, session.get('user_id')))) &
                  {'admin:assign','admin:policy','admin:audit'})
    data = {'capabilities':caps,'policy_revision':policy_revision(conn)}
    if 'admin:assign' in caps or 'admin:policy' in caps:
        data['roles'] = rows(conn, 'SELECT * FROM sys_role ORDER BY id')
    if 'admin:assign' in caps:
        data['users'] = users(conn)
        data['user_role_map'] = {str(user['id']):direct_roles(conn,user['id']) for user in users(conn)}
    if 'admin:policy' in caps:
        data['permissions'] = rows(conn,'SELECT * FROM sys_permission ORDER BY id')
        data['role_permission_map'] = {str(role['id']):[r['permission_id'] for r in conn.execute(
            'SELECT permission_id FROM sys_role_permission WHERE role_id=? ORDER BY permission_id',(role['id'],))]
            for role in conn.execute('SELECT id FROM sys_role')}
        data['hierarchy'] = rows(conn,'SELECT * FROM sys_role_hierarchy ORDER BY senior_id,junior_id')
        data['constraints'] = rows(conn,'SELECT * FROM sys_role_mutex ORDER BY role_a_id')
    return data


@bp.get('/api/admin/state')
def admin_state():
    conn = get_db()
    available = permissions_for_roles(conn, active_ids(conn, session.get('user_id')))
    permission = next((p for p in ('admin:assign','admin:policy','admin:audit') if p in available), 'admin:assign')
    payload,status = run_event('ADMIN_READ','admin:state',permission,result_builder=admin_data)
    return jsonify(payload),status


@bp.put('/api/admin/users/<int:user_id>/roles')
def assign_roles(user_id):
    if not 0 < user_id <= 2**63-1:
        raise DomainError('用户 ID 超出有效整数范围')
    data = body()
    payload,status = run_event('ROLE_ASSIGN_REQUEST',f'user:{user_id}','admin:assign',
                               {'user_id':user_id,'role_ids':data.get('role_ids'),'base_revision':data.get('base_revision')})
    return jsonify(payload),status


@bp.put('/api/admin/roles/<int:role_id>/permissions')
def grant_permissions(role_id):
    if not 0 < role_id <= 2**63-1:
        raise DomainError('角色 ID 超出有效整数范围')
    data = body()
    payload,status = run_event('ROLE_PERMISSION_UPDATE',f'role:{role_id}','admin:policy',
                               {'role_id':role_id,'permission_ids':data.get('permission_ids'),'base_revision':data.get('base_revision')})
    return jsonify(payload),status


@bp.put('/api/admin/hierarchy')
def hierarchy():
    payload,status = run_event('HIERARCHY_UPDATE','role:hierarchy','admin:policy',body())
    return jsonify(payload),status
