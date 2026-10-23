/** Dark Parchment visual/interaction checks; use only the disposable serve.py server. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const baseURL = process.env.BASE_URL || 'http://127.0.0.1:5053';
if (process.env.ALLOW_TEST_MUTATIONS !== '1' || !['127.0.0.1','localhost','[::1]'].includes(new URL(baseURL).hostname)) {
  throw new Error('Use ALLOW_TEST_MUTATIONS=1 with the local disposable browser server.');
}
const output = resolve(process.env.VISUAL_OUTPUT || 'dist/ui-review');
await mkdir(output, {recursive:true});
const browser = await chromium.launch({headless:true,...(process.env.EDGE_PATH ? {executablePath:process.env.EDGE_PATH} : {channel:'msedge'})});
const results = [];
const contexts = [];
const errors = [];
const cspErrors = [];
const evidence = {};
async function run(name, fn) {
  try {
    await fn();
    results.push({name,status:'passed'});
    console.log(`PASS ${name}`);
  } catch (error) {
    results.push({name,status:'failed',message:error.message});
    throw error;
  }
}
async function context(options = {}) {
  const ctx = await browser.newContext({baseURL,viewport:{width:1440,height:1000},...options});
  contexts.push(ctx);
  await ctx.addInitScript(() => {
    window.__intercepts = [];
    document.addEventListener('animationstart', event => {
      if (event.animationName === 'intercept-shake') window.__intercepts.push({
        source:event.target.dataset.testid || event.target.id,
        duration:getComputedStyle(event.target).animationDuration,
        keyframes:event.target.getAnimations().find(animation => animation.animationName === 'intercept-shake')?.effect.getKeyframes().map(frame => frame.transform),
      });
    });
  });
  ctx.on('page', page => {
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (/violates.*Content Security Policy|Refused to (load|apply)/i.test(message.text())) cspErrors.push(message.text()); });
  });
  return ctx;
}
async function login(page, username) {
  await page.goto('/', {waitUntil:'domcontentloaded'});
  await page.getByTestId('login-user').selectOption(username);
  await page.getByTestId('login-password').fill('Kingdom@2026');
  await page.getByTestId('login-submit').click();
  await page.locator('#application').waitFor({state:'visible'});
  await page.waitForFunction(() => location.hash.startsWith('#/'));
}
async function screenshot(page, name, fullPage = true) {
  await page.screenshot({path:resolve(output,`${name}.png`),fullPage,animations:'disabled'});
}
async function noOverflow(page) {
  const size = await page.evaluate(() => ({viewport:innerWidth,body:document.body.scrollWidth}));
  assert.ok(size.body <= size.viewport,JSON.stringify(size));
}
async function animation(page, source, previous = 0) {
  await page.waitForFunction(({source,previous}) => window.__intercepts.filter(item => item.source === source).length > previous,{source,previous});
  const last = await page.evaluate(source => window.__intercepts.filter(item => item.source === source).at(-1),source);
  assert.equal(last.duration,'0.38s');
  assert.ok(last.keyframes.some(value => value && value !== 'none'),'A horizontal transform must occur');
  return last;
}

try {
  const royalCtx = await context();
  const royal = await royalCtx.newPage();
  await royal.goto('/',{waitUntil:'domcontentloaded'});
  await royal.locator('#loginScreen').waitFor({state:'visible'});
  await screenshot(royal,'01-login');
  await login(royal,'wenceslas');
  await run('Desktop command deck, font stacks, crest and sticky event terminal',async () => {
    evidence.desktop = await royal.evaluate(() => {
      const box = selector => { const r=document.querySelector(selector).getBoundingClientRect();return {left:r.left,right:r.right,top:r.top}; };
      return {sidebar:box('.sidebar'),main:box('#mainContent'),terminal:box('.event-panel'),
        sticky:getComputedStyle(document.querySelector('.event-panel')).position,
        background:getComputedStyle(document.body).backgroundImage,
        bodyFont:getComputedStyle(document.body).fontFamily,
        titleFont:getComputedStyle(document.querySelector('.eyebrow')).fontFamily,
        monoFont:getComputedStyle(document.querySelector('.event-log code')).fontFamily,
        crestFilter:getComputedStyle(document.querySelector('.brand img')).filter};
    });
    assert.ok(evidence.desktop.sidebar.right <= evidence.desktop.main.left);
    assert.ok(evidence.desktop.main.right <= evidence.desktop.terminal.left);
    assert.equal(evidence.desktop.sticky,'sticky');
    assert.match(evidence.desktop.background,/gradient/);
    assert.match(evidence.desktop.bodyFont,/Noto Serif SC/);
    assert.match(evidence.desktop.titleFont,/Cinzel/);
    assert.match(evidence.desktop.monoFont,/JetBrains Mono/);
    assert.match(evidence.desktop.crestFilter,/drop-shadow/);
    const before = await royal.locator('.brand img').evaluate(el => getComputedStyle(el).transform);
    await royal.locator('.brand').hover();
    await royal.waitForFunction(before => getComputedStyle(document.querySelector('.brand img')).transform !== before,before);
    await royal.mouse.move(700,50);
    await royal.locator('#crossDomainPanel summary').click();
    await royal.evaluate(() => scrollTo(0,600));
    evidence.sticky = await royal.locator('.event-panel').evaluate(el => ({top:el.getBoundingClientRect().top,height:el.getBoundingClientRect().height,viewport:innerHeight}));
    assert.ok(evidence.sticky.top >= 0 && evidence.sticky.top < 70,JSON.stringify(evidence.sticky));
    assert.ok(evidence.sticky.top + evidence.sticky.height <= evidence.sticky.viewport + 1);
    const pageScroll = await royal.evaluate(() => scrollY);
    await royal.locator('#eventFeed').evaluate(el => { el.scrollTop=el.scrollHeight; });
    assert.equal(await royal.evaluate(() => scrollY),pageScroll);
    await royal.setViewportSize({width:1440,height:600});
    const compact = await royal.locator('.event-panel').evaluate(el => ({top:el.getBoundingClientRect().top,bottom:el.getBoundingClientRect().bottom,viewport:innerHeight}));
    assert.ok(compact.top >= 0 && compact.bottom <= compact.viewport,JSON.stringify(compact));
    evidence.compactDesktop=compact;
    await royal.setViewportSize({width:1440,height:1000});
    await royal.locator('#eventFeed').evaluate(el => { el.scrollTop=0; });
    await royal.evaluate(() => scrollTo(0,0));
    await screenshot(royal,'02-command-deck',false);
    evidence.fontFaces = await royal.evaluate(async () => {
      await Promise.race([document.fonts.ready,new Promise(resolve => setTimeout(resolve,8000))]);
      return [...document.fonts].filter(face => face.status==='loaded').map(face => face.family).filter((name,index,all) => all.indexOf(name)===index);
    });
  });

  const henryCtx = await context();
  const henry = await henryCtx.newPage();
  await login(henry,'henry');
  await run('Real page/menu/action-probe denials shake the originating control for 0.38s',async () => {
    await henry.locator('#crossDomainPanel summary').click();
    await henry.getByTestId('cross-page-defense_map').click();
    await henry.getByTestId('access-denied').waitFor({state:'visible'});
    evidence.pageShake = await animation(henry,'cross-page-defense_map');
    await henry.getByTestId('cross-page-defense_map').click();
    await animation(henry,'cross-page-defense_map',1);
    await henry.locator('.switch-label').click();
    await henry.getByTestId('nav-armory').click();
    evidence.menuShake = await animation(henry,'nav-armory');
    await henry.locator('.probe-panel summary').click();
    await henry.locator('#probeSelect').selectOption('btn:open_vault');
    await henry.getByTestId('probe-submit').click();
    evidence.probeShake = await animation(henry,'probe-submit');
    assert.equal(await henry.getByTestId('cross-action-open_vault').isDisabled(),true);
    assert.equal(await henry.locator('.event-card.verdict-deny').count() > 0,true);
    evidence.resourceTreatments = await henry.evaluate(() => ({
      menu:getComputedStyle(document.querySelector('[data-nav="armory"]')).backgroundImage,
      page:getComputedStyle(document.querySelector('#crossPageCards .page-card.denied')).backgroundImage,
      button:getComputedStyle(document.querySelector('#crossActionCards .action-card.denied')).backgroundImage,
      wax:getComputedStyle(document.querySelector('.page-card'),'::after').borderRadius,
      iron:getComputedStyle(document.querySelector('[data-action="forge_sword"]')).boxShadow,
      entryAnimation:getComputedStyle(document.querySelector('.event-card.event-arrival')).animationName,
    }));
    for (const type of ['menu','page','button']) assert.match(evidence.resourceTreatments[type],/repeating-linear-gradient/);
    assert.notEqual(evidence.resourceTreatments.wax,'0px');
    assert.notEqual(evidence.resourceTreatments.iron,'none');
    assert.equal(evidence.resourceTreatments.entryAnimation,'event-enter');
    await screenshot(henry,'03-denial');
  });

  let conflictId;
  await run('Real SSD conflict is amber, preserves its draft, and survives persisted audit refresh',async () => {
    await royal.locator('#adminButton').click();
    await royal.locator('#assignForm').waitFor({state:'visible'});
    await royal.getByTestId('assign-user').selectOption('1');
    await royal.getByTestId('assign-role-TREASURER').check();
    await royal.getByTestId('assign-role-AUDITOR').check();
    const response = royal.waitForResponse(r => r.request().method()==='PUT' && new URL(r.url()).pathname==='/api/admin/users/1/roles');
    await royal.getByTestId('save-assignment').click();
    const result = await response;
    assert.equal(result.status(),409);
    conflictId = (await result.json()).event.id;
    await animation(royal,'save-assignment');
    assert.equal(await royal.getByTestId('assign-role-TREASURER').isChecked(),true);
    assert.equal(await royal.getByTestId('assign-role-AUDITOR').isChecked(),true);
    assert.equal(await royal.locator(`[data-event-id="${conflictId}"].verdict-conflict`).count(),1);
    const refreshed = royal.waitForResponse(r => r.request().method()==='GET' && new URL(r.url()).pathname==='/api/events');
    await royal.locator('#refreshEvents').click();
    await refreshed;
    await royal.waitForFunction(() => !document.querySelector('#refreshEvents').hasAttribute('aria-busy'));
    await royal.waitForFunction(id => document.querySelector(`[data-event-id="${id}"].verdict-conflict`),conflictId);
    const colors = await royal.evaluate(() => Object.fromEntries(['allow','deny','conflict'].map(verdict => [
      verdict, getComputedStyle(document.querySelector(`.event-card.verdict-${verdict}`)).borderLeftColor,
    ])));
    for (const color of Object.values(colors)) assert.match(color,/^rgb\(/);
    assert.equal(new Set(Object.values(colors)).size,3,JSON.stringify(colors));
    evidence.verdictColors=colors;
    await screenshot(royal,'04-ssd-conflict');
  });

  await run('Independent archival scrolls visibly distinguish authorized 200 and denied 403',async () => {
    const archive = await royalCtx.newPage();
    const allowed = await archive.goto('/pages/defense_map');
    assert.equal(allowed.status(),200);
    await archive.locator('.archive-allowed .archive-status').waitFor();
    const watermark = await archive.locator('.archive-watermark').evaluate(el => ({opacity:Number(getComputedStyle(el).opacity),position:getComputedStyle(el).position}));
    assert.ok(watermark.opacity > 0 && watermark.opacity < 0.15);
    assert.equal(watermark.position,'absolute');
    evidence.archiveWatermark=watermark;
    await screenshot(archive,'05-archive-authorized');
    const refused = await henryCtx.newPage();
    const denial = await refused.goto('/pages/defense_map');
    assert.equal(denial.status(),403);
    await refused.locator('.archive-denied .archive-status').waitFor();
    assert.ok(!(await refused.content()).includes('第七骑兵团'));
    await screenshot(refused,'06-archive-denied');
  });

  await run('390px login, command deck, management, denial and archive fit without horizontal overflow',async () => {
    const mobileCtx = await context({viewport:{width:390,height:844}});
    const mobile = await mobileCtx.newPage();
    await mobile.goto('/');
    await noOverflow(mobile);
    await screenshot(mobile,'07-mobile-login');
    await login(mobile,'wenceslas');
    await noOverflow(mobile);
    await screenshot(mobile,'08-mobile-command',false);
    await mobile.locator('[data-mobile-view="admin"]').click();
    await mobile.locator('#assignForm').waitFor();
    await noOverflow(mobile);
    await screenshot(mobile,'09-mobile-admin');
    await mobile.goto('/#/pages/defense_map');
    await mobile.getByTestId('page-content').waitFor();
    await noOverflow(mobile);
    await mobile.goto('/pages/defense_map');
    await noOverflow(mobile);
    await screenshot(mobile,'10-mobile-archive');
    const mobileHenryCtx = await context({viewport:{width:390,height:844}});
    const mobileHenry = await mobileHenryCtx.newPage();
    await login(mobileHenry,'henry');
    await mobileHenry.goto('/#/pages/defense_map');
    await mobileHenry.getByTestId('access-denied').waitFor();
    await noOverflow(mobileHenry);
    await screenshot(mobileHenry,'11-mobile-denial');
  });

  await run('Reduced motion removes animations; blocked Google Fonts retain usable local fallbacks',async () => {
    const ctx = await context({reducedMotion:'reduce'});
    await ctx.route('https://fonts.googleapis.com/**', route => route.abort());
    await ctx.route('https://fonts.gstatic.com/**', route => route.abort());
    const page = await ctx.newPage();
    await login(page,'henry');
    await page.locator('#crossDomainPanel summary').click();
    await page.getByTestId('cross-page-defense_map').click();
    await page.getByTestId('access-denied').waitFor();
    assert.deepEqual(await page.evaluate(() => window.__intercepts),[]);
    assert.equal(await page.locator('.event-card').first().evaluate(el => getComputedStyle(el).animationName),'none');
    await noOverflow(page);
    await screenshot(page,'12-offline-font-fallback');
  });
  assert.deepEqual(errors,[],'JavaScript errors');
  assert.deepEqual(cspErrors,[],'CSP violations');
} finally {
  await writeFile(resolve(output,'visual-results.json'),JSON.stringify({baseURL,results,evidence,pageErrors:errors,cspErrors},null,2));
  for (const ctx of contexts) await ctx.close();
  await browser.close();
}
