/* Run only after deliberately stopping the isolated acceptance backend. */
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {chromium}=require(process.env.SDA_PLAYWRIGHT_MODULE||'playwright');
const output=process.env.SDA_EVIDENCE_DIR;
if(!output)throw new Error('Set SDA_EVIDENCE_DIR');
(async()=>{
  await fs.mkdir(output,{recursive:true});
  const browser=await chromium.launch({args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  try{
    await page.goto(process.env.SDA_UI_URL||'http://127.0.0.1:18501');
    await page.getByRole('heading',{name:'工作空间概览'}).waitFor();
    await page.locator('.st-key-nav-documents button').click();
    await page.getByRole('button',{name:'刷新文件列表',exact:true}).click();
    await page.getByText(/无法连接后端服务/).waitFor();
    assert.equal(await page.getByTestId('stException').count(),0);
    assert.equal(await page.getByText('当前没有可用材料文件。',{exact:true}).count(),0);
    await page.screenshot({path:path.join(output,'backend-unavailable.png'),fullPage:true});
    await fs.writeFile(path.join(output,'backend-unavailable.json'),JSON.stringify({passed:true,check:'real_backend_stopped_visible_error_no_false_empty'}));
    console.log('PASS real_backend_stopped_visible_error_no_false_empty');
  }finally{await browser.close()}
})().catch(error=>{console.error(error.message);process.exitCode=1});
