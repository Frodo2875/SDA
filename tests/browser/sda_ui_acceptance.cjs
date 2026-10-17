/* Optional real-browser acceptance. Uses a separately installed Playwright.
 * SDA_UI_URL, SDA_API_URL and SDA_EVIDENCE_DIR must point to the isolated run.
 * No model/search response is mocked. Only synthetic text is uploaded.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require(process.env.SDA_PLAYWRIGHT_MODULE || 'playwright');
const {verifyAppearance}=require('./sda_ui_appearance.cjs');
const ui = process.env.SDA_UI_URL || 'http://127.0.0.1:18501';
const api = process.env.SDA_API_URL || 'http://127.0.0.1:18000';
const evidence = process.env.SDA_EVIDENCE_DIR;
if (!evidence) throw new Error('Set SDA_EVIDENCE_DIR to an isolated evidence directory');
const results = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  await fs.mkdir(evidence, {recursive: true});
  const browser = await chromium.launch({headless:true, args:['--no-sandbox']});
  const context = await browser.newContext({viewport:{width:1440,height:1000}, acceptDownloads:true});
  const page = await context.newPage();
  const errors=[];
  page.on('pageerror', error => errors.push(error.message));
  page.setDefaultTimeout(30000);
  const settle=async()=>{
    await delay(400);
    await page.getByTestId('stStatusWidget').waitFor({state:'hidden',timeout:180000});
    await delay(200);
  };
  const nav = async name => {
    const keys={'首页':'dashboard','新建聊天':'chat','知识库':'knowledge','我的文档':'documents','研究任务':'tasks','报告中心':'reports','使用统计':'usage','系统设置':'settings'};
    const button=page.locator('.st-key-nav-'+keys[name]+' button');
    if(!await page.getByTestId('stSidebar').isVisible()) await page.getByTestId('stExpandSidebarButton').click();
    await button.click();
    await delay(800);
    assert.equal(await page.getByTestId('stException').count(),0);
  };
  const screenshot = async name => {
    await page.evaluate(()=>document.fonts.ready);
    await delay(500);
    return page.screenshot({path:path.join(evidence, name+'.png'), fullPage:true});
  };
  const record = (name, detail={}) => {results.push({name,passed:true,...detail}); console.log('PASS',name);};
  async function json(url, options) {
    const response=await context.request.fetch(api+url,options);
    assert(response.ok(),`API status ${response.status()} for ${url}`);
    return response.json();
  }
  try {
    assert.equal((await json('/health')).status,'ok');
    await page.goto(ui);
    await page.getByRole('heading',{name:'工作空间概览'}).waitFor();
    await screenshot('01-dashboard');
    record('real_frontend_backend_health');
    await nav('知识库');
    await page.getByText('新建知识库',{exact:true}).first().click();
    const runId=Date.now();
    const kbName='UI验收合成资料-'+runId;
    const documentName='student_info_'+runId+'.md';
    await page.getByRole('textbox',{name:'名称',exact:true}).fill(kbName);
    await page.getByRole('textbox',{name:'描述',exact:true}).fill('仅用于UI验收的合成数据');
    await page.getByRole('button',{name:'创建',exact:true}).click();
    await page.getByText('知识库创建成功',{exact:true}).waitFor();
    const kb=(await json('/api/knowledge-bases')).data.find(item=>item.name===kbName);
    assert(kb);
    await page.locator('.st-key-knowledge-enter-'+kb.knowledge_base_id+' button').click();
    await page.getByRole('heading',{name:'文件管理',exact:true}).waitFor();
    const content='# SDA合成学生资料\n\n## 专业分布\n\n本批合成材料共3名学生，其中计算机专业2名、数学专业1名。\n\n## 核验说明\n\n本文件完全为测试生成，不包含真实个人信息。\n';
    await page.locator('input[type=file]').setInputFiles({name:documentName,mimeType:'text/markdown',buffer:Buffer.from(content)});
    await page.getByRole('button',{name:'上传所选文件',exact:true}).click();
    await page.getByText(documentName+'：已上传并加入知识库',{exact:true}).waitFor({timeout:60000});
    const files=(await json('/api/knowledge-bases/'+kb.knowledge_base_id+'/files')).data;
    assert.equal(files.length,1);
    assert.equal(files[0].queryable,true);
    await screenshot('02-knowledge-upload');
    record('browser_create_upload_index',{file_id:files[0].file_id});
    await page.locator('input[type=file]').setInputFiles({name:'broken_'+runId+'.pdf',mimeType:'application/pdf',buffer:Buffer.from('not a valid PDF')});
    await page.getByRole('button',{name:'上传所选文件',exact:true}).click();
    await page.getByText(/文件不是可正常打开的 PDF/).waitFor();
    assert.equal((await json('/api/knowledge-bases/'+kb.knowledge_base_id+'/files')).data.length,1);
    await screenshot('02b-invalid-upload');
    record('invalid_upload_visible_no_file_saved');
    await page.locator('.st-key-knowledge-chat button').click();
    await page.getByRole('heading',{name:'欢迎来到SDA！'}).waitFor();
    assert.equal(await page.getByText('查看参考资料',{exact:true}).isVisible(),false);
    await page.getByPlaceholder('输入问题或研究目标').fill(`根据${documentName}，合成材料的总人数是多少？请检索文件后回答并引用资料。`);
    await page.getByPlaceholder('输入问题或研究目标').press('Enter');
    await page.getByTestId('stChatMessage').nth(1).waitFor({timeout:180000});
    assert.match(await page.getByTestId('stChatMessage').nth(1).innerText(),/3|三/);
    await screenshot('03-real-model-answer');
    record('browser_real_model_document_question');
    await page.getByPlaceholder('输入问题或研究目标').fill('根据同一个文件，计算机专业有多少人？请检索核实。');
    await page.getByPlaceholder('输入问题或研究目标').press('Enter');
    await page.getByTestId('stChatMessage').nth(3).waitFor({timeout:180000});
    assert.match(await page.getByTestId('stChatMessage').nth(3).innerText(),/2|两|二/);
    record('real_model_followup');
    await page.locator('.st-key-chat-more button').click();
    await page.getByText('显示选项',{exact:true}).click();
    await page.getByText('查看参考资料',{exact:true}).click();
    await settle();
    await page.getByRole('heading',{name:'参考资料',exact:true}).waitFor();
    const locate=page.getByRole('button',{name:'打开原文',exact:true}).first();
    if(await locate.count()) {await locate.click(); await page.getByText(/原文预览：/).first().waitFor();}
    await page.locator('.st-key-chat-more button').click();
    await settle();
    await nav('我的文档');
    await nav('新建聊天');
    await page.getByRole('heading',{name:'参考资料',exact:true}).waitFor();
    assert.equal(await page.getByTestId('stChatMessage').count(),4);
    record('source_location_and_state_preserved');
    // Turn sources back off before checking the compact composer.
    await page.locator('.st-key-chat-more button').click();
    await page.getByText('显示选项',{exact:true}).click();
    await page.getByText('查看参考资料',{exact:true}).click();
    await settle();
    await page.locator('.st-key-chat-more button').click();
    await settle();
    for(let attempt=0;attempt<3;attempt++) {
      await page.locator('.st-key-chat_mode').getByRole('combobox').click();
      try {await page.getByRole('option',{name:'报告生成',exact:true}).waitFor({timeout:3000});break;}
      catch(error) {if(attempt===2)throw error;await page.keyboard.press('Escape');await settle();}
    }
    await page.getByRole('option',{name:'报告生成',exact:true}).click();
    await settle();
    await page.getByPlaceholder('输入问题或研究目标').fill(`根据${documentName}的合成材料分析专业分布，生成简短研究报告。只使用本地材料，不联网，不写文件。`);
    await page.getByPlaceholder('输入问题或研究目标').press('Enter');
    await page.getByRole('button',{name:'在任务中心打开',exact:true}).waitFor({timeout:60000});
    const chatText=await page.locator('.st-key-sda-chat').innerText();
    const taskId=chatText.match(/研究任务 · ([A-Za-z0-9_-]+)/)?.[1];
    assert(taskId,'Research task ID is visible');
    let task;
    for(let i=0;i<180;i++) {
      task=await json('/api/research-task/'+taskId);
      if(['COMPLETED','FAILED','CANCELLED','WAITING_CONFIRMATION'].includes(task.status))break;
      await delay(2000);
    }
    assert.equal(task.status,'COMPLETED');
    await page.getByRole('button',{name:'刷新研究状态',exact:true}).click();
    await page.getByRole('button',{name:'生成报告草稿',exact:true}).click();
    await page.getByRole('heading',{name:'证据研究报告',exact:true}).waitFor({timeout:60000});
    await screenshot('04-research-completed');
    record('real_model_async_research_report',{task_id:taskId});
    await nav('报告中心');
    await page.getByRole('button',{name:'查看 / 下载 / 版本',exact:true}).first().click();
    await page.getByRole('button',{name:'预览并申请Word导出',exact:true}).waitFor();
    const exported=[];
    for(const format of ['Word','Markdown']) {
      await page.getByRole('button',{name:'预览并申请'+format+'导出',exact:true}).click();
      await page.getByRole('heading',{name:'操作确认',exact:true}).waitFor();
      assert.equal(await page.getByRole('button',{name:'下载'+format,exact:true}).count(),0);
      await page.getByRole('button',{name:'确认',exact:true}).click();
      await page.getByText('操作已确认执行。',{exact:true}).waitFor();
      await page.getByRole('button',{name:'加载'+format+'下载',exact:true}).click();
      const pending=page.waitForEvent('download');
      await page.getByRole('button',{name:'下载'+format,exact:true}).click();
      const download=await pending;
      const target=path.join(evidence,download.suggestedFilename());
      await download.saveAs(target);
      assert((await fs.stat(target)).size>0);
      exported.push(target);
    }
    await page.getByRole('button',{name:'恢复此版本',exact:true}).first().click();
    await page.getByRole('heading',{name:'操作确认',exact:true}).waitFor();
    await page.getByRole('button',{name:'取消',exact:true}).click();
    await page.getByText('操作已取消。',{exact:true}).waitFor();
    await page.getByRole('button',{name:'恢复此版本',exact:true}).first().click();
    await page.getByRole('heading',{name:'操作确认',exact:true}).waitFor();
    await page.getByRole('button',{name:'确认',exact:true}).click();
    await page.getByText('操作已确认执行。',{exact:true}).waitFor();
    await screenshot('05-report-approval-download');
    record('browser_report_approval_download_versions_rollback_confirm_cancel',{downloads:exported.map(x=>path.basename(x))});
    await nav('知识库');
    await page.getByRole('heading',{name:'文件管理',exact:true}).waitFor();
    const longName='synthetic_'+runId+'_'+'long_filename_'.repeat(10)+'.txt';
    await page.locator('input[type=file]').setInputFiles({name:longName,mimeType:'text/plain',buffer:Buffer.from('Synthetic UI layout fixture only.')});
    await page.getByRole('button',{name:'上传所选文件',exact:true}).click();
    await page.getByText(longName+'：已上传并加入知识库',{exact:true}).waitFor();
    await page.setViewportSize({width:390,height:1000});
    await delay(800);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false);
    await screenshot('05b-long-filename-mobile');
    await page.setViewportSize({width:1440,height:1000});
    record('long_filename_multiple_documents_mobile');
    for(const name of ['研究任务','使用统计','系统设置','首页']) {await nav(name);}
    record('all_navigation_pages');
    await nav('新建聊天');
    for(const width of [1440,1024,768,390]) {
      await page.setViewportSize({width,height:1000});
      await delay(800);
      const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2);
      assert.equal(overflow,false,'No document overflow at '+width);
      const composer=page.locator('.st-key-sda-composer');
      const box=await composer.boundingBox();
      assert(box && box.width<=width,'Composer fits viewport');
      await screenshot('06-chat-width-'+width);
    }
    record('responsive_chat_1440_1024_768_390');
    await verifyAppearance(page,evidence);
    record('native_dark_theme_keyboard_and_multiline');
    assert.equal(errors.length,0,'No browser page errors');
    record('no_browser_page_errors');
  } catch(error) {
    results.push({name:'failure',passed:false,message:error.message});
    await screenshot('failure');
    process.exitCode=1;
    console.error(error.message);
  } finally {
    await fs.writeFile(path.join(evidence,'browser-results.json'),JSON.stringify({results,pageErrors:errors},null,2));
    await browser.close();
  }
})();
