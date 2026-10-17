/* Browser regression: metadata, draft preservation, in-page preview and live material sync. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http'),os=require('node:os');
const {chromium}=require('playwright'),root=path.resolve(__dirname,'..');
const fields={title:'项目名称',teachingTeacher:'授课教师',schoolName:'学校名称',designTeacher:'项目设计教师',mainSubject:'主学科',relatedSubjects:'关联学科',grade:'授课年级',textbookEdition:'教材版本',teachingUnit:'教学单元或知识点',lessonSchedule:'课时安排',projectSummary:'项目概要',learningObjectives:'学习目标'};
const project={title:'小小采购员',teachingTeacher:'钟然娇',schoolName:'深圳市南山区第二实验学校',designTeacher:'钟然娇',mainSubject:'数学',relatedSubjects:'科学、信息技术',grade:'三年级',textbookEdition:'北师大版',teachingUnit:'乘与除、认识分数、小数乘法',lessonSchedule:'课内4学时',projectSummary:'围绕如何购买学生奖品最实惠，收集整理数据并比较采购方案。',learningObjectives:'知识：整理分析数据，掌握计算方法。\n能力：综合运用知识解决问题，提高沟通与协作能力。'};
const plan={schemaVersion:2,warnings:['原文含四年级知识，请核对'],lessons:Array.from({length:4},(_,i)=>({projectInfo:{...project,lessonSchedule:'第'+(i+1)+'课时 / 共4课时'}}))};
const state={className:'七年级测试班',project:'原项目',groupCount:3,groupId:1,currentStage:1,teacherConfig:{mathTopic:'原主题',standard:'原目标',finalProduct:'原成果',knowledge:[],staticHints:['','','']},groups:[],queue:[],common:[]};
(async()=>{
 const server=http.createServer((req,res)=>{const file=path.join(root,new URL(req.url,'http://localhost').pathname);try{res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.html')?'text/html':'image/png');res.end(fs.readFileSync(file))}catch(_){res.writeHead(404);res.end()}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const base='http://127.0.0.1:'+server.address().port;let browser;
 try{
  browser=await chromium.launch({headless:true,...(fs.existsSync(chromium.executablePath())?{}:{channel:'msedge'})});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  let published={},resources=[],job={id:'test',name:'采购.docx',status:'ready',detail:'解析完成',result:plan},publishCalls=0,analyzeCalls=0;
  await page.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url());let result={};
   if(url.pathname==='/api/session')result={authenticated:true,role:'teacher',teacherId:1,groupCount:3};
   else if(url.pathname==='/api/state')result={state};
   else if(url.pathname==='/api/lesson-plan')result={published,job,fields,resources};
   else if(url.pathname==='/api/lesson-plan/resources'){
    const body=req.postDataJSON();assert.equal(body.name,'采购清单.txt');const item={id:'resource-1',name:body.name,mime:'text/plain'};resources.unshift(item);result=item;
   }else if(url.pathname==='/api/lesson-plan/source')return route.fulfill({contentType:'text/html',body:'<p>课程支持材料预览正文</p>'});
   else if(url.pathname==='/api/lesson-plan/inspect'){
    const body=req.postDataJSON();assert.equal(body.name,'新教案.docx');job={id:'inspected',name:body.name,status:'inspected',detail:'AI建议4课时：原文明确安排4课时',lessonCount:4,result:null};result={id:job.id};
   }
   else if(url.pathname==='/api/lesson-plan/analyze'){
    analyzeCalls++;assert.equal(req.postDataJSON().lessonCount,analyzeCalls===1?4:3);assert.equal(req.postDataJSON().retryId,analyzeCalls===1?'inspected':'failed');
    job=analyzeCalls===1?{id:'failed',name:'新教案.docx',status:'failed',detail:'解析超时',result:null}:{id:'retried',name:'新教案.docx',status:'ready',detail:'解析完成',result:plan};result={id:job.id};
   }else if(url.pathname==='/api/lesson-plan/publish'){
    const body=req.postDataJSON();assert.equal(body.reviewed,true);assert.equal(body.shareSource,true);assert.deepEqual(body.resourceIds,['resource-1']);publishCalls++;
    published={plan:body.plan,selectedLesson:body.selectedLesson,revision:publishCalls,sourceJobId:body.sourceJobId,supportMaterial:{id:'shared',name:'采购.docx'},supportResources:resources};state.project=body.plan.lessons[body.selectedLesson-1].projectInfo.title;
    state.currentLesson={lessonNo:body.selectedLesson,title:project.title,projectInfo:body.plan.lessons[body.selectedLesson-1].projectInfo,totalLessons:4,revision:publishCalls,supportMaterial:published.supportMaterial,supportMaterials:[published.supportMaterial,...resources]};result=published;
   }
   await route.fulfill({contentType:'application/json',body:JSON.stringify(result)});
  });
  await page.goto(base+'/teacher/project.html');await page.locator('#pcProject').waitFor();
  assert.equal(await page.locator('#pcProject').inputValue(),'小小采购员');assert.equal(await page.locator('#pcClass').inputValue(),'七年级测试班');
  assert.equal(await page.locator('#pcGroupCount').inputValue(),'3');assert.equal(await page.locator('#pcLesson option').count(),4);
  for(const [key,label] of Object.entries(fields)){const field=page.getByLabel(label,{exact:true});assert.equal(await field.count(),1);assert.equal(await field.inputValue(),key==='lessonSchedule'?'第1课时 / 共4课时':project[key])}
  assert.equal(await page.getByText('本课时任务',{exact:true}).count(),0);assert.equal(await page.locator('#planWarnings').count(),0);assert.equal(publishCalls,0);
  assert.equal(await page.locator('#planShare').count(),0);assert.equal(await page.locator('[data-plan-share]').count(),1);assert.equal(await page.locator('[data-plan-share]').isChecked(),true);
  const [planBox,resourceBox,chooseBox,analyzeBox]=await Promise.all([page.locator('#planSource').boundingBox(),page.locator('#resourceSource').boundingBox(),page.locator('label[for="planFile"]+ .file-upload .evidence-file-button').boundingBox(),page.locator('#planAnalyze').boundingBox()]);assert.equal(Math.round(planBox.y),Math.round(resourceBox.y));assert.equal(Math.round(planBox.height),Math.round(resourceBox.height));assert.equal(Math.round(chooseBox.width),Math.round(analyzeBox.width));assert.equal(Math.round(chooseBox.height),Math.round(analyzeBox.height));
  await page.locator('[data-material-preview]').click();await page.frameLocator('.material-preview').getByText('课程支持材料预览正文').waitFor();
  assert(page.url().endsWith('/teacher/project.html'));await page.locator('[data-close]').click();
  await page.locator('#pcLesson').selectOption('2');await page.getByLabel('学习目标',{exact:true}).fill('第二课时人工审核目标');
  await page.locator('#pcLesson').selectOption('1');await page.locator('#pcLesson').selectOption('2');assert.equal(await page.getByLabel('学习目标',{exact:true}).inputValue(),'第二课时人工审核目标');
  await page.reload();await page.locator('#pcProject').waitFor();assert.equal(await page.getByLabel('学习目标',{exact:true}).inputValue(),'第二课时人工审核目标');
  await page.locator('#resourceFile').setInputFiles({name:'采购清单.txt',mimeType:'text/plain',buffer:Buffer.from('预算材料')});await page.locator('#resourceUpload').click();await page.getByText('采购清单.txt',{exact:true}).waitFor();
  await page.locator('#saveProject').click();await page.waitForFunction(()=>document.querySelector('#planSaveStatus').textContent.includes('已发布'));
  assert.match(await page.locator('#planStatus').textContent(),/已发布第2课时/);
  assert.equal(publishCalls,1);assert.equal(published.selectedLesson,2);assert.equal(published.plan.lessons[1].projectInfo.learningObjectives,'第二课时人工审核目标');
  await page.locator('#planFile').setInputFiles({name:'新教案.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:Buffer.from('mock document')});await page.waitForFunction(()=>document.querySelector('#planStatus').textContent.includes('建议4课时'));assert.equal(await page.locator('#planTotal').inputValue(),'4');
  await page.locator('#planAnalyze').click();await page.waitForFunction(()=>document.querySelector('#planStatus').textContent.includes('超时'));
  assert.equal(await page.getByLabel('学习目标',{exact:true}).inputValue(),'第二课时人工审核目标');assert.equal(await page.locator('[data-material-preview]').count(),2);
  assert.equal(await page.locator('#planRetry').count(),0);await page.locator('#planTotal').fill('3');await page.locator('#planAnalyze').click();await page.waitForFunction(()=>document.querySelector('#planSaveStatus').textContent==='已填入，待保存');assert.equal(publishCalls,1);assert.equal(analyzeCalls,2);
  await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(os.tmpdir(),'maple-project-metadata-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile must not overflow');
  await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(os.tmpdir(),'maple-project-metadata-mobile.png'),fullPage:true});
  await page.unroute('**/api/**');
  await page.route('**/api/**',async route=>{
   const url=new URL(route.request().url());let result={};
   if(url.pathname==='/api/session')result={authenticated:true,role:'student',groupId:1,groupCount:3};
   else if(url.pathname==='/api/state')result={state};
   else if(url.pathname==='/api/lesson-plan/current')result={currentLesson:state.currentLesson};
   else if(url.pathname==='/api/lesson-plan/material')return route.fulfill({contentType:'text/html',body:'<p>学生材料正文</p>'});
   await route.fulfill({contentType:'application/json',body:JSON.stringify(result)});
  });
  await page.goto(base+'/student/workspace.html');await page.locator('.material-dock-trigger').waitFor();await page.locator('[data-work="problem"]').fill('尚未保存的学生思路');
  await page.locator('.material-dock-trigger').click();
  await page.locator('[data-material-preview]').first().click();await page.frameLocator('.material-preview').getByText('学生材料正文').waitFor();
  assert(page.url().endsWith('/student/workspace.html'));await page.locator('[data-close]').click();
  state.currentLesson={...state.currentLesson,lessonNo:3,revision:2};
  await page.waitForFunction(()=>document.querySelector('.material-dock-panel')?.textContent.includes('第3课时'),{timeout:8000});
  assert.equal(await page.locator('[data-work="problem"]').inputValue(),'尚未保存的学生思路');
  assert.equal(await page.getByText('查看本课时任务',{exact:true}).count(),0);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'student material must not overflow');
  state.currentLesson={...state.currentLesson,supportMaterial:null,supportMaterials:[],revision:3};
  await page.waitForFunction(()=>!document.querySelector('[data-material-preview]'),{timeout:8000});
  const completeTeacherConfig=state.teacherConfig;state.teacherConfig={mathTopic:'旧版线上状态'};
  await page.goto(base+'/student/stage1-materials.html');await page.locator('#savePreparation').waitFor();
  assert.equal(await page.getByText('平台暂时不可用',{exact:true}).count(),0,'partial legacy state must be normalized');
  state.teacherConfig=completeTeacherConfig;
  // Render every teacher/student entry after the shared UI cleanup.
  await page.unroute('**/api/**');
  let auditRole='student';state.currentStage=5;
  await page.route('**/api/**',async route=>{
   const url=new URL(route.request().url());let result={};
   if(url.pathname==='/api/session')result={authenticated:true,role:auditRole,teacherId:1,groupId:1,groupCount:3,isAdmin:true};
   else if(url.pathname==='/api/state')result={state};
   else if(url.pathname==='/api/lesson-plan')result={published,job,fields,resources};
   else if(url.pathname==='/api/lesson-plan/current')result={currentLesson:state.currentLesson};
   else if(url.pathname==='/api/runtime')result={database:{healthy:true,sizeBytes:1024},data:{events:0,states:1,sessions:1,groups:3,files:0},ai:{configured:false,freeAiConfigured:false,calls:0,failedCalls:0},security:{sessionSeconds:3600},environment:'test'};
   await route.fulfill({contentType:'application/json',body:JSON.stringify(result)});
  });
  // Use realistic reading-length metadata for dashboard layout review.
  state.project='《小小采购员》';state.members=['成员1','成员2','成员3','成员4'];state.membersLocked=true;state.stageSubmitted={1:true};
  state.currentLesson={...state.currentLesson,projectInfo:{...project,
   teachingUnit:'三年级《数据的整理和表示》、四年级下册第三单元《小数乘法》',
   projectSummary:'本课时为方案探究制作阶段，引导学生整理前期收集的奖品偏好、价格等数据，用制表的方法整理数据并进行简单分析，结合奖品价格、折扣活动计算不同采购方案的总价，绘制数据分析对照表，运用列表策略对比筛选出最实惠的奖品购买方案，形成各组的初步购买方案。',
   learningObjectives:'知识目标：学会用制表的方法整理数据并进行简单分析，掌握小数相关的价格计算方法，理解列表策略在优化采购方案中的作用。\n能力目标：能够独立完成多套采购方案的总价计算，通过对比分析筛选出符合预算的最优采购方案，提升数据分析与综合应用知识解决问题的能力。'}};
  let audited=0;
  for(const viewport of [{width:1440,height:1000},{width:900,height:1100},{width:390,height:844}]){
   await page.setViewportSize(viewport);
   for(const role of ['student','teacher']){auditRole=role;for(const file of fs.readdirSync(path.join(root,role)).filter(x=>x.endsWith('.html'))){
    await page.goto(base+'/'+role+'/'+file);await page.locator('#root').waitFor();
    await page.waitForFunction(()=>document.querySelector('#root').innerHTML.length>0);
    assert(!await page.getByText('平台暂时不可用',{exact:true}).count(),role+'/'+file+' must render');
    if(role==='student'&&file==='dashboard.html'){
     assert.equal(await page.locator('.dashboard-project-head h1').textContent(),state.project);
     assert.equal(await page.locator('#projectOverview').count(),0);
     assert((await page.locator('.dashboard-project-head p').textContent()).length<=53);
     assert.equal((await page.locator('.dashboard-task-intro').textContent()).trim(),'和伙伴一起迈出每一步，让想法在探索中变得更清晰！');
     assert.equal(await page.locator('.dashboard-task-card .task small').count(),0);
     assert.equal(await page.locator('.dashboard-status').count(),1);
     assert.equal(await page.locator('.dashboard-status .journey-step').count(),5);
     assert.equal(await page.locator('.journey-step[aria-current="step"]').count(),1);
     assert.equal(await page.locator('.main>.stage-strip,.main>.cols-4').count(),0);
     const layout=await page.evaluate(()=>{
      const status=document.querySelector('.dashboard-status'),task=document.querySelector('.dashboard-task-card');
      return {statusBottom:status.getBoundingClientRect().bottom,taskTop:task.getBoundingClientRect().top,summaryFont:parseFloat(getComputedStyle(document.querySelector('.dashboard-project-head p')).fontSize)};
     });
     assert(layout.statusBottom<=layout.taskTop,'primary task panels must stay below the stage progress');
     assert(layout.summaryFont>=14,'project summary must keep a readable type size');
     if(viewport.width===1440){
      state.stagePaused=true;state.stageTaskStatus=Array.from({length:5},()=>[false,false,false,false]);state.stageTaskStatus[4]=[true,true,false,true];
      await page.waitForFunction(()=>document.querySelector('.dashboard-status')?.classList.contains('is-paused')&&document.querySelector('.gauge-value')?.getAttribute('stroke-dasharray')==='75 100',null,{timeout:12000});
      assert.equal(await page.locator('.live-indicator').evaluate(el=>getComputedStyle(el).animationName),'none');
      state.stagePaused=false;delete state.stageTaskStatus;
      await page.waitForFunction(()=>document.querySelector('.dashboard-status')?.classList.contains('is-running')&&document.querySelector('.gauge-value')?.getAttribute('stroke-dasharray')==='0 100',null,{timeout:12000});
      await page.emulateMedia({reducedMotion:'reduce'});
      assert.equal(await page.locator('.live-indicator').evaluate(el=>getComputedStyle(el).animationName),'none');
      await page.emulateMedia({reducedMotion:'no-preference'});
     }
     const name='maple-student-dashboard-'+viewport.width+'.png';await page.screenshot({path:path.join(os.tmpdir(),name),fullPage:true});
    }
    const typeOutliers=await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(el=>el.offsetParent!==null&&[...el.childNodes].some(node=>node.nodeType===3&&node.textContent.trim())).map(el=>({tag:el.tagName,className:el.className,size:parseFloat(getComputedStyle(el).fontSize)})).filter(item=>item.size<12||item.size>42));
    assert.deepEqual(typeOutliers,[],role+'/'+file+' typography must stay on the readable scale at '+viewport.width);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),role+'/'+file+' must not overflow at '+viewport.width);audited++;
   }}
  }
  assert.deepEqual(errors,[]);
  console.log('Rendered teacher/student pages:',audited);
  console.log('PASS: 12 metadata fields, autosaved drafts, period switching, publication, failure/retry, teacher/student inline preview, live withdrawal, responsive layout');
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve))}
})().catch(error=>{console.error(error);process.exitCode=1});
