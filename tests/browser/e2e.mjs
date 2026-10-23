/**
 * Reproducible browser regressions for the local RBAC teaching application.
 *
 * Start a separate Flask process using create_app({DATABASE: <temporary file>}).
 * NEVER point this script at the user's instance/kingdom.sqlite3: it deliberately
 * changes policy and business data in its disposable fixture, then restores the
 * policy. Run:
 *   BASE_URL=http://127.0.0.1:5053 ALLOW_TEST_MUTATIONS=1 node tests/browser/e2e.mjs
 * Windows PowerShell: set the two environment variables before invoking Node.
 * Optional: EDGE_PATH, PLAYWRIGHT_MODULE, RESULT_PATH.
 */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const baseURL = process.env.BASE_URL || 'http://127.0.0.1:5053';
if (process.env.ALLOW_TEST_MUTATIONS !== '1') throw new Error('Set ALLOW_TEST_MUTATIONS=1 only for an isolated temporary-database server.');
if (!['127.0.0.1','localhost','[::1]'].includes(new URL(baseURL).hostname)) throw new Error('Browser regressions may only target a local isolated test server.');

const results = [];
const browser = await chromium.launch({headless:true,...(process.env.EDGE_PATH ? {executablePath:process.env.EDGE_PATH} : {channel:'msedge'})});
const contexts = [];
const pageErrors = [];
async function context(viewport = {width:1440,height:1000}) {
  const ctx = await browser.newContext({baseURL,viewport});
  contexts.push(ctx);
  ctx.on('page', page => page.on('pageerror', error => pageErrors.push(error.message)));
  return ctx;
}
async function run(name, fn) {
  const start = Date.now();
  try { await fn(); results.push({name,status:'passed',ms:Date.now()-start}); console.log(`PASS ${name}`); }
  catch (error) { results.push({name,status:'failed',message:error.message}); throw error; }
}
async function login(page, username) {
  await page.goto('/');
  await page.getByTestId('login-user').selectOption(username);
  await page.getByTestId('login-password').fill('Kingdom@2026');
  await page.getByTestId('login-submit').click();
  await page.locator('#application').waitFor({state:'visible'});
  await page.waitForFunction(() => location.hash.startsWith('#/'));
}
async function api(ctx, path, {method='GET',body} = {}) {
  const bootstrap = await (await ctx.request.get('/api/bootstrap')).json();
  const response = await ctx.request.fetch(path, {method,headers:{'X-CSRF-Token':bootstrap.csrf_token},...(body === undefined ? {} : {data:body})});
  return {status:response.status(),data:await response.json()};
}
async function successful(ctx, path, options) {
  const result = await api(ctx,path,options);
  assert.equal(result.status,200,JSON.stringify(result.data));
  return result.data;
}
async function route(page, path, target) {
  const previousHash = new URL(page.url()).hash;
  const [kind,slug] = path.split('/').filter(Boolean);
  const endpoint = {territory:`/api/navigation/${slug}`,pages:`/api/pages/${slug}`,admin:'/api/admin/state'}[kind];
  const response = endpoint && previousHash !== `#${path}` ? page.waitForResponse(response => new URL(response.url()).pathname === endpoint && response.request().method() === 'GET') : null;
  await page.evaluate(path => { location.hash = `#${path}`; },path);
  if (response) await response;
  if (target) await page.locator(target).waitFor({state:'visible'});
}
async function permissionUpdate(ctx, roleId, permissionIds) {
  const snapshot = await successful(ctx,'/api/admin/state');
  return successful(ctx,`/api/admin/roles/${roleId}/permissions`,{method:'PUT',body:{permission_ids:permissionIds,base_revision:snapshot.policy_revision}});
}

let royalContext;
let originalAuditorPermissions;
let originalPeasantPermissions;
try {
  const henryContext = await context();
  const henry = await henryContext.newPage();
  await login(henry,'henry');

  await run('Database resource catalogue is scoped to the territory; cross-domain lab is explicit',async () => {
    assert.equal(await henry.locator('#pageCards [data-page]').count(),1);
    assert.equal(await henry.locator('#actionCards [data-action]').count(),1);
    assert.equal(await henry.locator('#pageCards [data-page]').getAttribute('data-page'),'tax_scroll');
    await henry.locator('#crossDomainPanel summary').click();
    assert.equal(await henry.locator('#crossPageCards [data-page]').count(),3);
    assert.equal(await henry.locator('#crossActionCards [data-action]').count(),4);
    assert.equal(await henry.locator('#crossPageCards .domain-tag').count(),3);
    const before = Number((await henry.locator('#metricSwords').innerText()).replaceAll(',',''));
    await henry.getByTestId('action-forge_sword').click();
    await henry.waitForFunction(before => Number(document.querySelector('#metricSwords').textContent.replaceAll(',','')) === before+1,before);
  });

  await run('Hash routes support back, refresh, and protected deep links',async () => {
    await henry.getByTestId('page-tax_scroll').click();
    await henry.getByTestId('page-content').waitFor({state:'visible'});
    assert.match(henry.url(),/#\/pages\/tax_scroll$/);
    await henry.locator('#modelButton').click();
    await henry.locator('#modelView').waitFor({state:'visible'});
    await henry.goBack();
    await henry.getByTestId('page-content').waitFor({state:'visible'});
    await henry.reload();
    await henry.getByTestId('page-content').waitFor({state:'visible'});
    await route(henry,'/pages/defense_map','[data-testid="access-denied"]');
    const denial = await henry.getByTestId('access-denied').innerText();
    assert.match(denial,/page:defense_map/);
    assert.match(denial,/DECISION_DENY/);
    assert.ok(!denial.includes('第七骑兵团'));
    const independent = await henryContext.request.get('/pages/defense_map');
    assert.equal(independent.status(),403);
    assert.ok(!(await independent.text()).includes('第七骑兵团'));
  });

  await run('Expired-session POST refreshes CSRF, does not replay, and permits direct login',async () => {
    await route(henry,'/territory/square','#actionCards [data-action="forge_sword"]');
    const before = Number((await henry.locator('#metricSwords').innerText()).replaceAll(',',''));
    let writes = 0;
    const onRequest = request => { if (request.method() === 'POST' && request.url().endsWith('/api/actions/forge_sword')) writes++; };
    henry.on('request',onRequest);
    await henryContext.clearCookies();
    await henry.getByTestId('action-forge_sword').click();
    await henry.locator('#loginScreen').waitFor({state:'visible'});
    await henry.getByTestId('login-user').selectOption('henry');
    await henry.getByTestId('login-submit').click();
    await henry.locator('#application').waitFor({state:'visible'});
    assert.equal(Number((await henry.locator('#metricSwords').innerText()).replaceAll(',','')),before);
    assert.equal(writes,1,'The original mutation must never be automatically retried.');
    henry.off('request',onRequest);
  });

  const agnesContext = await context();
  const agnes = await agnesContext.newPage();
  await login(agnes,'agnes');
  await run('Independent audit displays persisted inputs, expected/actual totals and difference',async () => {
    await route(agnes,'/territory/treasury','#actionCards [data-action="audit_ledger"]');
    await agnes.getByTestId('action-audit_ledger').click();
    await agnes.getByTestId('audit-report').waitFor({state:'visible'});
    for (const key of ['opening_gold','total_credits','total_debits','expected_gold','actual_gold','difference','ledger_count','through_ledger_id']) {
      assert.equal(await agnes.locator(`[data-report-field="${key}"]`).count(),1,key);
    }
    const expected = Number(await agnes.locator('[data-report-field="expected_gold"]').innerText());
    const actual = Number(await agnes.locator('[data-report-field="actual_gold"]').innerText());
    const difference = Number(await agnes.locator('[data-report-field="difference"]').innerText());
    assert.equal(actual-expected,difference);
  });

  royalContext = await context();
  const royal = await royalContext.newPage();
  await login(royal,'wenceslas');
  let admin = await successful(royalContext,'/api/admin/state');
  originalAuditorPermissions = [...admin.role_permission_map['5']];
  originalPeasantPermissions = [...admin.role_permission_map['1']];

  await run('Optimistic conflict preserves dirty form and requires explicit reload/discard',async () => {
    await route(royal,'/admin/permissions','#permissionForm');
    await royal.locator('#permissionRole').selectOption('1');
    const checkbox = royal.locator('[data-testid="permission-btn:deploy_cavalry"]');
    await checkbox.check();
    await royal.locator('#adminDirty').waitFor({state:'visible'});
    const snapshot = await successful(royalContext,'/api/admin/state');
    const hansRoles = snapshot.user_role_map['3'];
    await successful(royalContext,'/api/admin/users/3/roles',{method:'PUT',body:{role_ids:hansRoles,base_revision:snapshot.policy_revision}});
    await royal.getByTestId('save-permissions').click();
    await royal.waitForFunction(() => document.querySelector('#adminFeedback').textContent.includes('草稿已保留'));
    assert.equal(await checkbox.isChecked(),true);
    assert.match(await royal.locator('#adminFeedback').innerText(),/不会覆盖或自动重放/);
    await royal.locator('#adminReload').click();
    await royal.locator('#adminDiscard').waitFor({state:'visible'});
    const reloadResponse = royal.waitForResponse(response => new URL(response.url()).pathname === '/api/admin/state' && response.request().method() === 'GET');
    await royal.locator('#adminDiscard').click();
    await reloadResponse;
    await royal.waitForFunction(() => {
      const checkbox = document.querySelector('[data-testid="permission-btn:deploy_cavalry"]');
      return checkbox && !checkbox.checked && document.querySelector('#adminDirty').hidden;
    });
    assert.equal(await royal.locator('[data-testid="permission-btn:deploy_cavalry"]').isChecked(),false);
  });

  await run('Assign-only administrator can enter the matching UI but cannot expose policy/audit tabs',async () => {
    const snapshot = await successful(royalContext,'/api/admin/state');
    const assignId = snapshot.permissions.find(permission => permission.code === 'admin:assign').id;
    await permissionUpdate(royalContext,5,[...originalAuditorPermissions,assignId]);
    await agnes.reload();
    await agnes.locator('#adminButton').waitFor({state:'visible'});
    await agnes.locator('#adminButton').click();
    await agnes.locator('#assignForm').waitFor({state:'visible'});
    assert.equal(await agnes.locator('[data-admin-tab="assign"]').isVisible(),true);
    for (const tab of ['permissions','hierarchy','audit']) assert.equal(await agnes.locator(`[data-admin-tab="${tab}"]`).isVisible(),false,tab);
    assert.match(await agnes.locator('.admin-note').innerText(),/部分册封官/);
    await agnes.getByTestId('save-assignment').click();
    await agnes.waitForFunction(() => document.querySelector('#adminFeedback').textContent.includes('已生效'));
    await permissionUpdate(royalContext,5,originalAuditorPermissions);
  });

  await run('Audit-only administrator has an audit view without assignment or policy fields',async () => {
    const snapshot = await successful(royalContext,'/api/admin/state');
    const auditId = snapshot.permissions.find(permission => permission.code === 'admin:audit').id;
    await permissionUpdate(royalContext,5,[...originalAuditorPermissions,auditId]);
    await agnes.goto('/');
    await agnes.locator('#adminButton').waitFor({state:'visible'});
    await agnes.locator('#adminButton').click();
    await agnes.locator('#adminRefreshAudit').waitFor({state:'visible'});
    for (const tab of ['assign','permissions','hierarchy']) assert.equal(await agnes.locator(`[data-admin-tab="${tab}"]`).isVisible(),false,tab);
    const clipped = await successful(agnesContext,'/api/admin/state');
    assert.ok(!Object.hasOwn(clipped,'users'));
    assert.ok(!Object.hasOwn(clipped,'role_permission_map'));
    await permissionUpdate(royalContext,5,originalAuditorPermissions);
  });

  await run('No-menu policy gives an explicit empty state while page/action lab remains usable',async () => {
    const snapshot = await successful(royalContext,'/api/admin/state');
    const squareId = snapshot.permissions.find(permission => permission.code === 'menu:square').id;
    await permissionUpdate(royalContext,1,originalPeasantPermissions.filter(id => id !== squareId));
    await henry.goto('/');
    await henry.locator('#territoryEmpty').waitFor({state:'visible'});
    assert.equal(await henry.locator('#territoryNav [aria-current="page"]').count(),0);
    assert.equal(await henry.locator('#crossDomainPanel').getAttribute('open'),'');
    await henry.getByTestId('cross-page-tax_scroll').click();
    await henry.getByTestId('page-content').waitFor({state:'visible'});
    await permissionUpdate(royalContext,1,originalPeasantPermissions);
  });

  await run('Resource and log text are escaped; 390px layout and tab controls remain accessible',async () => {
    const mobileContext = await context({width:390,height:844});
    const mobile = await mobileContext.newPage();
    await mobile.route('**/api/state', async route => {
      const response = await route.fetch();
      const data = await response.json();
      data.resources.pages[0].title = '<img src=x onerror="window.__xss=1">';
      data.resources.actions[0].description = '<svg onload="window.__xss=2">';
      await route.fulfill({response,json:data});
    });
    await mobile.route('**/api/events', async route => {
      const response = await route.fetch();
      const data = await response.json();
      if (data.events?.length) {
        data.events[0].actor_name = '<img src=x onerror="window.__xss=3">';
        data.events[0].logs = [{step:'XSS_REGRESSION',detail:'<img src=x onerror="window.__xss=4">'}];
      }
      await route.fulfill({response,json:data});
    });
    await login(mobile,'wenceslas');
    assert.equal(await mobile.locator('#pageCards img,#actionCards svg').count(),0);
    assert.match(await mobile.locator('#pageCards').innerText(),/<img src=x/);
    assert.equal(await mobile.evaluate(() => window.__xss),undefined);
    await mobile.locator('#refreshEvents').click();
    await mobile.waitForFunction(() => document.querySelector('#eventFeed').textContent.includes('XSS_REGRESSION'));
    assert.equal(await mobile.locator('#eventFeed img').count(),0);
    const measurements = await mobile.evaluate(() => ({width:innerWidth,body:document.body.scrollWidth,nav:document.querySelector('#territoryNav').scrollWidth,navClient:document.querySelector('#territoryNav').clientWidth,button:document.querySelector('#actionCards button').getBoundingClientRect().height,font:parseFloat(getComputedStyle(document.querySelector('#actionCards button')).fontSize)}));
    assert.ok(measurements.body <= measurements.width,JSON.stringify(measurements));
    assert.ok(measurements.nav <= measurements.navClient+1,JSON.stringify(measurements));
    assert.ok(measurements.button >= 40,JSON.stringify(measurements));
    assert.ok(measurements.font >= 12,JSON.stringify(measurements));
    await route(royal,'/admin/assign','#assignForm');
    const tab = royal.locator('[data-admin-tab="assign"]');
    await tab.focus();
    await royal.keyboard.press('ArrowRight');
    await royal.locator('#permissionForm').waitFor({state:'visible'});
    assert.equal(await royal.locator('[data-admin-tab="permissions"]').getAttribute('aria-selected'),'true');
    assert.equal(await royal.locator('#eventFeed').getAttribute('aria-live'),'polite');
  });
  assert.deepEqual(pageErrors,[],'Browser page errors');
} finally {
  // Restore fixture policy even if an assertion fails; the server is still disposable.
  if (royalContext && originalAuditorPermissions) await permissionUpdate(royalContext,5,originalAuditorPermissions).catch(error => console.error('Cleanup auditor:',error.message));
  if (royalContext && originalPeasantPermissions) await permissionUpdate(royalContext,1,originalPeasantPermissions).catch(error => console.error('Cleanup peasant:',error.message));
  if (process.env.RESULT_PATH) await writeFile(process.env.RESULT_PATH,JSON.stringify({baseURL,results,pageErrors},null,2));
  for (const ctx of contexts) await ctx.close();
  await browser.close();
}
