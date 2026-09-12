/* Browser regression. Run with playwright available on NODE_PATH. No live AI calls. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const os=require('node:os');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const plan=process.env.MAPLE_LIVE_PLAN?JSON.parse(fs.readFileSync(process.env.MAPLE_LIVE_PLAN,'utf8')):{title:'小小采购员',grade:'三年级',mathTopic:'预算与比较',standard:'运用数学解决采购问题',finalProduct:'采购方案报告',projectType:'',allocationBasis:'原文4学时；活动分配为建议，请核对',warnings:['含四年级知识，请审核适用性'],lessons:Array.from({length:4},(_,i)=>({lessonNo:i+1,title:`采购活动${i+1}`,mathFocus:'预算计算',objectives:`目标${i+1}`,activities:'分组讨论与计算',resources:'纸笔与调查表',output:`产出${i+1}`,durationMinutes:40,stageMappings:[Math.min(i+1,5)],sourcePages:[i+3]}))};
const state={className:'七年级测试班',project:'原项目',groupCount:3,groupId:1,currentStage:1,teacherConfig:{mathTopic:'原主题',standard:'原目标',finalProduct:'原成果',knowledge:[],staticHints:['','','']},groups:[],queue:[],common:[]};
(async()=>{
 const server=http.createServer((req,res)=>{const file=path.join(root,new URL(req.url,'http://localhost').pathname);try{res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.html')?'text/html':'image/png');res.end(fs.readFileSync(file))}catch(_){res.writeHead(404);res.end()}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base=`http://127.0.0.1:${server.address().port}`;
 const launch=fs.existsSync(chromium.executablePath())?{}:{channel:'msedge'};
 let browser;
 try{
  browser=await chromium.launch({headless:true,...launch});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  let published={},job={id:'test',name:'采购.pdf',status:'ready',detail:'解析完成，待审核',pages:14,result:plan},publishCalls=0,analyzeCalls=0;
  await page.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url());let result={};
   if(url.pathname==='/api/session')result={authenticated:true,role:'teacher',teacherId:1,groupCount:3};
   else if(url.pathname==='/api/state')result={state};
   else if(url.pathname==='/api/lesson-plan')result={published,job};
   else if(url.pathname==='/api/lesson-plan/analyze'){
    analyzeCalls++;job=analyzeCalls===1?{id:'failed',name:'新教案.docx',status:'failed',detail:'豆包解析超时，原文件已保留',result:null}:{id:'retried',name:'新教案.docx',status:'ready',detail:'解析完成，待审核',pages:0,result:plan};result={id:job.id};
   }
   else if(url.pathname==='/api/lesson-plan/publish'){
    const body=req.postDataJSON();assert.equal(body.reviewed,true);publishCalls++;
    published={plan:body.plan,selectedLesson:body.selectedLesson,revision:publishCalls};
    state.currentLesson={...body.plan.lessons[body.selectedLesson-1],totalLessons:body.plan.lessons.length,revision:publishCalls};result=published;
   }
   await route.fulfill({contentType:'application/json',body:JSON.stringify(result)});
  });
  await page.goto(base+'/teacher/project.html');await page.locator('#planUse').waitFor();
  assert.equal(await page.locator('#pcProject').inputValue(),'原项目');
  await page.locator('#planUse').click();
  assert.equal(await page.locator('#pcProject').inputValue(),'小小采购员');
  assert.equal(await page.locator('#pcClass').inputValue(),'七年级测试班');
  assert.equal(await page.locator('#pcGroupCount').inputValue(),'3');
  assert.equal(await page.locator('#pcLesson option').count(),4);
  assert(await page.locator('#planWarnings').isVisible(),'grade warnings must not be hidden by static-note styles');
  assert((await page.locator('#planWarnings').innerText()).includes('三年级'));
  assert.equal(publishCalls,0);
  await page.locator('#pcLesson').selectOption('2');await page.locator('#lesson-objectives').fill('第二课时人工审核目标');
  await page.locator('#pcLesson').selectOption('1');await page.locator('#pcLesson').selectOption('2');
  assert.equal(await page.locator('#lesson-objectives').inputValue(),'第二课时人工审核目标');
  await page.locator('#saveProject').click();assert.equal(publishCalls,0);
  await page.locator('#planReviewed').check();await page.locator('#saveProject').click();
  await page.waitForFunction(()=>document.querySelector('#planSaveStatus').textContent.includes('已发布'));
  assert.equal(publishCalls,1);assert.equal(published.selectedLesson,2);assert.equal(published.plan.lessons[1].objectives,'第二课时人工审核目标');
  if(plan.sourceActivities?.length){assert.deepEqual(published.plan.sourceActivities,plan.sourceActivities);assert.equal(published.plan.lessons.flatMap(x=>x.activityAllocations).reduce((sum,x)=>sum+x.minutes,0),plan.sourceActivities.reduce((sum,x)=>sum+x.durationMinutes,0))}
  await page.locator('#planFile').setInputFiles({name:'新教案.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:Buffer.from('mock document')});
  await page.locator('#planAnalyze').click();await page.waitForFunction(()=>document.querySelector('#planStatus').textContent.includes('超时'));
  assert.equal(await page.locator('#lesson-objectives').inputValue(),'第二课时人工审核目标');
  assert.equal(publishCalls,1);await page.locator('#planRetry').click();await page.locator('#planUse').waitFor();
  assert.equal(analyzeCalls,2);assert.equal(publishCalls,1);
  const screenshot=path.join(os.tmpdir(),'maple-lesson-plan-desktop.png');await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:screenshot,fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile must not overflow');
  await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(os.tmpdir(),'maple-lesson-plan-mobile.png'),fullPage:true});
  // Student view reads only the published lesson and refreshes without touching input.
  await page.unroute('**/api/**');
  await page.route('**/api/**',async route=>{const url=new URL(route.request().url());let result={};if(url.pathname==='/api/session')result={authenticated:true,role:'student',groupId:1,groupCount:3};else if(url.pathname==='/api/state')result={state};else if(url.pathname==='/api/lesson-plan/current')result={currentLesson:state.currentLesson};await route.fulfill({contentType:'application/json',body:JSON.stringify(result)})});
  await page.goto(base+'/student/workspace.html');await page.locator('#currentLessonBanner').waitFor();
  await page.locator('[data-work="problem"]').fill('尚未保存的学生思路');
  assert((await page.locator('#currentLessonBanner').innerText()).includes('第2课时'));
  state.currentLesson={...plan.lessons[2],totalLessons:4,revision:2};
  await page.waitForFunction(()=>document.querySelector('#currentLessonBanner').innerText.includes('第3课时'),{timeout:8000});
  assert.equal(await page.locator('[data-work="problem"]').inputValue(),'尚未保存的学生思路');
  assert.deepEqual(errors,[]);
  console.log('PASS: preview, no automatic publish, class/group preservation, per-lesson edits, review, publication, responsive layout, student synchronization');
  console.log(screenshot);
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve))}
})().catch(error=>{console.error(error);process.exitCode=1});
