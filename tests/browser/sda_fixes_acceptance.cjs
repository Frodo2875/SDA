/* Real frontend/backend/model acceptance for the October audit fixes.
 * Required: SDA_UI_URL, SDA_API_URL, SDA_EVIDENCE_DIR, SDA_FIXTURE_DIR.
 * Use only a fresh isolated backend; this uploads conventional student files.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require(process.env.SDA_PLAYWRIGHT_MODULE || 'playwright');
const output = process.env.SDA_EVIDENCE_DIR;
const fixtures = process.env.SDA_FIXTURE_DIR;
if (!output || !fixtures || !process.env.SDA_API_URL || !process.env.SDA_UI_URL) {
  throw new Error('Set isolated SDA_UI_URL, SDA_API_URL, SDA_EVIDENCE_DIR and SDA_FIXTURE_DIR');
}
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  await fs.mkdir(output, {recursive:true});
  const browser = await chromium.launch({headless:true, args:['--no-sandbox']});
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  const results = [], errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const save = async () => fs.writeFile(path.join(output,'results.json'),JSON.stringify({results,pageErrors:errors},null,2));
  const record = async (name, detail={}) => {
    results.push({name,passed:true,...detail}); await save(); console.log('PASS',name);
  };
  const settle = async () => {
    await delay(350);
    await page.getByTestId('stStatusWidget').waitFor({state:'hidden',timeout:180000});
    assert.equal(await page.getByTestId('stException').count(),0);
  };
  const api = async route => {
    const r = await context.request.get(process.env.SDA_API_URL+route);
    assert(r.ok(),`${route}: ${r.status()}`); return r.json();
  };
  const screenshot = async name => {
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});
  };
  const ask = async (question, expected) => {
    const before = await page.getByTestId('stChatMessage').count();
    await page.getByPlaceholder('输入问题或研究目标').fill(question);
    await page.getByPlaceholder('输入问题或研究目标').press('Enter');
    const answer = page.getByTestId('stChatMessage').nth(before+1);
    await answer.waitFor({timeout:180000}); await settle();
    const text = await answer.innerText(); assert.match(text,expected);
    return {question,answer:text};
  };
  try {
    await page.goto(process.env.SDA_UI_URL);
    await page.getByRole('heading',{name:'工作空间概览'}).waitFor();
    await page.locator('.st-key-nav-knowledge button').click(); await settle();
    await page.getByText('新建知识库',{exact:true}).first().click();
    const name='四项修复验收-'+Date.now();
    await page.getByRole('textbox',{name:'名称',exact:true}).fill(name);
    await page.getByRole('button',{name:'创建',exact:true}).click();
    await page.getByText('知识库创建成功',{exact:true}).waitFor();
    const kb=(await api('/api/knowledge-bases')).data.find(k=>k.name===name);
    await page.locator('.st-key-knowledge-enter-'+kb.knowledge_base_id+' button').click();
    await page.getByRole('heading',{name:'文件管理',exact:true}).waitFor();
    const names=['学生基本信息.xlsx','学生成绩.xlsx','科研成果.xlsx','修复验收评审办法.md','fix_stock.xlsx'];
    await page.locator('input[type=file]').setInputFiles(names.map(n=>path.join(fixtures,n)));
    await page.getByRole('button',{name:'上传所选文件',exact:true}).click();
    for (const n of names) await page.getByText(n+'：已上传并加入知识库',{exact:true}).waitFor({timeout:60000});
    const uploaded=(await api('/api/knowledge-bases/'+kb.knowledge_base_id+'/files')).data;
    assert.equal(uploaded.length,5); assert(uploaded.every(f=>f.queryable));
    const tables=uploaded.filter(f=>['学生成绩.xlsx','科研成果.xlsx'].includes(f.file_name));
    assert(tables.every(f=>f.schema_summary===null),'Must start without saved table schemas');
    await record('browser_upload_all_materials_with_cold_schemas',{knowledge_base_id:kb.knowledge_base_id});
    await page.locator('.st-key-knowledge-chat button').click();
    await page.getByRole('heading',{name:'欢迎来到SDA！'}).waitFor();
    const eligible=await ask('请根据本地学生材料，判断S001是否符合一等奖学金资格，并说明依据。不要联网。',/S001满足一等奖学金条件/);
    await screenshot('01-natural-scholarship');
    await record('cold_natural_scholarship_no_ids_or_tool_steps',eligible);
    const after=(await api('/api/knowledge-bases/'+kb.knowledge_base_id+'/files')).data;
    assert(after.filter(f=>tables.some(t=>t.file_id===f.file_id)).every(f=>f.schema_summary?.sheet_count>0));
    await record('schemas_discovered_by_agent_during_question');
    await record('ineligible_student_rule',await ask('请判断S002是否符合一等奖学金资格，只使用本地学生材料。',/S002不满足一等奖学金条件/));
    await record('missing_research_is_not_zero',await ask('请判断S003是否符合一等奖学金资格，只使用本地学生材料。',/当前材料不足以判断/));
    await record('duplicate_name_requires_clarification',await ask('请判断测试重名是否符合一等奖学金资格，只使用本地材料。',/S004[\s\S]*S005/));
    const aggregation=await ask('只用本地库存文件fix_stock.xlsx，按类别筛选传感器，计算数量之和，并给出来源。不要联网。',/22/);
    await page.locator('.st-key-chat-more button').click();
    await page.getByText('显示选项',{exact:true}).click();
    await page.getByText('查看参考资料',{exact:true}).click(); await settle();
    await page.getByRole('heading',{name:'参考资料',exact:true}).waitFor();
    await page.locator('.st-key-chat-more button').click(); await settle();
    // Earlier messages also keep collapsed source cards in the DOM.
    await page.locator('p:visible').filter({hasText:'参与求和：数量'}).first().waitFor();
    await page.locator('p:visible').filter({hasText:'筛选：类别 = 传感器'}).first().waitFor();
    await page.getByRole('button',{name:'打开原文',exact:true}).first().click();
    await page.getByText(/原文预览：fix_stock.xlsx/).first().waitFor();
    await screenshot('02-aggregate-source-location');
    await record('aggregation_sources_computation_and_original_cells_visible',aggregation);
    const url=await ask('请读取 https://docs.python.org/3/library/asyncio.html 并用一句话说明asyncio的用途。',/asyncio/);
    await page.getByRole('link',{name:'打开来源网页',exact:true}).first().waitFor();
    await screenshot('03-real-url-fetch');
    await record('real_python_docs_url_visible_in_frontend',url);
    assert.equal(errors.length,0);
    await record('no_browser_page_errors');
  } catch (error) {
    results.push({name:'failure',passed:false,message:error.message});
    await screenshot('failure');process.exitCode=1;console.error(error.stack);
  } finally {
    await save();await browser.close();
  }
})();
