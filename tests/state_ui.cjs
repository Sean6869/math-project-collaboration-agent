/* Real HTTP/SQLite/browser regression for cross-device workflow and uploads. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process'),{once}=require('node:events'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
(async()=>{
 const temp=path.join(root,'.test-tmp');fs.mkdirSync(temp,{recursive:true});
 const child=spawn(process.env.MPT_TEST_PYTHON||'python',['-u','tests/state_ui_server.py'],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,TMP:temp,TEMP:temp}});
 let browser,log='';child.stderr.on('data',part=>log+=part);
 try{
  const base=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Server startup timeout: '+log)),20000);child.stdout.on('data',part=>{output+=part;const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0])}});child.once('exit',()=>reject(Error(log)))});
  browser=await chromium.launch({headless:true,...(fs.existsSync(chromium.executablePath())?{}:{channel:'msedge'})});
  const student=await browser.newContext(),teacher=await browser.newContext();
  assert((await student.request.post(base+'/api/login',{data:{role:'student',classCode:'STATECLASS',groupId:1,groupCode:'TEAM01'}})).ok());
  assert((await teacher.request.post(base+'/api/login',{data:{role:'teacher',username:'state_teacher',password:'StateTeacher#2026'}})).ok());
  const page=await student.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const getState=async request=>(await (await request.get(base+'/api/state')).json()).state;
  const putState=async(request,state)=>{const response=await request.put(base+'/api/state',{data:{state}});assert(response.ok(),await response.text());return (await response.json()).state};
  const saveTeacher=async patch=>{const state=await getState(teacher.request);Object.assign(state,patch);return putState(teacher.request,state)};
  const waitPut=async action=>{const response=page.waitForResponse(item=>item.url()===base+'/api/state'&&item.request().method()==='PUT');await action();assert((await response).ok())};

  for(const action of ['save','submit']){const response=await student.request.post(base+'/api/group/members',{data:{action,members:['甲','乙']}});assert(response.ok(),await response.text())}
  let state=await getState(student.request);
  Object.assign(state,{preparationChecks:{goal:true,materials:true,rules:true},firstThoughts:{0:'比较价格',1:'预算限制'},questionTree:['核心问题','子问题一','子问题二'],graphConfig:{a:0,b:3,c:0,min:0,max:30,note:'边界核验'},checklist:{0:true,1:true,2:true,3:true,4:true,5:true},evidence:[{text:'预算计算',source:'小组计算',verified:true}],viewpoints:[{who:'方案A',claim:'方案甲',basis:'成本计算',risk:'样本少'},{who:'方案B',claim:'方案乙',basis:'实测数据',risk:'时间短'}],product:{audience:'同学',title:'采购建议',claim:'方案甲更稳妥',limitations:'仅适用于当前预算',evidenceValidation:'用预算计算和实测数据交叉核验',rehearsal:'预演完成',rehearsalRoles:[],rehearsalIssues:[],rehearsalChecks:[true,true,true,true],reflection:'数学工作\n提示帮助\n迁移计划'}});
  Object.assign(state.workspace??={}, {problem:'怎样购买最合适',known:'价格和预算',unknown:'采购数量',constraints:'总价不超过90',variables:'x为数量',relation:'总价=3x',model:'3x≤90'});
  state=await putState(student.request,state);assert.equal(state.versions.length,1);
  state.workspace.model='优惠后总价=2.5x';state=await putState(student.request,state);assert.equal(state.versions.length,2);
  assert.deepEqual(state.stageTaskStatus,Array.from({length:5},()=>[true,true,true,true]));

  await saveTeacher({currentStage:3});await page.goto(base+'/student/stage3.html');
  await page.route('**/api/stages/submit',route=>route.fulfill({status:500,contentType:'application/json',body:'{"error":"测试提交失败"}'}));
  await page.locator('#submitStage').click();await page.waitForFunction(()=>document.querySelector('.toast').textContent.includes('提交失败'));
  assert.equal((await getState(student.request)).stageSubmitted[3],false);await page.unroute('**/api/stages/submit');
  const submitted=page.waitForResponse(item=>item.url()===base+'/api/stages/submit'&&item.request().method()==='POST');await page.locator('#submitStage').click();assert((await submitted).ok());
  await page.waitForFunction(()=>document.querySelector('#submitStage').textContent==='已提交阶段产出');
  let overview=await getState(teacher.request);assert(overview.groups[0].currentSubmitted);assert.equal(overview.groups[0].submitted,1);

  await saveTeacher({currentStage:4,aiOpen:true});await page.goto(base+'/student/stage3-compare.html?stage=4');
  await page.getByText('阶段4/4 · 三级页面',{exact:true}).waitFor();assert.equal(await page.getByText('返回阶段4',{exact:true}).getAttribute('href'),'stage4.html');
  await page.route('**/api/ai/compare',route=>route.fulfill({status:200,contentType:'application/json',body:'{"answer":"先核对样本范围，再比较预算边界。","model":"test"}'}));
  await waitPut(()=>page.locator('#aiCompare').click());await page.getByText('AI比较建议',{exact:true}).waitFor();await page.unroute('**/api/ai/compare');
  await page.goto(base+'/student/stage4-discussion.html');await page.locator('#debateSpeaker').waitFor();assert.deepEqual(await page.locator('#debateSpeaker option').allTextContents(),['甲','乙']);
  await page.locator('#debateSpeaker').selectOption({label:'乙'});await page.locator('#debateText').fill('我质疑的是样本量；我的依据是只有两次测量。');await waitPut(()=>page.locator('#addDebate').click());
  assert(page.url().endsWith('/student/stage4-discussion.html'));await page.waitForFunction(()=>document.body.textContent.includes('乙：'));
  assert.equal(await page.getByText('进入数学工具',{exact:true}).getAttribute('href'),'math-tools.html');

  await page.goto(base+'/student/stage4-evidence.html');await page.locator('#addEvidence').click();assert(await page.locator('#newEvidencePhoto').getAttribute('multiple')!==null);
  const image=await page.evaluate(()=>{const canvas=document.createElement('canvas');canvas.width=2400;canvas.height=1800;const context=canvas.getContext('2d'),data=context.createImageData(canvas.width,canvas.height);let seed=123456789;for(let i=0;i<data.data.length;i+=4){seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;data.data[i]=seed&255;data.data[i+1]=(seed>>>8)&255;data.data[i+2]=(seed>>>16)&255;data.data[i+3]=255}context.putImageData(data,0,0);return canvas.toDataURL('image/png').split(',')[1]});
  const buffer=Buffer.from(image,'base64');await page.locator('#newEvidencePhoto').setInputFiles([{name:'photo-one.png',mimeType:'image/png',buffer},{name:'photo-two.png',mimeType:'image/png',buffer}]);
  await page.waitForFunction(()=>document.querySelector('#evidenceFileName').textContent.includes('2 个附件'));await waitPut(()=>page.locator('#commitEvidence').click());await page.waitForFunction(()=>!document.querySelector('.modal-mask'));
  state=await getState(student.request);const uploaded=state.evidence.slice(-2);assert.equal(uploaded.length,2);assert(uploaded.every(item=>item.file.size<buffer.length));assert(uploaded.every(item=>item.file.name.endsWith('.jpg')));

  await page.goto(base+'/student/math-tools.html');await page.locator('[data-math-lab-tool]').first().waitFor();assert.equal(await page.locator('[data-math-lab-tool]').count(),8);
  await page.locator('#mathLabInput').fill('√81+2²+50%');await page.locator('#runMathLab').click();assert((await page.locator('#mathLabResult').inputValue()).includes('13.5'));
  await page.locator('[data-math-lab-tool="equation"]').click();await page.locator('#mathLabInput').fill('2x+5=17');await page.locator('#runMathLab').click();assert((await page.locator('#mathLabResult').inputValue()).includes('x=6'));
  await page.locator('#mathLabExplanation').fill('预算条件下最多购买6份');await waitPut(()=>page.locator('[data-math-lab-save="evidence"]').click());

  await saveTeacher({currentStage:5});await page.goto(base+'/student/stage5-product.html');await page.locator('[data-p="evidenceValidation"]').waitFor();await page.locator('[data-p="title"]').fill('更新后的成果');await waitPut(()=>page.locator('#saveProduct').click());assert(page.url().endsWith('/student/stage5-product.html'));assert.equal(await page.getByText('进入展示预演',{exact:true}).getAttribute('href'),'stage5-rehearsal.html');
  await page.goto(base+'/student/stage5-rehearsal.html');await page.locator('[data-rehearsal-role]').first().waitFor();assert.equal(await page.locator('[data-rehearsal-role]').count(),2);await page.locator('[data-rehearsal-role="0"]').fill('介绍问题与模型');await page.locator('#issueProblem').fill('统计图缺少单位');await page.locator('#issueSolution').fill('补充单位并复核数据');await waitPut(()=>page.locator('#addRehearsalIssue').click());await page.waitForFunction(()=>document.body.textContent.includes('统计图缺少单位'));
  state=await getState(student.request);assert.equal(state.product.rehearsalIssues.length,1);assert(state.product.rehearsalIssues[0].time.includes('T'));
  await page.goto(base+'/student/workspace.html');assert.equal(await page.locator('.tabs a[href="history.html"]').count(),0);
  await page.goto(base+'/student/history.html');await page.getByText('操作记录',{exact:true}).waitFor();await page.getByText('方案版本',{exact:true}).waitFor();

  for(const viewport of [{width:390,height:844},{width:820,height:1180}]){await page.setViewportSize(viewport);for(const file of ['stage4-evidence.html','math-tools.html','stage5-rehearsal.html']){await page.goto(base+'/student/'+file);await page.locator('.shell').waitFor();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+2),`${file} overflows at ${viewport.width}px`)}}
  await page.setViewportSize({width:1440,height:900});
  for(const [role,context] of [['student',student],['teacher',teacher]]){const audit=await context.newPage();audit.on('pageerror',error=>errors.push(error.message));for(const file of fs.readdirSync(path.join(root,role)).filter(name=>name.endsWith('.html'))){await audit.goto(base+'/'+role+'/'+file);await audit.waitForFunction(()=>document.querySelector('#root')?.innerHTML.length>0);assert.equal(await audit.getByText('平台暂时不可用',{exact:true}).count(),0,role+'/'+file)}await audit.close()}
  assert.deepEqual(errors,[]);console.log('PASS: atomic teacher sync, stage 4 context, speaker records, no save redirects, compressed multi-photo upload, math laboratory, rehearsal records, process review, phone/tablet layout');
 }finally{if(browser)await browser.close();const exited=once(child,'exit');child.stdin.end();await exited}
})().catch(error=>{console.error(error);process.exitCode=1});
