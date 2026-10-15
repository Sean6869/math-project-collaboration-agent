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
  const teacher=await browser.newContext({viewport:{width:1680,height:945}});
  assert((await teacher.request.post(base+'/api/login',{data:{role:'teacher',username:'state_teacher',password:'StateTeacher#2026'}})).ok());
  const created=await (await teacher.request.post(base+'/api/projects',{data:{action:'create',name:'教师仪表盘测试项目'}})).json();
  let state=(await (await teacher.request.get(base+'/api/state')).json()).state;
  Object.assign(state,{className:'初二（3）班',project:'教师仪表盘测试项目',currentStage:2,common:[{title:'变量关系表达不完整',groups:[1,2],note:'需要补充单位与方向'},{title:'样本范围未说明',groups:[2],note:'核对采集边界'}],queue:[{group:2,type:'模型核验',reason:'需要教师确认约束',priority:'高',resolved:false}]});
  state.groups=state.groups.map((group,index)=>({...group,stage:index===0?2:1,status:index===0?'建模中':'准备中',progress:[68,42,20][index]||0,help:index+1,maxHint:index===0?2:1,teacher:index===1}));
  assert((await teacher.request.put(base+'/api/state',{data:{state}})).ok());
  const page=await teacher.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/teacher/dashboard.html');await page.locator('.teacher-dashboard-grid').waitFor();
  assert.equal(await page.locator('.teacher-group-bar').count(),state.groups.length);assert.equal(await page.locator('.common-chart').count(),1);assert.equal(await page.locator('.teacher-queue-summary').count(),1);assert.equal(await page.locator('.stage-column').count(),5);assert.equal(await page.getByText('切换教学项目',{exact:true}).count(),1);
  for(const viewport of [{width:1680,height:945},{width:1366,height:768},{width:768,height:900},{width:390,height:844}]){await page.setViewportSize(viewport);await page.reload();await page.locator('.teacher-dashboard-grid').waitFor();const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-window.innerWidth);assert(overflow<=2,`teacher dashboard overflows by ${overflow}px at ${viewport.width}`)}
  const teacherShot=process.env.TEACHER_DASHBOARD_QA_SCREENSHOT;if(teacherShot){await page.setViewportSize({width:1680,height:945});await page.reload();await page.locator('.teacher-dashboard-grid').waitFor();await page.screenshot({path:teacherShot,fullPage:true})}
  await page.goto(base+'/?projects');await page.locator('[data-delete-project]').waitFor();assert.equal(await page.locator('[data-delete-project]').count(),1);
  const guest=await browser.newContext({viewport:{width:1366,height:768}}),login=await guest.newPage();login.on('pageerror',error=>errors.push(error.message));await login.goto(base+'/?current');await login.locator('.auth-login-card').waitFor();assert.equal(await login.locator('.auth-brand-lockup').count(),1);await login.getByRole('tab',{name:'教师端'}).click();assert.equal(await login.locator('.auth-login-card').getAttribute('data-auth-role'),'teacher');assert.equal(await login.locator('#teacherAuthForm').isVisible(),true);assert((await login.evaluate(()=>document.documentElement.scrollWidth-window.innerWidth))<=2);
  const loginShot=process.env.LOGIN_QA_SCREENSHOT;if(loginShot)await login.screenshot({path:loginShot,fullPage:true});
  assert.deepEqual(errors,[]);await guest.close();await teacher.close();console.log('PASS: teacher dashboards, role-aware navigation, admin project controls, and responsive login');
 }finally{if(browser)await browser.close();const exited=once(child,'exit');child.stdin.end();await exited}
})().catch(error=>{console.error(error);process.exitCode=1});


