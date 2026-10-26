const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require('playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),python=process.env.TEST_PYTHON||'python';
 const server=spawn(python,['tests/classroom_ui_server.py'],{cwd:root,stdio:['pipe','pipe','pipe']});
 let log='';server.stderr.on('data',d=>log+=d);let browser;
 const base=await new Promise((resolve,reject)=>{server.stdout.on('data',d=>{const text=String(d).trim();if(text.startsWith('http://'))resolve(text.split('\n')[0])});server.on('exit',code=>reject(Error('test server '+code+' '+log)))});
 try{
  browser=await chromium.launch({headless:true,...(fs.existsSync(chromium.executablePath())?{}:{channel:'msedge'})});
  const student=await browser.newContext({viewport:{width:1680,height:945}}),teacher=await browser.newContext({viewport:{width:1680,height:945}});
  assert((await student.request.post(base+'/api/login',{data:{role:'student',classId:'default',groupId:1,studentCode:'9512'}})).ok());
  assert((await teacher.request.post(base+'/api/login',{data:{role:'teacher',username:'ui_teacher',password:'ClassroomTeacher#2026'}})).ok());
  const p=await student.newPage(),t=await teacher.newPage(),errors=[];p.on('dialog',d=>d.accept('核对条件'));t.on('dialog',d=>d.accept('核对条件'));p.on('pageerror',e=>errors.push(e.message));t.on('pageerror',e=>errors.push(e.message));
  await p.goto(base+'/student/stage2-understanding.html');await p.locator('#thoughtContent').fill('先核对预算');await p.locator('#thoughtMode').selectOption('proxy');await p.locator('#thoughtConfirmed').check();await p.locator('#saveThoughtOne').click();await p.getByText('第1版',{exact:false}).waitFor({state:'attached'});
  await p.locator('#thoughtContent').fill('补充数量限制');await p.locator('#thoughtMode').selectOption('proxy');await p.locator('#thoughtConfirmed').check();await p.locator('#saveThoughtOne').click();await p.getByText('第2版',{exact:false}).waitFor({state:'attached'});
  await p.locator('#under-problem').fill('采购奖品');await p.locator('#under-known').fill('总预算100元');await p.locator('#under-constraints').fill('不能超预算');await p.locator('#saveUnderstandingNew').click();await p.getByText('已保存到服务器',{exact:true}).first().waitFor();
  await p.goto(base+'/student/stage3-model.html');await p.locator('#modelVariables').fill('x表示数量，单位个');await p.locator('#modelRelation').fill('8');await p.locator('[data-symbol="×"]').dispatchEvent('pointerdown');await p.locator('[data-symbol="x"]').dispatchEvent('pointerdown');await p.locator('#modelReason').fill('补充单价和数量');await p.locator('#saveModelNew').click();await p.locator('#saveModelNew').filter({hasText:'V3'}).waitFor();
  await p.goto(base+'/student/stage3-compare.html');await p.locator('#compareConditions').fill('同样买10个');await p.locator('#compare-0-claim').fill('方案一');await p.locator('#compare-0-basis').fill('计算80元');await p.locator('#saveCompareNew').click();
  await p.goto(base+'/student/group.html');await p.locator('[data-role-index="0"]').selectOption('检查者');await p.locator('#confirmRoles').click();await p.getByText('历史分工').waitFor();
  await p.goto(base+'/student/stage5-product.html');await p.locator('#productVersions').waitFor();assert(await p.locator('#productModelRef option').count()>=3);
  await t.goto(base+'/teacher/project.html');await t.locator('#classroomConfig').waitFor();await t.locator('#learnedKnowledge').fill('已学乘除法，未学方程');await t.locator('#brief-situation').fill('班级采购奖品');await t.locator('#brief-problem').fill('怎样购买更合适');await t.locator('#brief-conditions').fill('总预算100元');await t.locator('#brief-output').fill('采购方案');await t.locator('#briefConfirmed').check();await t.locator('#saveClassroomConfig').click();await t.getByText('已保存到服务器',{exact:true}).first().waitFor();
  await p.goto(base+'/student/stage1-materials.html');await p.getByRole('heading',{name:'题目告诉了我们什么？'}).waitFor();
  await t.goto(base+'/teacher/evaluation.html?g=1');await t.locator('#evaluationComment').fill('请核对单位');await t.locator('#evaluationDraft').click();await t.getByText('草稿',{exact:true}).waitFor();
  await t.locator('#evaluationPublish').click();await t.getByText('已发布',{exact:true}).waitFor();
  await p.goto(base+'/student/dashboard.html');await p.locator('#publishedEvaluation').waitFor();
  const confirmed=(await (await student.request.get(base+'/api/state')).json()).state,stagePercents=confirmed.stageTaskStatus.map(tasks=>Math.round(tasks.filter(Boolean).length/tasks.length*100));
  assert.deepEqual(await p.locator('.dashboard-progress-lines b').allTextContents(),stagePercents.map(value=>value+'%'));
  assert.equal(await p.locator('.dashboard-progress-ring b').innerText(),Math.round(stagePercents.reduce((sum,value)=>sum+value,0)/5)+'%');
  const monitored=(await (await teacher.request.get(base+'/api/state')).json()).state.groups.find(group=>group.id===1);assert.equal(monitored.progress,Math.round(stagePercents.reduce((sum,value)=>sum+value,0)/5));
  await p.screenshot({path:path.join(root,'.test-tmp','classroom-dashboard.png'),fullPage:true});
  await t.goto(base+'/teacher/groups.html');await t.locator('#groupCards article').first().waitFor();await t.locator('#groupFilter').selectOption('stage-5');
  for(const width of [1680,768]){await p.setViewportSize({width,height:1024});await p.goto(base+'/student/stage2-understanding.html');await p.locator('#thoughtContent').waitFor();assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await p.screenshot({path:path.join(root,'.test-tmp',`classroom-understanding-${width}.png`),fullPage:true})}
  await t.screenshot({path:path.join(root,'.test-tmp','classroom-teacher-groups.png'),fullPage:true});
  await p.locator('#under-problem').fill('断网后保留的想法');await p.route('**/api/state',route=>route.request().method()==='PUT'?route.abort('failed'):route.continue());await p.locator('#saveUnderstandingNew').click();await p.locator('#saveStateIndicator').filter({hasText:'保存失败'}).waitFor();assert.equal(await p.locator('#under-problem').inputValue(),'断网后保留的想法');await p.unroute('**/api/state');await p.reload();await p.locator('#under-problem').waitFor();assert.equal(await p.locator('#under-problem').inputValue(),'断网后保留的想法');
  const second=await student.newPage();second.on('dialog',d=>d.accept('补充条件'));await second.goto(base+'/student/stage2-understanding.html');await second.locator('#under-problem').fill('另一个窗口的记录');await second.locator('#saveUnderstandingNew').click();await second.getByText('已保存到服务器',{exact:true}).first().waitFor();await p.locator('#saveUnderstandingNew').click();await p.locator('#saveConflictVersion').waitFor();assert.equal(await p.locator('.conflict-content pre').count(),0);await p.locator('#saveConflictVersion').click();await p.getByText('已保存到服务器',{exact:true}).first().waitFor();await second.close();

  assert.deepEqual(errors,[]);console.log('Real API browser acceptance passed: individual versions, modeling, comparison, roles, brief, evaluation, responsive layout.');
 }finally{if(browser)await browser.close();server.stdin.end();await new Promise(resolve=>server.on('exit',resolve))}
})().catch(e=>{console.error(e);process.exitCode=1});
