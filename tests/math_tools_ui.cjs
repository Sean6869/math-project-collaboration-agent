const assert=require('node:assert/strict'),path=require('node:path');
const {spawn}=require('node:child_process'),{once}=require('node:events'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
(async()=>{
 const child=spawn(process.env.MPT_TEST_PYTHON||'python',['-u','tests/state_ui_server.py'],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,MPT_ENV:'development',MPT_TEACHER_USERNAME:'',MPT_TEACHER_PASSWORD:'',DESMOS_API_KEY:'test'}});
 let browser;
 try{
  const base=await new Promise((resolve,reject)=>{let output='',errors='';child.stderr.on('data',chunk=>errors+=chunk);child.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(match)resolve(match[0])});child.once('exit',()=>reject(Error(errors)))});
  browser=await chromium.launch({headless:true,channel:'msedge'});
  const teacher=await browser.newContext(),student=await browser.newContext();
  assert((await teacher.request.post(base+'/api/login',{data:{role:'teacher',username:'state_teacher',password:'StateTeacher#2026'}})).ok());
  assert((await student.request.post(base+'/api/login',{data:{role:'student',classId:'default',groupId:1,studentCode:'9512'}})).ok());
  const project=await (await teacher.request.post(base+'/api/projects',{data:{action:'create',name:'状态测试项目',school:'测试学校',grade:'七年级',className:'1班',groupCount:12}})).json();
  const teacherPage=await teacher.newPage();await teacherPage.goto(base+'/?projects');assert((await teacherPage.locator('.project-owner-tag').first().textContent()).includes('state_teacher'));await teacherPage.goto(base+'/teacher/project.html');await teacherPage.locator('#pc-projectSummary').fill('浏览器保存验证');const publish=teacherPage.waitForResponse(response=>response.url().endsWith('/api/lesson-plan/publish')&&response.request().method()==='POST');await teacherPage.locator('#saveProject').click();assert((await publish).ok());await teacherPage.reload();assert.equal(await teacherPage.locator('#pc-projectSummary').inputValue(),'浏览器保存验证');await teacherPage.close();
  assert((await teacher.request.post(base+'/api/lesson-plan/publish',{data:{plan:{title:'状态测试项目',lessons:[{title:'状态测试项目'}]},selectedLesson:1,revision:1,className:'测试班',reviewed:true}})).ok());
  assert((await student.request.post(base+'/api/projects',{data:{action:'select',projectId:project.projectId}})).ok());
  const page=await student.newPage();
  const board=await teacher.newPage();await board.goto(base+'/teacher/dashboard.html');await board.locator('.teacher-group-bar').first().waitFor();assert.equal(await board.locator('.teacher-group-bar').count(),12);await board.getByRole('link',{name:'查看详情',exact:true}).first().click();await board.locator('.class-board-card').first().waitFor();assert.equal(await board.locator('.class-board-card').count(),12);
  for(const size of [{width:1440,height:900},{width:1100,height:720},{width:720,height:1100},{width:390,height:844}]){await board.setViewportSize(size);assert(await board.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));assert(await board.locator('.class-board-card').last().isVisible())}
  await board.goto(base+'/teacher/group-situation.html');await board.locator('#groupCards').waitFor();assert.equal(await board.locator('#groupCards article').count(),12);
  await page.goto(base+'/student/class-board.html');await page.locator('.class-board-card').first().waitFor();assert.equal(await page.locator('.class-board-card').count(),12);
  await page.route('https://www.desmos.com/api/v1.12/calculator.js**',route=>route.fulfill({contentType:'application/javascript',body:"window.Desmos={GraphingCalculator:e=>{e.append(document.createElement('div'));return {getState:()=>({}),setState:()=>{},destroy:()=>e.replaceChildren()}}}"}));
  await page.route('https://qilingtools.com/zh/math/graph',route=>route.fulfill({contentType:'text/html; charset=utf-8',body:'<title>函数图像生成器</title><main>函数图像生成器</main>'}));
  await page.goto(base+'/student/dashboard.html');await page.locator('.dashboard-group-identity strong').waitFor();assert.equal(await page.locator('.dashboard-group-identity strong').textContent(),'1组');
  await page.goto(base+'/student/math-tools.html');await page.locator('#desmosCalculator[data-desmos-ready="true"]').waitFor();
  for(const size of [{width:1440,height:900},{width:820,height:1180},{width:390,height:844}]){await page.setViewportSize(size);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));assert.equal(await page.locator('.desmos-tool-switch').evaluate(e=>new Set([...e.children].map(x=>x.offsetTop)).size),1)}
  await page.locator('[data-domestic-graph]').click();await page.frameLocator('.domestic-graph-frame').getByText('函数图像生成器').waitFor();assert(await page.locator('.desmos-host').isHidden());assert(await page.locator('#captureMathLab').isHidden());
  await page.locator('button[data-desmos-tool="graph"]').click();await page.locator('#desmosCalculator[data-desmos-ready="true"]').waitFor();assert(await page.locator('.domestic-graph-frame').isHidden());assert(await page.locator('#captureMathLab').isVisible());
  await page.goto(base+'/student/dashboard.html');
  const controls=await teacher.newPage();await controls.goto(base+'/teacher/stages.html');
  controls.on('dialog',dialog=>dialog.accept());
  const advanced=controls.waitForResponse(response=>response.url().endsWith('/api/classroom/advance')&&response.request().method()==='POST');
  await controls.locator('#advanceClass').click();assert((await advanced).ok());
  await page.waitForFunction(()=>document.querySelector('[data-nav-key="stage-2"]')!==null,null,{timeout:15000});
  assert.equal((await (await student.request.get(base+'/api/state')).json()).state.currentStage,2);
  await controls.close();
  console.log('PASS: group identity, math tool switching and responsive layout');
 }finally{if(browser)await browser.close();const exited=once(child,'exit');child.stdin.end();await exited}
})().catch(error=>{console.error(error);process.exitCode=1});
