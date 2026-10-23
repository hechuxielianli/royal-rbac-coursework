'use strict';

// The browser uses permissions to present the interface. The server decides every request.
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
let state = null;
let csrfToken = '';
let currentTerritory = null;
let currentView = 'governance';
let eventFilter = 'all';
let events = [];
let adminState = null;
let adminTab = 'assign';
let selectedUserId = 1;
let selectedRoleId = 1;
let draftEdges = [];
let notificationTimeout;
let adminDirty = false;
let pendingAdminAction = null;
let appliedHash = '';
let routeVersion = 0;
let recoveringSession = null;

// Remember a control across a policy refresh that may rebuild its DOM node.
function describeTrigger(trigger) {
  if (!trigger || trigger.selector !== undefined) return trigger;
  const selector = trigger.id ? `#${CSS.escape(trigger.id)}` : trigger.dataset.testid
    ? `[data-testid="${CSS.escape(trigger.dataset.testid)}"]` : null;
  return {element:trigger, selector};
}

function interceptShake(trigger) {
  const source = describeTrigger(trigger);
  const element = source?.element?.isConnected ? source.element : source?.selector ? $(source.selector) : null;
  if (!element || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (element._interceptCleanup) element._interceptCleanup();
  element.classList.remove('intercept-shake');
  void element.offsetWidth; // Restart the same short animation on repeated refusals.
  element.classList.add('intercept-shake');
  const cleanup = () => {
    clearTimeout(timer);
    element.classList.remove('intercept-shake');
    element.removeEventListener('animationend', onEnd);
    delete element._interceptCleanup;
  };
  const onEnd = event => { if (event.target === element && event.animationName === 'intercept-shake') cleanup(); };
  const timer = setTimeout(cleanup, 420);
  element._interceptCleanup = cleanup;
  element.addEventListener('animationend', onEnd);
}

function isIntercept(error) {
  return error.status === 403 || error.status === 409 || error.data?.event?.allowed === false;
}

function eventVerdict(event) {
  if (event.allowed) return 'allow';
  const conflicts = new Set(['SSD_CONFLICT', 'PERMISSION_CONFLICT', 'HIERARCHY_CYCLE',
    'LAST_ADMINISTRATOR', 'STALE_POLICY', 'INSUFFICIENT_GOLD']);
  const denial = (event.logs || []).find(log => log.step === 'DECISION_DENY');
  return conflicts.has(String(denial?.detail || '').split(':')[0]) ? 'conflict' : 'deny';
}

function hasPermission(code) { return Boolean(state?.permission_codes?.includes(code)); }
function roleName(id) { return state?.roles?.find(role => Number(role.id) === Number(id))?.name || `角色 ${id}`; }
function canManage() { return (state?.permission_codes || []).some(code => code.startsWith('admin:')); }
function resources(kind) { return state?.resources?.[kind] || []; }
function firstTerritory() { return resources('menus').find(menu => hasPermission(menu.code))?.slug || null; }
function territoryLabel(slug) { return resources('menus').find(menu => menu.slug === slug)?.title || slug; }
function clearViewport() { $('#pageViewport').hidden = true; $('#pageViewport').replaceChildren(); }
function focusHeading(root) {
  const heading = $('h2,h3', root);
  if (heading) {
    heading.tabIndex = -1;
    // Native focus scrolling is immediate and also respects reduced-motion users.
    heading.focus();
  }
}

function notify(message, isError = false) {
  const box = $('#notification');
  box.textContent = message;
  box.className = `notification${isError ? ' error' : ''}`;
  box.hidden = false;
  clearTimeout(notificationTimeout);
  notificationTimeout = setTimeout(() => { box.hidden = true; }, isError ? 8500 : 5000);
}

async function api(path, {method = 'GET', body} = {}) {
  const headers = {'Accept':'application/json'};
  if (method !== 'GET') {
    headers['Content-Type'] = 'application/json';
    headers['X-CSRF-Token'] = csrfToken;
  }
  const response = await fetch(path, {method, headers, credentials:'same-origin', cache:'no-store', ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  const contentType = response.headers.get('Content-Type') || '';
  if (!contentType.includes('application/json')) throw new Error(`服务器返回了非预期响应（${response.status}），请刷新后重试。`);
  const data = await response.json();
  if (data.csrf_token) csrfToken = data.csrf_token;
  if (data.event) addEvent({...data.event, message:data.message, actor_name:state?.user?.display_name});
  if (!response.ok || data.ok === false) {
    const error = new Error(data.message || `请求失败（${response.status}）`);
    error.data = data;
    error.status = response.status;
    if ((response.status === 401 && path !== '/api/login') || data.code === 'CSRF_FAILED') {
      await recoverSession();
      error.message = state ? '通行令牌已更新，请检查当前状态后重新操作；原请求未自动重发。' : '会话已过期，请重新登录。原操作未执行或重发。';
    }
    throw error;
  }
  return data;
}

async function busy(button, task) {
  const trigger = describeTrigger(button);
  const wasDisabled = button?.disabled;
  if (button) { button.disabled = true; button.setAttribute('aria-busy','true'); }
  try { return await task(); }
  catch (error) {
    notify(error.message || '网络连接失败，请稍后重试。', true);
    if (state && error.status === 403 && error.data?.code === 'PERMISSION_DENIED') {
      try { await loadState(); } catch (_) { /* The original error remains visible. */ }
    }
    if (isIntercept(error)) interceptShake(trigger);
  }
  finally { if (button?.isConnected) { button.disabled = wasDisabled; button.removeAttribute('aria-busy'); } }
}

function showLogin() {
  $('#application').hidden = true;
  $('#loginScreen').hidden = false;
  state = null;
  adminState = null;
  adminDirty = false;
  pendingAdminAction = null;
  clearViewport();
  events = [];
  renderEvents();
  $('#loginUser').focus();
}

// Refresh authentication and CSRF state, but never replay an interrupted write.
async function bootstrap() {
  const response = await fetch('/api/bootstrap', {credentials:'same-origin',cache:'no-store'});
  if (!response.ok) throw new Error('无法恢复通行令牌，请检查本地服务。');
  const data = await response.json();
  csrfToken = data.csrf_token;
  const previous = $('#loginUser').value;
  if (data.users?.length) $('#loginUser').innerHTML = data.users.map(user => `<option value="${escapeHTML(user.username)}">${escapeHTML(user.display_name)} · ${escapeHTML(user.title)}</option>`).join('');
  if ([...$('#loginUser').options].some(option => option.value === previous)) $('#loginUser').value = previous;
  return data;
}

async function recoverSession() {
  if (!recoveringSession) recoveringSession = (async () => {
    const data = await bootstrap();
    if (!data.authenticated) showLogin();
  })().finally(() => { recoveringSession = null; });
  return recoveringSession;
}

async function loadState() {
  const data = await api('/api/state');
  state = data;
  if (!resources('menus').some(menu => menu.slug === currentTerritory && hasPermission(menu.code))) currentTerritory = firstTerritory();
  $('#application').hidden = false;
  $('#loginScreen').hidden = true;
  renderState();
}

function renderState() {
  const user = state.user;
  $('#sidebarIdentity').innerHTML = `<div class="identity-name">${escapeHTML(user.display_name)}</div><div class="identity-title">${escapeHTML(user.title)}</div>`;
  const activeIds = state.active_role_ids || [];
  const directIds = state.direct_role_ids || [];
  const effective = state.effective_roles || [];
  $('#roleBadges').innerHTML = effective.map(role => `<span class="role-tag${directIds.includes(role.id) ? '' : ' inherited'}" title="${directIds.includes(role.id) ? '会话激活的直接角色' : '通过层级继承获得'}">${escapeHTML(role.name)} · ${directIds.includes(role.id) ? '直接' : '继承'}</span>`).join('') || '<span class="role-tag inactive">当前未激活任何角色</span>';
  $('#activeRoleChoices').innerHTML = directIds.map(id => `<label class="check-row"><input type="checkbox" name="activeRole" value="${id}"${activeIds.includes(id) ? ' checked' : ''}><span>${escapeHTML(roleName(id))}</span></label>`).join('') || '<p>未分配角色</p>';
  $('#adminButton').hidden = !canManage();
  $('#auditScope').textContent = `${hasPermission('admin:audit') ? '全王国' : '当前身份'} · 最近40条 · 手动刷新`;
  renderBusiness();
  renderNavigation();
  renderResources();
  renderModel();
  if (currentView === 'admin' && !canManage()) { adminDirty = false; setView('governance'); }
  const probeSelect = $('#probeSelect');
  const previousProbe = probeSelect.value;
  probeSelect.innerHTML = (state.permissions || []).filter(permission => ['MENU','PAGE','BUTTON'].includes(permission.type)).map(permission => `<option value="${escapeHTML(permission.code)}">${hasPermission(permission.code) ? '✓ 已授权' : '⊘ 未授权'} · ${escapeHTML(permission.name)} (${escapeHTML(permission.code)})</option>`).join('');
  if ([...probeSelect.options].some(option => option.value === previousProbe)) probeSelect.value = previousProbe;
  else { const deniedOption = [...probeSelect.options].find(option => option.textContent.startsWith('⊘')); if (deniedOption) probeSelect.value = deniedOption.value; }
  ensureMobileTools();
}

function renderBusiness() {
  const business = state.business || {};
  for (const [id,key] of [['metricSwords','swords'],['metricCavalry','cavalry_deployments'],['metricGold','gold'],['metricAudits','audits']]) {
    $(`#${id}`).textContent = Number(business[key] ?? 0).toLocaleString('zh-CN');
  }
}

function renderNavigation() {
  const showLocked = $('#showLocked').checked;
  $('#territoryNav').innerHTML = resources('menus').filter(menu => showLocked || hasPermission(menu.code)).map(menu => {
    const active = currentTerritory === menu.slug && currentView === 'governance' && hasPermission(menu.code);
    return `<button class="nav-button${active ? ' active' : ''}${hasPermission(menu.code) ? '' : ' locked'}" data-nav="${escapeHTML(menu.slug)}" data-testid="nav-${escapeHTML(menu.slug)}"${active ? ' aria-current="page"' : ''}><span class="nav-symbol" aria-hidden="true">${escapeHTML(menu.icon)}</span><span>${escapeHTML(menu.title)}</span><span class="nav-arrow">${hasPermission(menu.code) ? '→' : '⊘'}</span></button>`;
  }).join('') || '<div class="empty-nav">本会话没有领地权限。可使用跨域实验区单独验证页面和动作。</div>';
}

function pageCards(items, crossDomain = false) {
  return items.map(page => {
    const allowed = hasPermission(page.code);
    return `<article class="page-card${allowed ? '' : ' denied'}"><div class="page-card-title"><span aria-hidden="true">${escapeHTML(page.icon)}</span><span class="lock" title="${allowed ? '拥有页面权限' : '没有页面权限'}">${allowed ? '↗' : '⊘'}</span></div><h4>${escapeHTML(page.title)}</h4><p>${crossDomain ? `<span class="domain-tag">${escapeHTML(territoryLabel(page.territory))}</span>` : ''}${escapeHTML(page.description)}</p><div class="page-card-actions"><button data-page="${escapeHTML(page.slug)}" data-testid="${crossDomain ? 'cross-' : ''}page-${escapeHTML(page.slug)}">${allowed ? '展开卷轴 →' : '验证访问 ⊘'}</button><a href="/pages/${encodeURIComponent(page.slug)}" target="_blank" rel="noopener" title="直接请求服务器受保护页面">独立页面 ↗</a></div></article>`;
  }).join('');
}

function actionCards(items, crossDomain = false) {
  return items.map(action => {
    const allowed = hasPermission(action.code);
    return `<article class="action-card${allowed ? '' : ' denied'}"><span class="action-symbol" aria-hidden="true">${escapeHTML(action.icon)}</span><div><h4>${escapeHTML(action.title)}</h4><p>${crossDomain ? `<span class="domain-tag">${escapeHTML(territoryLabel(action.territory))}</span>` : ''}${escapeHTML(action.description)}</p><button data-action="${escapeHTML(action.slug)}" data-testid="${crossDomain ? 'cross-' : ''}action-${escapeHTML(action.slug)}"${allowed ? '' : ' disabled'}><span>${allowed ? '执行政令' : '未获授权'}</span><span>${allowed ? '→' : '⊘'}</span></button></div></article>`;
  }).join('');
}

function renderResources() {
  const territory = resources('menus').find(menu => menu.slug === currentTerritory && hasPermission(menu.code));
  $('#territoryTitle').textContent = territory?.title || '暂无可进入的领地';
  $('#territoryEyebrow').textContent = territory?.eyebrow || 'NO ACTIVE TERRITORY';
  $('#territorySubtitle').textContent = territory?.subtitle || '当前会话没有菜单权限。';
  $('#territoryDescription').textContent = territory?.description || '可在跨域实验区分别验证独立的页面和功能权限。';
  $('#territoryNumber').textContent = territory?.number || '—';
  if ($('#territoryEmpty')) $('#territoryEmpty').hidden = Boolean(territory);
  if (!territory && $('#crossDomainPanel')) $('#crossDomainPanel').open = true;
  $('#pageCards').innerHTML = pageCards(resources('pages').filter(page => territory && page.territory === territory.slug));
  $('#actionCards').innerHTML = actionCards(resources('actions').filter(action => territory && action.territory === territory.slug));
  if ($('#crossPageCards')) $('#crossPageCards').innerHTML = pageCards(resources('pages'), true);
  if ($('#crossActionCards')) $('#crossActionCards').innerHTML = actionCards(resources('actions'), true);
}

function renderModel() {
  const direct = state.direct_role_ids || [];
  const active = state.active_role_ids || [];
  const effective = state.effective_roles || [];
  $('#modelContent').innerHTML = `
    <div class="model-detail"><h4>身份 / USER</h4><div class="model-tags"><span class="model-tag">${escapeHTML(state.user.display_name)}<small>${escapeHTML(state.user.username)}</small></span></div></div>
    <div class="model-detail"><h4>已分配角色 / ASSIGNED ROLES</h4><div class="model-tags">${direct.map(id => `<span class="model-tag">${escapeHTML(roleName(id))}<small>${active.includes(id) ? '已激活' : '未激活'}</small></span>`).join('') || '<p>无已分配角色</p>'}</div></div>
    <div class="model-detail"><h4>会话激活 / ACTIVE SUBSET</h4><form id="modelActivationForm"><div class="checkbox-grid">${direct.map(id => `<label class="check-row"><input type="checkbox" name="activeRole" value="${id}"${active.includes(id) ? ' checked' : ''}><span>${escapeHTML(roleName(id))}</span></label>`).join('') || '<p>无可激活角色</p>'}</div><button class="small-button" type="submit">保存会话激活角色</button></form></div>
    <div class="model-detail"><h4>本会话有效角色 / ROLE CLOSURE</h4><div class="model-tags">${effective.map(role => `<span class="model-tag">${escapeHTML(role.name)}<small>${direct.includes(role.id) ? '直接' : '继承'}</small></span>`).join('') || '<p>空角色集合，所有受控资源默认拒绝</p>'}</div></div>
    <div class="model-detail"><h4>角色继承 / SENIOR → JUNIOR</h4>${(state.hierarchy || []).map(edge => `<div class="hierarchy-line"><span>${escapeHTML(roleName(edge.senior_id))}</span><span>→</span><span>${escapeHTML(roleName(edge.junior_id))}</span></div>`).join('')}<p>箭头左侧角色继承右侧角色的全部权限。</p></div>
    <div class="model-detail"><h4>职责分离 / SEPARATION OF DUTY</h4>${(state.constraints || []).map(rule => `<p>${escapeHTML(roleName(rule.role_a_id))} × ${escapeHTML(roleName(rule.role_b_id))}<br>${escapeHTML(rule.reason)}</p>`).join('')}<p>附加业务约束：任何角色或用户的有效权限中，拨款与审计不能同时存在。国王也受此约束。</p></div>
    <div class="model-detail"><h4>本会话有效权限 / EFFECTIVE PERMISSIONS</h4><div class="model-tags">${(state.permission_codes || []).map(code => `<span class="model-tag"><code>${escapeHTML(code)}</code></span>`).join('') || '<p>无有效权限</p>'}</div></div>`;
}

function ensureMobileTools() {
  let tools = $('#mobileTools');
  if (!tools) {
    tools = document.createElement('div');
    tools.id = 'mobileTools';
    tools.className = 'mobile-tools';
    $('.hero').after(tools);
  }
  tools.innerHTML = `<button data-mobile-view="model">权限图谱与会话</button>${canManage() ? '<button data-mobile-view="admin">枢密院管理</button>' : ''}<label><input type="checkbox" id="mobileLocked"${$('#showLocked').checked ? ' checked' : ''}>显示无权领地</label>`;
}

function setView(view) {
  currentView = view;
  $('#governanceView').hidden = view !== 'governance';
  $('#modelView').hidden = view !== 'model';
  $('#adminView').hidden = view !== 'admin';
  $('#modelButton').classList.toggle('active', view === 'model');
  $('#adminButton').classList.toggle('active', view === 'admin');
  renderNavigation();
}

async function navigate(slug, trigger = null) {
  return routeTo(`/territory/${encodeURIComponent(slug)}`, {trigger});
}

function adminFeedback(message = '', {error = false, discard = false} = {}) {
  const box = $('#adminFeedback');
  if (!box) return;
  box.hidden = !message;
  box.tabIndex = -1;
  box.className = `inline-feedback${error ? ' error' : ''}`;
  box.textContent = message;
  if (discard) {
    const button = document.createElement('button');
    button.type = 'button';
    button.id = 'adminDiscard';
    button.className = 'small-button';
    button.textContent = '放弃未保存草稿并继续';
    box.append(document.createElement('br'), button);
  }
}

function markDirty() {
  adminDirty = true;
  updateAdminRevision();
}

function updateAdminRevision() {
  if ($('#adminRevision')) $('#adminRevision').textContent = `策略快照 v${adminState?.policy_revision ?? '—'}`;
  if ($('#adminDirty')) { $('#adminDirty').hidden = !adminDirty; $('#adminDirty').textContent = '有未保存修改'; }
}

function allowDraftExit(action) {
  if (!adminDirty) return true;
  pendingAdminAction = action;
  adminFeedback('当前草稿尚未保存。候选内容已保留；保存、继续编辑，或明确放弃后再离开。', {error:true,discard:true});
  $('#adminFeedback')?.focus({preventScroll:true});
  return false;
}

async function routeTo(path, {replace = false, force = false, trigger = null} = {}) {
  const source = describeTrigger(trigger);
  const nextHash = `#${path}`;
  if (!force && nextHash === appliedHash) return adminDirty ? undefined : applyRoute(source);
  if (!force && nextHash !== appliedHash && !allowDraftExit(() => routeTo(path,{replace,force:true,trigger:source}))) return;
  if (force) { adminDirty = false; pendingAdminAction = null; }
  if (location.hash !== nextHash) history[replace ? 'replaceState' : 'pushState'](null, '', nextHash);
  await applyRoute(source);
}

async function applyRoute(trigger = null) {
  if (!state) return;
  const version = ++routeVersion;
  const requestedHash = location.hash;
  if (adminDirty && requestedHash !== appliedHash && !allowDraftExit(() => routeTo(requestedHash.slice(1),{force:true}))) {
    history.replaceState(null, '', appliedHash || '#/model');
    return;
  }
  appliedHash = requestedHash;
  const parts = requestedHash.replace(/^#\/?/, '').split('/').filter(Boolean);
  let slug;
  try { slug = decodeURIComponent(parts[1] || ''); } catch (_) { slug = ''; }
  clearViewport();
  try {
    if (!parts.length) {
      const first = firstTerritory();
      return routeTo(first ? `/territory/${encodeURIComponent(first)}` : '/experiment', {replace:true,force:true});
    }
    if (parts[0] === 'model') { setView('model'); focusHeading($('#modelView')); return; }
    if (parts[0] === 'admin') { await openAdmin(slug); return; }
    setView('governance');
    if (parts[0] === 'experiment') {
      $('#crossDomainPanel') && ($('#crossDomainPanel').open = true);
      renderResources();
      focusHeading($('#governanceView'));
      return;
    }
    if (parts[0] === 'pages' && slug) {
      await openPage(slug);
      return;
    }
    if (parts[0] === 'territory' && slug) {
      await api(`/api/navigation/${encodeURIComponent(slug)}`);
      if (version !== routeVersion || !state) return;
      currentTerritory = slug;
      renderNavigation();
      renderResources();
      focusHeading($('#governanceView'));
      return;
    }
    throw new Error('该大厅路径不存在，请使用领地导航或跨域实验区。');
  } catch (error) {
    if (version !== routeVersion || !state) return;
    setView('governance');
    showDenied(error, parts[0] === 'pages' ? `page:${slug}` : `menu:${slug}`, parts[0] === 'pages' ? slug : null);
    notify(error.message, true);
    if (isIntercept(error)) interceptShake(trigger);
  }
}

window.addEventListener('hashchange', () => { applyRoute(); });
window.addEventListener('beforeunload', event => {
  if (adminDirty) { event.preventDefault(); event.returnValue = ''; }
});

function recordTable(records) {
  if (!Array.isArray(records) || records.length === 0) return '';
  const objects = records.every(record => record && typeof record === 'object' && !Array.isArray(record));
  if (!objects) return `<p>${records.map(record => escapeHTML(typeof record === 'object' ? JSON.stringify(record) : record)).join('<br>')}</p>`;
  const keys = [...new Set(records.flatMap(record => Object.keys(record)))];
  return `<div class="table-scroll"><table class="record-table"><thead><tr>${keys.map(key => `<th>${escapeHTML(key)}</th>`).join('')}</tr></thead><tbody>${records.map(record => `<tr>${keys.map(key => `<td>${escapeHTML(typeof record[key] === 'object' ? JSON.stringify(record[key]) : record[key])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function showDenied(error, target, slug = null) {
  const viewport = $('#pageViewport');
  const event = error.data?.event;
  viewport.classList.add('denial-panel');
  viewport.innerHTML = `<div class="viewport-top"><h3>访问被拒绝 · 操作未生效</h3><button data-close-page aria-label="关闭拒绝详情">×</button></div><p>${escapeHTML(error.message)}</p><p>目标 <code>${escapeHTML(event?.target || target)}</code> · 事件 <code>${escapeHTML(event?.id || '本次响应未生成事件')}</code></p><p>当前有效角色：${escapeHTML((state?.effective_roles || []).map(role => role.name).join('、') || '空集合')}</p>${event ? `<div class="denial-trace">${(event.logs || []).map(log => `<div class="scroll-log"><code>${escapeHTML(log.step)}</code><span>${escapeHTML(log.detail)}</span></div>`).join('')}</div>` : ''}${slug ? `<a class="text-button" href="/pages/${encodeURIComponent(slug)}" target="_blank" rel="noopener">独立URL验证：/pages/${escapeHTML(slug)} ↗</a>` : ''}`;
  viewport.hidden = false;
  viewport.dataset.testid = 'access-denied';
  focusHeading(viewport);
}

function auditReportHTML(report) {
  if (!report) return '';
  const labels = {opening_gold:'期初金币',total_credits:'入账金币合计',total_debits:'支出金币合计',expected_gold:'账本预期余额',actual_gold:'实际国库余额',difference:'实际 − 预期',ledger_count:'核对流水条数',through_ledger_id:'核对至流水ID'};
  return `<section class="audit-report" data-testid="audit-report"><h3>独立审计报告 #${escapeHTML(report.id)}</h3><p class="audit-result${report.consistent ? '' : ' error'}">${report.consistent ? '核对一致：账本与国库余额相符。' : '发现差异：账本与国库余额不符。'}</p><p>预期余额 = 期初金币 + 入账金币 − 支出金币。仅汇总金币业务流水。</p><dl>${Object.entries(labels).map(([key,label]) => `<div><dt>${label}</dt><dd data-report-field="${key}">${escapeHTML(report[key])}</dd></div>`).join('')}</dl><p>生成时间：${escapeHTML(report.created_at)}。报告与业务记录在同一事务持久化。</p></section>`;
}

async function openPage(slug) {
  // Remove previously displayed protected content before making a fresh request.
  const viewport = $('#pageViewport');
  viewport.hidden = true;
  viewport.replaceChildren();
  const requestVersion = routeVersion;
  const data = await api(`/api/pages/${encodeURIComponent(slug)}`);
  if (!state || requestVersion !== routeVersion) return;
  viewport.classList.remove('denial-panel');
  viewport.dataset.testid = 'page-content';
  viewport.innerHTML = `<div class="viewport-top"><h3>${escapeHTML(data.title)}</h3><button data-close-page aria-label="关闭卷轴">×</button></div><p>${escapeHTML(data.content)}</p>${recordTable(data.records)}${(data.audit_reports || []).map(auditReportHTML).join('')}<a class="text-button" href="/pages/${encodeURIComponent(slug)}" target="_blank" rel="noopener">在独立受控页面中打开 ↗</a>`;
  viewport.hidden = false;
  focusHeading(viewport);
  notify(data.message || '卷轴已通过服务端鉴权。');
}

async function executeAction(slug) {
  try {
    const data = await api(`/api/actions/${encodeURIComponent(slug)}`, {method:'POST', body:{}});
    if (!state) return;
    if (data.business) { state.business = data.business; renderBusiness(); }
    else await loadState();
    if (data.audit_report) {
      const viewport = $('#pageViewport');
      viewport.classList.remove('denial-panel');
      viewport.dataset.testid = 'audit-result';
      viewport.innerHTML = auditReportHTML(data.audit_report);
      viewport.hidden = false;
      focusHeading(viewport);
    }
    notify(data.message || '政令已生效并写入数据库。');
  } catch (error) {
    if (state && error.status === 403) showDenied(error, `btn:${slug}`);
    throw error;
  }
}

function addEvent(event) {
  if (!event.id) return;
  const existing = events.find(item => item.id === event.id);
  events = [{...(existing || {}), ...event}, ...events.filter(item => item.id !== event.id)].slice(0, 40);
  renderEvents(event.id);
}

function renderEvents(openId) {
  const previouslyOpen = new Set($$('.event-entry[open]').map(entry => entry.dataset.eventId));
  const previouslyRendered = new Set($$('.event-entry').map(entry => entry.dataset.eventId));
  $('#eventCount').textContent = events.length;
  const filtered = eventFilter === 'denied' ? events.filter(event => !event.allowed) : events;
  const feed = $('#eventFeed');
  if (filtered.length === 0) {
    feed.innerHTML = `<div class="event-empty"><span>⌁</span><p>${eventFilter === 'denied' ? '当前没有拒绝事件' : '等待第一个政令事件'}</p><small>${eventFilter === 'denied' ? '通过越权验证沙盘，验证服务端如何拒绝请求。' : '打开卷轴或执行操作，查看真实授权轨迹。'}</small></div>`;
    return;
  }
  feed.innerHTML = filtered.map((event, index) => {
    const denied = !event.allowed;
    const verdict = eventVerdict(event);
    const labels = {allow:['ALLOW','授权放行'], deny:['DENY','卫兵拦截'], conflict:['CONFLICT','约束冲突']};
    const [badge, label] = labels[verdict];
    const timestamp = event.created_at || event.logs?.[0]?.time || '';
    const parsedTime = new Date(timestamp);
    const time = Number.isNaN(parsedTime.getTime()) ? timestamp.slice(0,12) : parsedTime.toLocaleTimeString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false});
    const shortId = String(event.id).slice(0,8);
    const open = event.id === openId || previouslyOpen.has(String(event.id)) || (index === 0 && previouslyOpen.size === 0);
    const arrived = !previouslyRendered.has(String(event.id));
    return `<details class="event-entry event-card verdict-${verdict}${arrived ? ' event-arrival' : ''}" data-event-id="${escapeHTML(event.id)}"${open ? ' open' : ''}><summary><div class="entry-meta"><span class="decision${denied ? ' denied' : ''}${verdict === 'conflict' ? ' conflict' : ''}">${badge}</span><time datetime="${escapeHTML(timestamp)}">${escapeHTML(time)}</time><span>#${escapeHTML(shortId)}</span></div><div class="event-status-label">${label}</div><div class="entry-name">${escapeHTML(event.target || event.event_type)}<span aria-hidden="true">⌄</span></div><div class="entry-actor">${escapeHTML(event.actor_name || state?.user?.display_name || '匿名请求')} · ${escapeHTML(event.event_type || '')}</div></summary><div class="event-logs">${(event.logs || []).map(log => `<div class="event-log${/DENY|VIOLATION|CONFLICT|ROLLBACK|ERROR/i.test(log.step) ? verdict === 'conflict' ? ' conflict' : ' deny' : ''}"><code>${escapeHTML(log.step)}</code><p>${escapeHTML(log.detail)}</p></div>`).join('')}${event.message ? `<div class="event-log${denied ? verdict === 'conflict' ? ' conflict' : ' deny' : ''}"><code>RESPONSE</code><p>${escapeHTML(event.message)}</p></div>` : ''}</div></details>`;
  }).join('');
  if (openId) feed.scrollTop = 0;
}

async function refreshEvents() {
  const data = await api('/api/events');
  events = data.events || [];
  renderEvents();
}

function adminTabs() {
  const capabilities = adminState?.capabilities || [];
  return ['assign','permissions','hierarchy','audit'].filter(tab => capabilities.includes({assign:'admin:assign',permissions:'admin:policy',hierarchy:'admin:policy',audit:'admin:audit'}[tab]));
}

async function openAdmin(requestedTab = '') {
  const expectedRouteVersion = routeVersion;
  const retainTabFocus = currentView === 'admin' && document.activeElement?.matches('[data-admin-tab]');
  const snapshot = await api('/api/admin/state');
  if (!state || expectedRouteVersion !== routeVersion) return;
  adminState = snapshot;
  adminDirty = false;
  pendingAdminAction = null;
  adminFeedback();
  draftEdges = (adminState.hierarchy || []).map(edge => ({...edge}));
  if (!adminState.users?.some(user => Number(user.id) === Number(selectedUserId) && user.id !== state.user.id)) selectedUserId = adminState.users?.find(user => user.id !== state.user.id)?.id;
  const tabs = adminTabs();
  adminTab = tabs.includes(requestedTab) ? requestedTab : tabs[0];
  if (!adminTab) throw new Error('当前会话没有可用的管理能力。');
  const canonicalHash = `#/admin/${adminTab}`;
  if (location.hash !== canonicalHash) history.replaceState(null, '', canonicalHash);
  appliedHash = canonicalHash;
  setView('admin');
  renderAdmin();
  if (adminTab === 'audit') await refreshEvents();
  if (retainTabFocus) $(`[data-admin-tab="${adminTab}"]`)?.focus();
  else focusHeading($('#adminContent'));
}

function renderAdmin() {
  if (!adminState) return;
  $$('[data-admin-tab]').forEach(button => {
    const active = button.dataset.adminTab === adminTab;
    button.hidden = !adminTabs().includes(button.dataset.adminTab);
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
    button.setAttribute('aria-controls','adminContent');
    button.tabIndex = active ? 0 : -1;
  });
  $('#adminContent').setAttribute('role','tabpanel');
  const activeTab = $(`[data-admin-tab="${adminTab}"]`);
  if (activeTab) { if (!activeTab.id) activeTab.id = `tab-${adminTab}`; $('#adminContent').setAttribute('aria-labelledby',activeTab.id); }
  updateAdminRevision();
  const roleOptions = selected => (adminState.roles || []).map(role => `<option value="${role.id}"${Number(role.id) === Number(selected) ? ' selected' : ''}>${escapeHTML(role.name)} · ${escapeHTML(role.code)}</option>`).join('');
  if (adminTab === 'assign') {
    const assigned = adminState.user_role_map[String(selectedUserId)] || [];
    $('#adminContent').innerHTML = `<div class="eyebrow">USER ASSIGNMENT / UA</div><h3>册封爵位，赋予职责。</h3><p class="panel-intro">选择目标臣民，保存完整的直接分配角色集合。</p><form id="assignForm"><div class="admin-select-row"><label for="assignUser">目标臣民</label><select id="assignUser" data-testid="assign-user">${adminState.users.map(user => `<option value="${user.id}"${Number(user.id) === Number(selectedUserId) ? ' selected' : ''}${user.id === state.user.id ? ' disabled' : ''}>${escapeHTML(user.display_name)}${user.id === state.user.id ? '（本人不可修改）' : ''} · ${escapeHTML(user.username)}</option>`).join('')}</select></div><div class="checkbox-grid">${adminState.roles.map(role => `<label class="check-row"><input type="checkbox" name="assignedRole" value="${role.id}"${assigned.includes(role.id) ? ' checked' : ''} data-testid="assign-role-${role.code}"><span class="check-row-text">${escapeHTML(role.name)}<small>${escapeHTML(role.code)}</small></span></label>`).join('')}</div><div class="admin-note">禁止修改本人的角色分配。部分册封官仅能向其他用户委派自己的当前业务权限覆盖的非管理角色；完整管理员也受职责分离与最后管理员约束。新角色需要目标会话激活，撤权下一次请求生效。</div><button type="submit" class="small-button admin-save" data-testid="save-assignment"${hasPermission('admin:assign') ? '' : ' disabled'}>颁布册封圣旨 →</button></form>`;
  } else if (adminTab === 'permissions') {
    const assigned = adminState.role_permission_map[String(selectedRoleId)] || [];
    $('#adminContent').innerHTML = `<div class="eyebrow">PERMISSION ASSIGNMENT / PA</div><h3>每项政令，授权有据。</h3><p class="panel-intro">编辑角色的直接权限。继承权限通过下级角色自动汇总。</p><form id="permissionForm"><div class="admin-select-row"><label for="permissionRole">目标爵位</label><select id="permissionRole" data-testid="permission-role">${roleOptions(selectedRoleId)}</select></div><div class="checkbox-grid">${adminState.permissions.map(permission => `<label class="check-row"><input type="checkbox" name="permission" value="${permission.id}"${assigned.includes(permission.id) ? ' checked' : ''} data-testid="permission-${escapeHTML(permission.code)}"><span class="check-row-text"><span><span class="permission-type">${escapeHTML(permission.type)}</span>${escapeHTML(permission.name)}</span><small>${escapeHTML(permission.code)}</small></span></label>`).join('')}</div><div class="admin-note">拨款和审计权限不能同时出现在同一角色或用户的有效权限集合中；系统也会保护最后一位完整管理员。</div><button type="submit" class="small-button admin-save" data-testid="save-permissions">保存权限矩阵 →</button></form>`;
  } else if (adminTab === 'hierarchy') {
    $('#adminContent').innerHTML = `<div class="eyebrow">ROLE HIERARCHY / RH</div><h3>继承权力，也继承约束。</h3><p class="panel-intro">箭头左侧为高级角色，继承右侧角色权限。保存时检查全图与全部受影响用户。</p><form id="hierarchyForm"><div id="hierarchyRows">${draftEdges.map((edge,index) => `<div class="edge-row"><select aria-label="第 ${index + 1} 条继承边的高级角色" data-edge="${index}" data-side="senior_id">${roleOptions(edge.senior_id)}</select><span>→</span><select aria-label="第 ${index + 1} 条继承边的被继承角色" data-edge="${index}" data-side="junior_id">${roleOptions(edge.junior_id)}</select><button type="button" class="remove-edge" data-remove-edge="${index}" aria-label="移除第 ${index + 1} 条继承边">×</button></div>`).join('')}</div><button type="button" class="add-edge" id="addEdge">＋ 添加一条继承关系</button><div class="admin-note">禁止自继承、重复边与循环继承；新增边也不能让财政与审计角色或其权限相遇。</div><button type="submit" class="small-button admin-save" data-testid="save-hierarchy">保存继承关系 →</button></form>`;
  } else if (adminTab === 'audit') {
    $('#adminContent').innerHTML = `<div class="eyebrow">AUDIT REVIEW / READ ONLY</div><h3>全王国最近事件</h3><p class="panel-intro">当前身份可以读取全局审计；这项能力不会授予角色册封、权限矩阵或继承修改能力。</p><p>右侧最近事件终端展示数据库返回的最近40条记录。这里采用手动刷新，不承诺跨会话实时推送。</p><button type="button" class="small-button" id="adminRefreshAudit">手动刷新最近事件</button>`;

  }
}

async function savePolicy(path, body) {
  if (!adminState || !Number.isInteger(adminState.policy_revision)) throw new Error('请先重新载入管理策略快照。');
  adminFeedback('正在提交当前候选策略…');
  try {
    const data = await api(path, {method:'PUT', body:{...body,base_revision:adminState.policy_revision}});
    adminDirty = false;
    pendingAdminAction = null;
    clearViewport();
    await loadState();
    if (canManage()) await openAdmin(adminTab);
    adminFeedback(data.message || '策略已通过审查并保存，已载入最新快照。');
    notify(data.message || '策略已写入数据库。');
  } catch (error) {
    if (state) {
      const stale = error.data?.code === 'STALE_POLICY';
      adminFeedback(stale ? `策略已被其他管理员修改（当前数据库 v${error.data?.policy_revision ?? '新版本'}）。你的候选草稿已保留；请比较后重新载入，系统不会覆盖或自动重放。` : `${error.message} 当前候选未生效，复选框与继承草稿已保留。`, {error:true});
      updateAdminRevision();
      $('#adminFeedback')?.focus({preventScroll:true});
    }
    throw error;
  }
}

async function saveActivation(form) {
  const roleIds = $$('input[name="activeRole"]:checked', form).map(input => Number(input.value));
  const data = await api('/api/session/roles', {method:'POST', body:{role_ids:roleIds}});
  clearViewport();
  await loadState();
  await applyRoute();
  notify(data.message || '会话激活角色已更新。');
}

document.addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button || button.disabled) return;
  if (button.dataset.nav) await busy(button, () => navigate(button.dataset.nav, button));
  else if (button.dataset.page) await busy(button, () => routeTo(`/pages/${encodeURIComponent(button.dataset.page)}`, {trigger:button}));
  else if (button.dataset.action) await busy(button, () => executeAction(button.dataset.action));
  else if (button.hasAttribute('data-close-page') || button.hasAttribute('data-back')) await busy(button, () => routeTo(currentTerritory ? `/territory/${encodeURIComponent(currentTerritory)}` : '/experiment'));
  else if (button.id === 'modelButton' || button.dataset.mobileView === 'model') await routeTo('/model');
  else if (button.id === 'adminButton' || button.dataset.mobileView === 'admin') await busy(button, () => routeTo(`/admin/${adminTab || 'assign'}`, {trigger:button}));
  else if (button.dataset.adminTab) await busy(button, () => routeTo(`/admin/${button.dataset.adminTab}`, {trigger:button}));
  else if (button.dataset.eventFilter) { eventFilter = button.dataset.eventFilter; $$('[data-event-filter]').forEach(item => item.classList.toggle('active', item === button)); renderEvents(); }
  else if (button.id === 'refreshEvents' || button.id === 'adminRefreshAudit') await busy(button, refreshEvents);
  else if (button.id === 'switchAccount') {
    const logout = () => busy(button, async () => {
      const previousUsername = state.user.username;
      await api('/api/logout', {method:'POST', body:{}});
      history.replaceState(null, '', location.pathname + location.search);
      appliedHash = '';
      currentTerritory = null;
      showLogin();
      await bootstrap();
      $('#loginUser').value = previousUsername;
      $('#loginError').textContent = '';
    });
    if (allowDraftExit(logout)) await logout();
  } else if (button.id === 'adminReload') {
    const reload = () => busy(button, () => openAdmin(adminTab));
    if (allowDraftExit(reload)) await reload();
  } else if (button.id === 'adminDiscard') {
    const next = pendingAdminAction;
    adminDirty = false;
    pendingAdminAction = null;
    adminFeedback();
    updateAdminRevision();
    if (next) await next();
  } else if (button.id === 'addEdge') {
    const roles = adminState.roles || [];
    draftEdges.push({senior_id:roles[0]?.id,junior_id:roles[1]?.id});
    markDirty();
    renderAdmin();
  } else if (button.hasAttribute('data-remove-edge')) { draftEdges.splice(Number(button.dataset.removeEdge), 1); markDirty(); renderAdmin(); }
});

document.addEventListener('change', event => {
  const element = event.target;
  if (element.id === 'showLocked' || element.id === 'mobileLocked') {
    $('#showLocked').checked = element.checked;
    const mobile = $('#mobileLocked'); if (mobile) mobile.checked = element.checked;
    renderNavigation();
  } else if (element.id === 'assignUser') {
    const next = Number(element.value);
    if (allowDraftExit(() => { selectedUserId = next; renderAdmin(); })) { selectedUserId = next; renderAdmin(); }
    else element.value = selectedUserId;
  } else if (element.id === 'permissionRole') {
    const next = Number(element.value);
    if (allowDraftExit(() => { selectedRoleId = next; renderAdmin(); })) { selectedRoleId = next; renderAdmin(); }
    else element.value = selectedRoleId;
  } else if (element.hasAttribute('data-edge')) { draftEdges[Number(element.dataset.edge)][element.dataset.side] = Number(element.value); markDirty(); }
  else if (element.closest('#assignForm,#permissionForm')) markDirty();
});

document.addEventListener('keydown', event => {
  const tab = event.target.closest('[data-admin-tab]');
  if (!tab || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
  event.preventDefault();
  const tabs = $$('[data-admin-tab]').filter(button => !button.hidden);
  const index = tabs.indexOf(tab);
  const next = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs.at(-1) : tabs[(index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
  if (next) { next.focus(); next.click(); }
});

document.addEventListener('submit', async event => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  event.preventDefault();
  const button = $('button[type="submit"]', form);
  if (form.id === 'loginForm') {
    $('#loginError').textContent = '';
    await busy(button, async () => {
      try {
        await api('/api/login', {method:'POST',body:{username:$('#loginUser').value,password:$('#loginPassword').value}});
        events = [];
        currentTerritory = null;
        currentView = 'governance';
        await loadState();
        await applyRoute();
        await refreshEvents();
      } catch (error) { $('#loginError').textContent = error.message; throw error; }
    });
  } else if (form.id === 'activationForm' || form.id === 'modelActivationForm') {
    const save = () => busy(button, () => saveActivation(form));
    if (allowDraftExit(save)) await save();
  }
  else if (form.id === 'probeForm') await busy(button, async () => {
    const code = $('#probeSelect').value;
    const [type,slug] = code.split(':');
    if (type === 'menu') await navigate(slug, button);
    else if (type === 'page') await routeTo(`/pages/${encodeURIComponent(slug)}`, {trigger:button});
    else if (type === 'btn') await executeAction(slug);
  });
  else if (form.id === 'assignForm') await busy(button, () => savePolicy(`/api/admin/users/${selectedUserId}/roles`, {role_ids:$$('input[name="assignedRole"]:checked',form).map(input => Number(input.value))}));
  else if (form.id === 'permissionForm') await busy(button, () => savePolicy(`/api/admin/roles/${selectedRoleId}/permissions`, {permission_ids:$$('input[name="permission"]:checked',form).map(input => Number(input.value))}));
  else if (form.id === 'hierarchyForm') await busy(button, () => savePolicy('/api/admin/hierarchy', {edges:draftEdges}));
});

async function init() {
  try {
    const data = await bootstrap();
    if (data.authenticated) { await loadState(); await applyRoute(); await refreshEvents(); }
    else showLogin();
  } catch (error) {
    $('#loginError').textContent = error.message || '连接服务器失败，请确认本地服务已启动。';
  }
}

init();
