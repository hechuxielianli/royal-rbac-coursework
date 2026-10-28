/** Actual browser feedback against the disposable serve.py database. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const baseURL = process.env.BASE_URL || 'http://127.0.0.1:5053';
if (process.env.ALLOW_TEST_MUTATIONS !== '1' || !['127.0.0.1','localhost','[::1]'].includes(new URL(baseURL).hostname)) {
  throw new Error('Use only the local disposable serve.py server with ALLOW_TEST_MUTATIONS=1.');
}
const output = resolve(process.env.THEME_OUTPUT || 'dist/kuttenberg-review');
await mkdir(output,{recursive:true});
const browser = await chromium.launch({headless:true,...(process.env.EDGE_PATH ? {executablePath:process.env.EDGE_PATH} : {channel:'msedge'})});
const contexts = [], results = [], errors = [], evidence = {};
async function pageFor(username,options={}) {
  const ctx = await browser.newContext({baseURL,viewport:{width:1440,height:1000},...options});
  contexts.push(ctx);
  await ctx.addInitScript(() => {
    window.__coinAnimations=[];
    document.addEventListener('animationstart',e => {
      if (/treasury-flash|coin-count-settle/.test(e.animationName)) window.__coinAnimations.push(e.animationName);
    });
  });
  const page = await ctx.newPage();
  page.on('pageerror',e => errors.push(e.message));
  await page.goto('/');
  await page.getByTestId('login-user').selectOption(username);
  await page.getByTestId('login-submit').click();
  await page.locator('#application').waitFor({state:'visible'});
  await page.waitForFunction(() => location.hash.startsWith('#/'));
  return page;
}
async function run(name,fn) {
  try { await fn(); results.push({name,status:'passed'}); console.log(`PASS ${name}`); }
  catch(e) { results.push({name,status:'failed',message:e.message}); throw e; }
}
async function noOverflow(page) {
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Page overflows horizontally');
}
async function pay(page) {
  await page.getByTestId('nav-treasury').click();
  const before = Number((await page.locator('#metricGold').innerText()).replaceAll(',',''));
  const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname==='/api/actions/open_vault' && r.request().method()==='POST');
  await page.getByTestId('action-open_vault').click();
  const response = await responsePromise;
  assert.equal(response.status(),200);
  const data = await response.json();
  await page.waitForFunction(balance => document.querySelector('#metricGold').textContent===balance.toLocaleString('zh-CN'),before-100);
  assert.equal(data.business.gold,before-100);
  assert.equal(await page.locator('#treasuryDelta').textContent(),'−100 G');
  return {before,after:data.business.gold,event:data.event.id};
}
try {
  const hans = await pageFor('hans');
  await run('Real 100-Groschen debit updates the counter and drives the coin feedback',async () => {
    assert.equal(await hans.locator('#guardAlert').isHidden(),true);
    assert.equal(await hans.locator('#treasuryDelta').textContent(),'');
    evidence.debit=await pay(hans);
    await hans.waitForFunction(() => window.__coinAnimations.includes('coin-count-settle'));
    evidence.coinAnimations=await hans.evaluate(() => window.__coinAnimations);
    await hans.screenshot({path:resolve(output,'13-groschen-debit.png'),fullPage:false});
    const persisted=await (await hans.request.get('/api/state')).json();
    assert.equal(persisted.business.gold,evidence.debit.after);
    await hans.locator('#switchAccount').click();
    await hans.locator('#loginScreen').waitFor({state:'visible'});
    assert.equal(await hans.locator('#treasuryDelta').textContent(),'');
    assert.equal(await hans.locator('.currency-updated').count(),0);
  });
  const henry=await pageFor('henry');
  await run('Only real denied events show the guard warning; close is keyboard accessible',async () => {
    const before=(await (await henry.request.get('/api/state')).json()).business;
    await henry.locator('.probe-panel summary').click();
    await henry.locator('#probeSelect').selectOption('btn:open_vault');
    const promise=henry.waitForResponse(r=>new URL(r.url()).pathname==='/api/actions/open_vault'&&r.request().method()==='POST');
    await henry.getByTestId('probe-submit').click();
    const denied=await promise;
    assert.equal(denied.status(),403);
    const data=await denied.json();
    await henry.locator('#guardAlert').waitFor({state:'visible'});
    assert.equal(await henry.locator('#guardAlert').getAttribute('data-event-id'),data.event.id);
    assert.match(await henry.locator('#guardAlertMessage').textContent(),/拨付运银经费/);
    assert.deepEqual((await (await henry.request.get('/api/state')).json()).business,before);
    assert.ok(!(await henry.content()).includes('第七骑兵团'));
    await henry.evaluate(()=>scrollTo(0,0));
    await henry.screenshot({path:resolve(output,'14-guard-warning.png'),fullPage:false});
    await henry.locator('#closeGuardAlert').focus();
    await henry.keyboard.press('Enter');
    assert.equal(await henry.locator('#guardAlert').isHidden(),true);
  });
  const royal=await pageFor('wenceslas');
  await run('SSD anti-smuggling decree reflects the rejected event and preserves the draft',async()=>{
    await royal.locator('#adminButton').click();
    await royal.getByTestId('assign-user').selectOption('1');
    await royal.getByTestId('assign-role-TREASURER').check();
    await royal.getByTestId('assign-role-AUDITOR').check();
    const promise=royal.waitForResponse(r=>new URL(r.url()).pathname==='/api/admin/users/1/roles'&&r.request().method()==='PUT');
    await royal.getByTestId('save-assignment').click();
    const response=await promise;
    assert.equal(response.status(),409);
    assert.equal((await response.json()).code,'SSD_CONFLICT');
    await royal.locator('#guardAlert').waitFor({state:'visible'});
    assert.match(await royal.locator('#guardAlertTitle').textContent(),/皇家反走私法令/);
    assert.equal(await royal.getByTestId('assign-role-AUDITOR').isChecked(),true);
    const persisted=await (await royal.request.get('/api/admin/state')).json();
    assert.deepEqual(persisted.user_role_map['1'],[1]);
    await royal.screenshot({path:resolve(output,'15-anti-smuggling-ssd.png'),fullPage:true});
  });
  await run('390px manuscript initials and reduced-motion coin feedback remain usable',async()=>{
    const reduced=await pageFor('hans',{viewport:{width:390,height:844},reducedMotion:'reduce'});
    await noOverflow(reduced);
    await pay(reduced);
    assert.deepEqual(await reduced.evaluate(()=>window.__coinAnimations),[]);
    assert.equal(await reduced.locator('#metricGold').evaluate(el=>getComputedStyle(el).animationName),'none');
    await reduced.goto('/pages/royal_ledger');
    const initial=await reduced.locator('.archive-content').evaluate(el=>({
      font:getComputedStyle(el,'::first-letter').fontSize,
      float:getComputedStyle(el,'::first-letter').float,
      color:getComputedStyle(el,'::first-letter').backgroundImage,
    }));
    assert.equal(initial.float,'left');
    assert.ok(parseFloat(initial.font)>=45);
    assert.match(initial.color,/gradient/);
    evidence.dropCap=initial;
    await noOverflow(reduced);
    await reduced.screenshot({path:resolve(output,'16-mobile-illuminated-ledger.png'),fullPage:true});
    const rejected=await contexts[1].newPage();
    assert.equal((await rejected.goto('/pages/defense_map')).status(),403);
    assert.notEqual(await rejected.locator('.archive-content').evaluate(el=>getComputedStyle(el,'::first-letter').float),'left');
    assert.ok(!(await rejected.content()).includes('第七骑兵团'));
  });
  assert.deepEqual(errors,[]);
} finally {
  await writeFile(resolve(output,'theme-results.json'),JSON.stringify({results,evidence,pageErrors:errors},null,2));
  for(const ctx of contexts) await ctx.close();
  await browser.close();
}
