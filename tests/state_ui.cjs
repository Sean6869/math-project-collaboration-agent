/* Real HTTP/SQLite/browser regression, with AI off and a disposable classroom. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process'),{once}=require('node:events'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
(async()=>{
 const child=spawn(process.env.MPT_TEST_PYTHON||'python',['-u','tests/state_ui_server.py'],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
 let browser,log='';child.stderr.on('data',part=>log+=part);
 try{
  const base=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Server startup timeout: '+log)),15000);child.stdout.on('data',part=>{text+=part;const match=text.match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0])}});child.once('exit',()=>{clearTimeout(timer);reject(Error(log))})});
  browser=await chromium.launch({headless:true,...(fs.existsSync(chromium.executablePath())?{}:{channel:'msedge'})});
  const context=await browser.newContext(),teacher=await browser.newContext();
  const login=await context.request.post(base+'/api/login',{data:{role:'student',classCode:'STATECLASS',groupId:1,groupCode:'TEAM01'}});assert(login.ok(),await login.text());
  assert((await teacher.request.post(base+'/api/login',{data:{role:'teacher',username:'state_teacher',password:'StateTeacher#2026'}})).ok());
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const getState=async request=>(await (await request.get(base+'/api/state')).json()).state;
  const saveTeacher=async patch=>{const state=await getState(teacher.request);Object.assign(state,patch);const r=await teacher.request.put(base+'/api/state',{data:{state}});assert(r.ok(),await r.text())};
  const saveButton=async selector=>{const response=page.waitForResponse(r=>r.url()===base+'/api/state'&&r.request().method()==='PUT');await page.locator(selector).click();assert((await response).ok());await page.waitForFunction(()=>!document.querySelector('[data-saving]'))};
  await page.goto(base+'/student/stage3-model.html');
  for(const [id,value] of Object.entries({'m-var':'x是购买数量','m-rel':'总价=3x','m-model':'3x≤90','m-range':'0≤x≤30'}))await page.locator('#'+id).fill(value);
  await saveButton('#saveModel');let state=await getState(context.request);
  assert.equal(state.versions.length,1);assert.equal(state.versions[0].model,'3x≤90');assert(state.stageTaskStatus[2][2]);
  await saveButton('#saveModel');assert.equal((await getState(context.request)).versions.length,1);
  await page.goto(base+'/student/stage3-check.html');
  for(let i=0;i<6;i++)await page.locator(`[data-check="${i}"]`).check();await saveButton('#saveCheck');
  await page.goto(base+'/student/stage3.html');await page.locator('#submitStage').waitFor();assert.equal(await page.locator('.task .badge.ok').count(),4);
  await saveTeacher({currentStage:3});
  await page.waitForFunction(()=>!document.querySelector('#submitStage').disabled,{timeout:12000});
  // A rejected submission must not mark the phase complete or lose retry ability.
  await page.route('**/api/state',async route=>{if(route.request().method()==='PUT')return route.fulfill({status:500,contentType:'application/json',body:'{"error":"测试保存失败"}'});await route.continue()});
  await page.locator('#submitStage').click();await page.waitForFunction(()=>document.querySelector('.toast').textContent.includes('保存失败'));
  assert.equal(await page.evaluate(()=>window.MPT.load().stageSubmitted[3]),false);
  await page.unroute('**/api/state');await saveButton('#submitStage');
  await page.waitForFunction(()=>document.querySelector('#submitStage').textContent==='已提交阶段产出');
  state=await getState(teacher.request);assert(state.groups[0].currentSubmitted);assert(state.stageSubmitted[3]);
  // One-click workspace versioning must capture current form input, not prior state.
  await page.goto(base+'/student/workspace.html');await page.locator('[data-work="model"]').fill('优惠后总价=2.5x');await saveButton('#newVersion');
  state=await getState(context.request);assert.equal(state.versions.length,2);assert.equal(state.versions[1].model,'优惠后总价=2.5x');assert.equal(state.versions[0].model,'3x≤90');
  await saveTeacher({currentStage:5});
  // Static mathematics tools remain usable with AI disabled.
  await page.goto(base+'/student/stage3-graph.html');
  for(const [id,value] of Object.entries({graphA:'0',graphB:'2.5',graphC:'0',graphMin:'0',graphMax:'30',graphJudge:'边界不超过预算'}))await page.locator('#'+id).fill(value);
  await saveButton('#saveGraphJudge');assert(await page.locator('#graph').evaluate(c=>c.width>0));
  await page.goto(base+'/student/stage3-compare.html');
  await page.locator('#saveCompare').waitFor();
  for(const el of await page.locator('[data-v]').all())await el.fill('本组方案、计算依据和局限');await saveButton('#saveCompare');
  state=await getState(context.request);assert(state.stageTaskStatus[3][2]);assert.equal(state.aiOpen,false);
  await page.goto(base+'/student/stage4-tools.html');await page.locator('#calcIn').fill('(12+8)/4');await page.locator('#calcBtn').click();
  await page.waitForFunction(()=>document.querySelector('#calcOut').textContent==='结果：5');
  // Submit all five phases against the same server-side completion contract.
  for(const action of ['save','submit']){const response=await context.request.post(base+'/api/group/members',{data:{action,members:['甲','乙']}});assert(response.ok(),await response.text())}
  state=await getState(context.request);
  Object.assign(state,{preparationChecks:{goal:true,materials:true,rules:true},firstThoughts:{0:'比较价格',1:'预算限制'},questionTree:['核心问题','子问题一','子问题二'],evidence:[{text:'预算计算',source:'小组计算',verified:true}],product:{audience:'同学',claim:'最优方案',limitations:'预算范围',rehearsal:'预演完成',rehearsalChecks:[true,true,true,true],reflection:'数学工作\n提示帮助\n迁移计划'}});
  Object.assign(state.workspace,{problem:'怎样购买最合适',known:'价格',unknown:'采购数量'});
  let response=await context.request.put(base+'/api/state',{data:{state}});assert(response.ok(),await response.text());
  assert.deepEqual((await response.json()).state.stageTaskStatus,Array.from({length:5},()=>[true,true,true,true]));
  for(let n=1;n<=5;n++){
   await saveTeacher({currentStage:n});state=await getState(context.request);state.stageSubmitted[n]=true;
   response=await context.request.put(base+'/api/state',{data:{state}});assert(response.ok(),await response.text());
   const overview=await getState(teacher.request);assert(overview.groups[0].currentSubmitted,'teacher must see phase '+n);
  }
  assert.equal((await getState(teacher.request)).groups[0].progress,100);
  // Ensure every current student/teacher page still renders on real saved state.
  for(const [role,ctx] of [['student',context],['teacher',teacher]]){
   const audit=await ctx.newPage();audit.on('pageerror',e=>errors.push(e.message));
   for(const file of fs.readdirSync(path.join(root,role)).filter(x=>x.endsWith('.html'))){
    await audit.goto(base+'/'+role+'/'+file);await audit.waitForFunction(()=>document.querySelector('#root')?.innerHTML.length>0);
    assert.equal(await audit.getByText('平台暂时不可用',{exact:true}).count(),0,role+'/'+file);
   }
   await audit.close();
  }
  assert.deepEqual(errors,[]);console.log('PASS: real model/version saves, duplicate-save idempotence, stage 3 completion, failed submission retry, all five phase submissions and teacher progress, live phase refresh, current workspace snapshot, static graph/calculator with AI off, all page renders');
 }finally{if(browser)await browser.close();const exited=once(child,'exit');child.stdin.end();await exited}
})().catch(error=>{console.error(error);process.exitCode=1});
