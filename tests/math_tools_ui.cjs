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
  const project=await (await teacher.request.post(base+'/api/projects',{data:{action:'create',name:'状态测试项目'}})).json();
  assert((await teacher.request.post(base+'/api/lesson-plan/publish',{data:{plan:{title:'状态测试项目',lessons:[{title:'状态测试项目'}]},selectedLesson:1,revision:0,className:'测试班',reviewed:true}})).ok());
  assert((await student.request.post(base+'/api/projects',{data:{action:'select',projectId:project.projectId}})).ok());
  const page=await student.newPage();
  await page.route('https://www.desmos.com/api/v1.12/calculator.js**',route=>route.fulfill({contentType:'application/javascript',body:"window.Desmos={GraphingCalculator:e=>{e.append(document.createElement('div'));return {getState:()=>({}),setState:()=>{},destroy:()=>e.replaceChildren()}}}"}));
  await page.route('https://qilingtools.com/zh/math/graph',route=>route.fulfill({contentType:'text/html; charset=utf-8',body:'<title>函数图像生成器</title><main>函数图像生成器</main>'}));
  await page.goto(base+'/student/dashboard.html');await page.locator('.dashboard-group-identity strong').waitFor();assert.equal(await page.locator('.dashboard-group-identity strong').textContent(),'1组');
  await page.goto(base+'/student/math-tools.html');await page.locator('#desmosCalculator[data-desmos-ready="true"]').waitFor();
  for(const size of [{width:1440,height:900},{width:820,height:1180},{width:390,height:844}]){await page.setViewportSize(size);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));assert.equal(await page.locator('.desmos-tool-switch').evaluate(e=>new Set([...e.children].map(x=>x.offsetTop)).size),1)}
  await page.locator('[data-domestic-graph]').click();await page.frameLocator('.domestic-graph-frame').getByText('函数图像生成器').waitFor();assert(await page.locator('.desmos-host').isHidden());assert(await page.locator('#captureMathLab').isHidden());
  await page.locator('button[data-desmos-tool="graph"]').click();await page.locator('#desmosCalculator[data-desmos-ready="true"]').waitFor();assert(await page.locator('.domestic-graph-frame').isHidden());assert(await page.locator('#captureMathLab').isVisible());
  console.log('PASS: group identity, math tool switching and responsive layout');
 }finally{if(browser)await browser.close();const exited=once(child,'exit');child.stdin.end();await exited}
})().catch(error=>{console.error(error);process.exitCode=1});
