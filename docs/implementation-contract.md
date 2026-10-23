# 接口与策略契约

应用为本地 Python Flask + SQLite 作业，不部署到公网。前端纯原生 JS/CSS，无 CDN。UTF-8 中文界面；主题为中世纪王室政令大厅。

## 安全和模型

- 密码登录，所有演示账号共用公开的教学密码 `Kingdom@2026`；服务端从 Flask 签名会话识别 actor，业务请求不接受 actor/user_id 作为身份。
- GET `/api/bootstrap` -> `{authenticated, users:[{id,username,display_name,title}], csrf_token}`。用户名 henry/radzig/hans/wenceslas/agnes；5 位分别平民、卫队长、财政大臣、国王、审计官。
- 所有 POST/PUT 必须 `X-CSRF-Token`；登录 POST `/api/login` body `{username,password}` -> `{ok,message,csrf_token}`；退出 POST `/api/logout`。
- RBAC0 用户/角色/权限和会话；默认激活所有直接分配角色。RBAC1 多继承 DAG（senior 继承 junior），闭包去重、防环。RBAC2 实现 SSD（财政与审计互斥，按有效角色闭包判断）；RBAC3 同时生效。额外拒绝有效权限同时包含拨款和审计的策略，防止直接授权绕过职责分离。国王无自动全权，不兼任财政与审计。
- 政策修改事务内检查：无环、所有用户有效角色 SSD、所有角色/用户的有效权限不能同时拨款与审计，至少一位用户同时拥有 admin:assign、admin:policy、admin:audit，失败全部回滚。角色激活必须是当前仍分配的角色子集；撤权下次请求即生效。
- 所有授权/约束由同步 EventBus + 监听器做出，默认拒绝。审计记录在数据库。业务写入事务后再响应；拒绝不改变业务数据。

## 权限码（数据库种子）

MENU: menu:square, menu:armory, menu:treasury。
PAGE: page:tax_scroll, page:defense_map, page:royal_ledger。
BUTTON: btn:forge_sword, btn:deploy_cavalry, btn:open_vault, btn:audit_ledger。
ADMIN: admin:assign, admin:policy, admin:audit。

roles 的 id/code/name: 1 PEASANT 行会平民，2 CAPTAIN 皇家卫队长，3 TREASURER 财政大臣，4 KING 至高国王，5 AUDITOR 皇家审计官。
hierarchy 初始 2->1,3->1,4->2,5->1。KING 直接权限 menu:treasury/page:royal_ledger/所有 ADMIN；不直接持有拨款或审计。

## 响应

一般成功 `{ok:true,message,...,event:{id,event_type,target,allowed,logs:[{step,detail,time}]}}`；错误 `{ok:false,message,code,event?}` 并返回 400/401/403/404/409。事件 UI 依次展示 EVENT_EMIT / LISTENER_CAPTURED / ROLE_CLOSURE / DECISION_ALLOW 或 DECISION_DENY / DB_COMMIT / AUDIT_PERSIST 等真实步骤，不伪造 SQL。

GET `/api/state` -> `{ok,user:{id,username,display_name,title}, users:[...], roles:[{id,code,name}], permissions:[{id,code,name,type,description}], direct_role_ids,active_role_ids,effective_roles:[{id,code,name}],permission_codes:[str],hierarchy:[{senior_id,junior_id}],constraints:[{role_a_id,role_b_id,reason}],business:{swords,cavalry_deployments,gold,audits},csrf_token}`。

POST `/api/session/roles` `{role_ids:[int]}` -> 更新激活子集（可空）；自助接口，不需管理权限。

GET `/api/navigation/<slug>` square/armory/treasury -> 检查相应 MENU -> `{ok,message,event}`。

GET `/api/pages/<slug>` tax_scroll/defense_map/royal_ledger -> PAGE 真实鉴权，成功 `{ok,title,content,records:[...],event}`。`records` 用通用对象数组，前端可安全 stringify 或表格。GET `/pages/<slug>` 返回真实受保护的独立 HTML（同一模板 resource.html；提供 title/content/records/event），无权返回 403 HTML，不返回秘密内容。

POST `/api/actions/<slug>` forge_sword/deploy_cavalry/open_vault/audit_ledger，body `{}`。成功真实更新 SQLite（铸剑+1、骑兵出征+1、拨款-100、审计+1），返回 `{ok,message,business,event}`。

GET `/api/events` -> `{ok,events:[{id,event_type,target,allowed,message,created_at,actor_name,logs:[...]}]}`，最近40条新到旧；普通账号仅自己，国王有 admin:audit 可看全部。

GET `/api/admin/state` 要求任一已激活admin能力，返回`capabilities`与`policy_revision`；按能力裁剪字段：assign得到users/roles/user_role_map，policy得到roles/permissions/role_permission_map/hierarchy/constraints，audit-only只返回能力和版本，全局事件另从`/api/events`读取。
PUT `/api/admin/users/<int:user_id>/roles` 要求admin:assign，body `{role_ids:[int],base_revision:int}`，还检查委派边界与禁止修改自身分配。
PUT `/api/admin/roles/<int:role_id>/permissions` 要求admin:policy，body `{permission_ids:[int],base_revision:int}`，还检查修改前有效权限的委派范围。
PUT `/api/admin/hierarchy` 要求admin:policy，body `{edges:[{senior_id,junior_id}],base_revision:int}`（完整替换），同样不能通过继承绕过委派边界。过期版本409 `STALE_POLICY`，成功返回新`policy_revision`。

`/api/state`新增`resources:{menus,pages,actions}`与`policy_revision`；资源字段及本轮委派边界详见[修订合同](revision-contract.md)。目录存数据库，不含受保护正文；菜单导航成功返回该领地目录。

`audit_ledger`返回真实`audit_report`：开账余额、金币流入/流出、预期/实际余额、差异、是否一致及覆盖流水上界；审计次数+1不再是全部业务语义。所有报告、计数、流水和成功审计一起提交。

GET `/` 渲染 index.html。前端 script 使用 `url_for('static',filename='app.js')`，style 同理。index/resource 都自己负责模板，无 Jinja 上下文额外依赖（resource 的四个字段见上）。

