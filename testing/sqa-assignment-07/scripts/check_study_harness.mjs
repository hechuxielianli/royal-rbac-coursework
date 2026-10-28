/** A tool smoke check; its data is kept separately from participant observations. */
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({headless:true,channel:'msedge'});
try{
 const page=await browser.newPage();const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('console',m=>{if(m.type()==='error'&&!m.location().url.endsWith('/favicon.ico'))errors.push(m.text());});
 await page.goto('http://127.0.0.1:5090/study');
 await page.locator('#person').fill('AUTOMATION-TOOL-CHECK');
 await page.locator('#U1 .result').selectOption('未完成');
 await page.locator('#U1 .notes').fill('自动化工具校核；无参与者，不计入用户结果');
 await page.locator('[data-start="U1"]').click();
 await page.locator('#U1 .started').filter({hasText:'已开始'}).waitFor();
 await page.waitForTimeout(300);
 await page.locator('[data-finish="U1"]').click();
 await page.locator('#U1 .saved').filter({hasText:'已保存'}).waitFor();
 const styles=await page.locator('body').evaluate(e=>({fontSize:getComputedStyle(e).fontSize,background:getComputedStyle(e).backgroundColor}));
 assert.equal(styles.fontSize,'18px');assert.equal(errors.length,0,JSON.stringify(errors));
 const records=JSON.parse(await readFile(new URL('../evidence/study-harness-check-records.json',import.meta.url),'utf8'));
 assert.equal(records.length,1);assert.equal(records[0].person,'AUTOMATION-TOOL-CHECK');
 assert(records[0].elapsed_seconds>0);assert.equal(records[0].verified_actions.length,0);
 await writeFile(new URL('../evidence/study-harness-check.json',import.meta.url),JSON.stringify({status:'passed',checks:['same-origin external CSS loaded','same-origin script loaded without CSP errors','start stores time','finish saves elapsed time','tool records separate from P1 observations'],styles,errors,record:records[0]},null,2));
 console.log('PASS study harness; participant records unchanged');
}finally{await browser.close();}
