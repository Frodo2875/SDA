/* Appearance/keyboard checks also run independently without model requests. */
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function verifyAppearance(page, output) {
  await fs.mkdir(output,{recursive:true});
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('.st-key-nav-chat button').click();
  await page.getByRole('heading',{name:'欢迎来到SDA！'}).waitFor();
  await delay(800);
  const light=await page.locator('.stApp').evaluate(e=>getComputedStyle(e).backgroundColor);
  await page.getByTestId('stMainMenu').getByRole('button').click();
  await page.getByTestId('stMainMenuItem-theme-Dark').click();
  await page.keyboard.press('Escape');
  await delay(800);
  const dark=await page.locator('.stApp').evaluate(e=>getComputedStyle(e).backgroundColor);
  assert.notEqual(dark,light);
  await page.screenshot({path:path.join(output,'07-dark-theme.png'),fullPage:true});
  await page.locator('.st-key-chat-more button').click();
  await page.getByText('显示选项',{exact:true}).click();
  const source=page.getByRole('switch',{name:'查看参考资料',exact:true});
  await source.focus();
  await page.keyboard.press('Space');
  await page.getByRole('heading',{name:'参考资料',exact:true}).waitFor();
  const before=await page.getByTestId('stChatMessage').count();
  const input=page.getByPlaceholder('输入问题或研究目标');
  await input.fill('第一行');
  await input.press('Shift+Enter');
  await input.pressSequentially('第二行');
  assert.equal(await input.inputValue(),'第一行\n第二行');
  assert.equal(await page.getByTestId('stChatMessage').count(),before);
  await input.fill('');
  await page.locator('.st-key-nav-dashboard button').click();
  await page.getByRole('heading',{name:'工作空间概览'}).waitFor();
  await delay(800);
  const metric=await page.getByTestId('stMetric').first().evaluate(e=>({fg:getComputedStyle(e).color,bg:getComputedStyle(e).backgroundColor}));
  assert.notEqual(metric.fg,'rgb(23, 33, 59)');
  const accent=await page.getByRole('tab',{name:'概览',exact:true}).evaluate(e=>getComputedStyle(e).color);
  assert.equal(accent,'rgb(49, 94, 231)');
  await page.screenshot({path:path.join(output,'08-dark-dashboard.png'),fullPage:true});
  const results={passed:true,checks:['native_dark_theme','keyboard_toggle','multiline_without_submission','dark_dashboard','native_accent_consistency'],light,dark,metric,accent};
  await fs.writeFile(path.join(output,'appearance-results.json'),JSON.stringify(results,null,2));
  return results;
}
module.exports={verifyAppearance};
if(require.main===module) {
  const {chromium}=require(process.env.SDA_PLAYWRIGHT_MODULE||'playwright');
  (async()=>{
    const output=process.env.SDA_EVIDENCE_DIR;
    if(!output)throw new Error('Set SDA_EVIDENCE_DIR');
    const browser=await chromium.launch({args:['--no-sandbox']});
    const page=await browser.newPage();
    const consoleErrors=[];
    page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text())});
    page.on('pageerror',error=>consoleErrors.push(error.message));
    try{
      await page.goto(process.env.SDA_UI_URL||'http://127.0.0.1:18501');
      await page.getByRole('heading',{name:'工作空间概览'}).waitFor();
      await verifyAppearance(page,output);
      assert.deepEqual(consoleErrors,[]);
      console.log('PASS real_theme_keyboard_multiline_and_no_console_errors');
    }finally{await browser.close()}
  })().catch(e=>{console.error(e.message);process.exitCode=1});
}
