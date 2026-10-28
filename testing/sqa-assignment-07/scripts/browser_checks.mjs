/** Browser scenarios and recorded, iterative exploration of the disposable SUT. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve, dirname} from 'node:path';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const baseURL = 'http://127.0.0.1:5089';
const phase = process.argv[2] || 'scenarios';
const startedAt = new Date().toISOString();
const records = [], errors = [], contexts = [];
const browser = await chromium.launch({headless:true, channel:'msedge'});
const browserVersion = browser.version();
await mkdir(resolve(root,'figures'), {recursive:true});

async function login(username, viewport={width:1440,height:1100}) {
  const ctx = await browser.newContext({baseURL,viewport,locale:'zh-CN',timezoneId:'Asia/Shanghai'});
  contexts.push(ctx);
  const page = await ctx.newPage();
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/');
  await page.getByTestId('login-user').selectOption(username);
  await page.getByTestId('login-password').fill('Kingdom@2026');
  await page.getByTestId('login-submit').click();
  await page.locator('#application').waitFor({state:'visible'});
  await page.waitForFunction(() => location.hash.startsWith('#/'));
  return {ctx,page};
}
async function read(ctx, path='/api/state') {
  const response = await ctx.request.get(path);
  assert.equal(response.status(),200);
  return response.json();
}
async function clickResponse(page, locator, path, method='POST') {
  const wait = page.waitForResponse(r => new URL(r.url()).pathname === path && r.request().method() === method);
  await locator.click();
  const response = await wait;
  const data = await response.json();
  return {status:response.status(),data};
}
async function openAdmin(page, tab='assign') {
  await page.evaluate(tab => {location.hash=`#/admin/${tab}`;},tab);
  await page.locator(tab==='assign'?'#assignForm':'#permissionForm').waitFor({state:'visible'});
}
async function assignment(page, roles) {
  await page.getByTestId('assign-user').selectOption('1');
  for (const code of ['PEASANT','CAPTAIN','TREASURER','KING','AUDITOR']) {
    await page.getByTestId(`assign-role-${code}`).setChecked(roles.includes(code));
  }
  const result = await clickResponse(page,page.getByTestId('save-assignment'),'/api/admin/users/1/roles','PUT');
  assert.equal(result.status,200,JSON.stringify(result.data));
  await page.waitForFunction(() => document.querySelector('#adminDirty').hidden);
  return result;
}
async function record(id, purpose, action, nextHypothesis) {
  const began = new Date().toISOString(), start=performance.now();
  try {
    const observed=await action();
    records.push({id,purpose,started_at:began,ended_at:new Date().toISOString(),elapsed_ms:Math.round(performance.now()-start),status:'passed',observed,next_hypothesis:nextHypothesis});
    console.log(`PASS ${id} ${JSON.stringify(observed)}`);
    return observed;
  } catch(e) {
    records.push({id,purpose,started_at:began,ended_at:new Date().toISOString(),status:'failed',message:e.message});
    throw e;
  }
}

async function scenarios() {
  const royal=await login('wenceslas'), henry=await login('henry');
  const initialBusiness=(await read(royal.ctx)).business;
  assert.equal(initialBusiness.gold,3200,'Start a fresh serve_browser.py process before running the scenario suite.');
  assert.equal(initialBusiness.cavalry_deployments,0,'The browser fixture must start with the original business state.');
  await record('S1','国王授予卫队角色、亨利激活并停留军事卷轴，撤权后通过旧页面按钮真实提交。',async()=>{
    await openAdmin(royal.page);
    await assignment(royal.page,['PEASANT','CAPTAIN']);
    await henry.page.reload();
    await henry.page.locator('.activation-details summary').click();
    await henry.page.locator('#activationForm input[value="2"]').check();
    const activation=await clickResponse(henry.page,henry.page.getByTestId('save-active-roles'),'/api/session/roles');
    assert.equal(activation.status,200);
    await henry.page.getByTestId('nav-armory').waitFor({state:'visible'});
    await henry.page.getByTestId('nav-armory').click();
    await henry.page.getByTestId('page-defense_map').click();
    await henry.page.getByTestId('page-content').waitFor({state:'visible'});
    assert.match(await henry.page.getByTestId('page-content').innerText(),/第七骑兵团/);
    const before=(await read(henry.ctx)).business.cavalry_deployments;
    await assignment(royal.page,['PEASANT']);
    assert.equal(await henry.page.getByTestId('action-deploy_cavalry').isVisible(),true);
    const denied=await clickResponse(henry.page,henry.page.getByTestId('action-deploy_cavalry'),'/api/actions/deploy_cavalry');
    assert.equal(denied.status,403);
    assert.equal(denied.data.code,'PERMISSION_DENIED');
    await henry.page.getByTestId('access-denied').waitFor({state:'visible'});
    await henry.page.locator('#guardAlert').waitFor({state:'visible'});
    const after=(await read(henry.ctx)).business.cavalry_deployments;
    assert.equal(after,before);
    assert.ok(!(await henry.page.locator('#pageViewport').innerText()).includes('第七骑兵团'));
    await henry.page.evaluate(()=>scrollTo(0,0));
    await henry.page.screenshot({path:resolve(root,'figures','scenario-revocation.png'),fullPage:false});
    return {separate_browser_contexts:true,assigned:[1,2],activated:[1,2],revoked_to:[1],response_status:denied.status,response_code:denied.data.code,cavalry_before:before,cavalry_after:after,event_id:denied.data.event.id,visible_error:await henry.page.locator('#guardAlertMessage').innerText(),old_protected_content_removed:true};
  });
  await record('S2','两个管理员各持一个快照，第二个保存旧版本候选应409且不覆盖已提交策略。',async()=>{
    const other=await login('wenceslas');
    await openAdmin(royal.page,'permissions');
    await royal.page.locator('#permissionRole').selectOption('1');
    await openAdmin(other.page,'permissions');
    await other.page.locator('#permissionRole').selectOption('1');
    const initial=await read(royal.ctx,'/api/admin/state');
    await royal.page.getByTestId('permission-btn:forge_sword').uncheck();
    const first=await clickResponse(royal.page,royal.page.getByTestId('save-permissions'),'/api/admin/roles/1/permissions','PUT');
    assert.equal(first.status,200);
    await other.page.getByTestId('permission-btn:deploy_cavalry').check();
    const second=await clickResponse(other.page,other.page.getByTestId('save-permissions'),'/api/admin/roles/1/permissions','PUT');
    assert.equal(second.status,409);
    assert.equal(second.data.code,'STALE_POLICY');
    await other.page.waitForFunction(()=>document.querySelector('#adminFeedback').textContent.includes('草稿已保留'));
    assert.equal(await other.page.getByTestId('permission-btn:deploy_cavalry').isChecked(),true);
    const saved=await read(royal.ctx,'/api/admin/state');
    assert.deepEqual(saved.role_permission_map['1'],[1,4]);
    const observation={snapshot_revision:initial.policy_revision,committed_revision:saved.policy_revision,second_status:second.status,second_code:second.data.code,saved_permissions:saved.role_permission_map['1'],candidate_retained:true,feedback:await other.page.locator('#adminFeedback').innerText()};
    // Restore only this disposable fixture through the same real administration form.
    await royal.page.getByTestId('permission-btn:forge_sword').check();
    const restored=await clickResponse(royal.page,royal.page.getByTestId('save-permissions'),'/api/admin/roles/1/permissions','PUT');
    assert.equal(restored.status,200);
    return observation;
  });
  await record('S3','铸币司库拨付100，审计官在独立会话核对流水与余额。',async()=>{
    const hans=await login('hans'), agnes=await login('agnes');
    await hans.page.getByTestId('nav-treasury').click();
    const payment=await clickResponse(hans.page,hans.page.getByTestId('action-open_vault'),'/api/actions/open_vault');
    assert.equal(payment.status,200);
    assert.equal(payment.data.business.gold,3100);
    await agnes.page.getByTestId('nav-treasury').click();
    const audit=await clickResponse(agnes.page,agnes.page.getByTestId('action-audit_ledger'),'/api/actions/audit_ledger');
    assert.equal(audit.status,200);
    await agnes.page.getByTestId('audit-report').waitFor({state:'visible'});
    const report={};
    for(const key of ['opening_gold','total_debits','expected_gold','actual_gold','difference','ledger_count']) report[key]=Number(await agnes.page.locator(`[data-report-field="${key}"]`).innerText());
    assert.equal(report.expected_gold,3100); assert.equal(report.actual_gold,3100); assert.equal(report.difference,0);
    await agnes.page.evaluate(()=>scrollTo(0,0));
    await agnes.page.getByTestId('audit-report').screenshot({path:resolve(root,'figures','scenario-audit.png')});
    return {...report,payment_event:payment.data.event.id,audit_event:audit.data.event.id};
  });
}

async function explore1() {
  const henry=await login('henry');
  let writes=0;
  henry.page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/actions/forge_sword') writes++;});
  await record('E1','观察快速双击是否造成同一轮提交重复生产。',async()=>{
    const before=(await read(henry.ctx)).business.swords;
    const button=henry.page.getByTestId('action-forge_sword');
    await button.scrollIntoViewIfNeeded();
    const box=await button.boundingBox();
    await henry.page.mouse.click(box.x+box.width/2,box.y+box.height/2,{clickCount:2,delay:1});
    await henry.page.waitForFunction(value=>Number(document.querySelector('#metricSwords').textContent.replaceAll(',',''))>value,before);
    await henry.page.waitForFunction(()=>!document.querySelector('[data-testid="action-forge_sword"]').disabled);
    const after=(await read(henry.ctx)).business.swords;
    assert.equal(writes,1); assert.equal(after,before+1);
    return {post_requests:writes,swords_before:before,swords_after:after,mechanism_observed:'first click disables busy button; second immediate click sends no POST'};
  },'即时双击受到busy状态保护，继续检查浏览器历史与刷新会不会重放已完成操作。');
  await record('E2','完成生产后打开卷轴、切换模型、后退前进并刷新，检查业务写请求与库存。',async()=>{
    const before=(await read(henry.ctx)).business.swords, postsBefore=writes;
    await henry.page.getByTestId('page-tax_scroll').click();
    await henry.page.getByTestId('page-content').waitFor({state:'visible'});
    await henry.page.locator('#modelButton').click();
    await henry.page.locator('#modelView').waitFor({state:'visible'});
    await henry.page.goBack();
    await henry.page.getByTestId('page-content').waitFor({state:'visible'});
    await henry.page.goForward();
    await henry.page.locator('#modelView').waitFor({state:'visible'});
    await henry.page.reload();
    await henry.page.locator('#modelView').waitFor({state:'visible'});
    const after=(await read(henry.ctx)).business.swords;
    assert.equal(after,before); assert.equal(writes,postsBefore);
    return {swords_before:before,swords_after:after,additional_write_requests:writes-postsBefore,final_hash:new URL(henry.page.url()).hash};
  },'导航只读取状态且未重放，下一轮转向客户端身份伪造和真实服务器会话过期边界。');
}

async function screenshots() {
  const agnes=await login('agnes');
  await agnes.page.getByTestId('nav-treasury').click();
  await agnes.page.getByTestId('page-royal_ledger').click();
  await agnes.page.getByTestId('audit-report').waitFor({state:'visible'});
  await agnes.page.getByTestId('audit-report').screenshot({path:resolve(root,'figures','scenario-audit.png')});
  console.log('Captured persisted audit report without a new payment or audit.');
}

async function explore2() {
  const henry=await login('henry');
  await record('E3','根据首轮未重放观察，转向篡改本地身份及角色，检查服务端是否仍拒绝财政操作。',async()=>{
    const before=(await read(henry.ctx)).business;
    await henry.page.evaluate(()=>{
      localStorage.setItem('user_id','4');
      localStorage.setItem('username','wenceslas');
      localStorage.setItem('role','KING');
      localStorage.setItem('active_role_ids','[4]');
      sessionStorage.setItem('user_id','4');
      sessionStorage.setItem('role','KING');
    });
    await henry.page.reload();
    await henry.page.locator('#application').waitFor({state:'visible'});
    const current=await read(henry.ctx);
    assert.equal(current.user.username,'henry');
    assert.deepEqual(current.active_role_ids,[1]);
    await henry.page.locator('.probe-panel summary').click();
    await henry.page.locator('#probeSelect').selectOption('btn:open_vault');
    const result=await clickResponse(henry.page,henry.page.getByTestId('probe-submit'),'/api/actions/open_vault');
    assert.equal(result.status,403); assert.equal(result.data.code,'PERMISSION_DENIED');
    await henry.page.locator('#guardAlert').waitFor({state:'visible'});
    const after=(await read(henry.ctx)).business;
    assert.deepEqual(after,before);
    return {forged_local_storage:{user_id:4,username:'wenceslas',role:'KING',active_role_ids:[4]},server_user:current.user.username,server_active_roles:current.active_role_ids,status:result.status,code:result.data.code,business_unchanged:true,visible_message:await henry.page.locator('#guardAlertMessage').innerText()};
  },'客户端身份字段未影响授权，继续让已登录会话在服务器真实过期，检查旧表单提交与恢复。');
  await record('E4','保持已登录旧页面，通过夹具使auth_session到期，点击生产按钮后重新登录并检查不重放。',async()=>{
    await henry.page.reload();
    await henry.page.getByTestId('action-forge_sword').waitFor({state:'visible'});
    const before=(await read(henry.ctx)).business.swords;
    let posts=0;
    const watch=r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/actions/forge_sword')posts++;};
    henry.page.on('request',watch);
    const fixture=await henry.ctx.request.post('/__browser__/expire-current-session',{headers:{'X-Browser-Fixture':'1'}});
    assert.equal(fixture.status(),200); assert.equal((await fixture.json()).ok,true);
    const result=await clickResponse(henry.page,henry.page.getByTestId('action-forge_sword'),'/api/actions/forge_sword');
    assert.equal(result.status,401); assert.equal(result.data.code,'AUTHENTICATION_REQUIRED');
    await henry.page.locator('#loginScreen').waitFor({state:'visible'});
    const message=await henry.page.locator('#notification').innerText();
    assert.match(message,/会话已过期/);
    await henry.page.getByTestId('login-user').selectOption('henry');
    await henry.page.getByTestId('login-submit').click();
    await henry.page.locator('#application').waitFor({state:'visible'});
    const after=(await read(henry.ctx)).business.swords;
    assert.equal(after,before); assert.equal(posts,1);
    henry.page.off('request',watch);
    return {fixture_setup:'expires_at set to current time minus one in the disposable server session row',status:result.status,code:result.data.code,visible_message:message,forge_post_count:posts,swords_before:before,swords_after:after,explicit_login_succeeded:true};
  },'会话恢复未自动重发业务请求，最后检查窄屏下现有拒绝反馈与管理表单是否可见、可操作。');
  await record('E5','以390×844视口检查正常页面、拒绝详情及国王授权表单的横向溢出和按钮尺寸。',async()=>{
    const mobile=await login('henry',{width:390,height:844});
    const measurements=[];
    const measure=async(label)=>{
      const data=await mobile.page.evaluate(()=>({viewport:innerWidth,document_width:document.documentElement.scrollWidth,body_width:document.body.scrollWidth}));
      assert.ok(data.document_width<=data.viewport,`${label} ${JSON.stringify(data)}`);
      measurements.push({view:label,...data});
    };
    await measure('henry home');
    await mobile.page.locator('.probe-panel summary').click();
    await mobile.page.locator('#probeSelect').selectOption('btn:open_vault');
    const denied=await clickResponse(mobile.page,mobile.page.getByTestId('probe-submit'),'/api/actions/open_vault');
    assert.equal(denied.status,403);
    await mobile.page.getByTestId('access-denied').waitFor({state:'visible'});
    await measure('permission denial and trace');
    const errorVisible=await mobile.page.locator('#guardAlert').isVisible();
    assert.equal(errorVisible,true);
    const royal=await login('wenceslas',{width:390,height:844});
    await openAdmin(royal.page,'permissions');
    const admin=await royal.page.evaluate(()=>({viewport:innerWidth,document_width:document.documentElement.scrollWidth,save_button_height:document.querySelector('[data-testid="save-permissions"]').getBoundingClientRect().height}));
    assert.ok(admin.document_width<=admin.viewport); assert.ok(admin.save_button_height>=40);
    measurements.push({view:'permission form',...admin});
    return {viewport:'390x844',measurements,error_message_visible:errorVisible};
  },'本轮检查未发现新的失效路径，记录结果并结束本次短时探索。');
}

try {
  if(phase==='scenarios') await scenarios();
  else if(phase==='explore1') await explore1();
  else if(phase==='explore2') await explore2();
  else if(phase==='screenshots') await screenshots();
  else throw new Error(`Unknown phase ${phase}`);
  assert.deepEqual(errors,[]);
} finally {
  const filename=phase==='scenarios'?'browser-results.json':phase==='screenshots'?'browser-screenshots.json':phase==='explore2'?'exploration-round2.json':'exploration-round1.json';
  const result={kind:phase==='scenarios'?'browser scenario testing':'browser-assisted guided exploration',base_url:baseURL,browser:'Microsoft Edge headless',browser_version:browserVersion,started_at:startedAt,ended_at:new Date().toISOString(),records,page_errors:errors};
  await writeFile(resolve(root,'evidence',filename),JSON.stringify(result,null,2));
  if(phase==='explore2') {
    const first=JSON.parse(await readFile(resolve(root,'evidence','exploration-round1.json'),'utf8'));
    const combined={...result,started_at:first.started_at,rounds:2,records:[...first.records,...records],page_errors:[...first.page_errors,...errors],harness_adjustment:'Initial pointer-coordinate test timed out before the target was scrolled into the viewport. It is retained separately in exploration-harness-initial.json and excluded from product defect counts.',testing_type:'browser-assisted guided exploration, followed by automated replay; no participant user study data'};
    combined.wall_duration_seconds=Math.round((Date.parse(combined.ended_at)-Date.parse(combined.started_at))/1000);
    combined.active_step_seconds=Math.round(combined.records.reduce((sum,r)=>sum+(r.elapsed_ms||0),0)/1000);
    await writeFile(resolve(root,'evidence','exploration.json'),JSON.stringify(combined,null,2));
  }
  for(const ctx of contexts) await ctx.close();
  await browser.close();
}
