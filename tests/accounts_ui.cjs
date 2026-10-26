const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require('playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),server=spawn(process.env.TEST_PYTHON||'python',['tests/classroom_ui_server.py'],{cwd:root,stdio:['pipe','pipe','pipe']});
 let diagnostics='',browser;server.stderr.on('data',value=>diagnostics+=value);
 const base=await new Promise((resolve,reject)=>{server.stdout.on('data',value=>{const match=String(value).match(/http:\/\/127\.0\.0\.1:\d+/);if(match)resolve(match[0])});server.on('exit',code=>reject(Error(`server ${code}: ${diagnostics}`)))});
 try{
  browser=await chromium.launch({headless:true,...(fs.existsSync(chromium.executablePath())?{}:{channel:'msedge'})});
  const admin=await browser.newContext(),student=await browser.newContext(),teacher=await browser.newContext();
  const a=await admin.newPage(),s=await student.newPage(),t=await teacher.newPage(),errors=[];
  for(const page of [a,s,t])page.on('pageerror',error=>errors.push(error.message));
  await s.goto(base+'/?current');await s.locator('#studentAuthForm').waitFor();
  assert.equal(await s.locator('#classCode').count(),0);assert.equal(await s.locator('#studentAuthForm input').count(),1);
  await s.locator('#groupCode').fill('wrong');await s.locator('#studentAuthForm button[type="submit"]').click();await s.getByText('小组或小组码不正确',{exact:true}).waitFor();
  assert.equal(await s.locator('#groupCode').inputValue(),'');await s.locator('#groupCode').fill('01');await s.locator('#studentAuthForm button[type="submit"]').click();await s.locator('.project-picker').waitFor();
  await a.goto(base+'/?current');await a.getByRole('tab',{name:'教师端',exact:true}).click();await a.locator('#teacherUser').fill('ui_teacher');await a.locator('#teacherPass').fill('ClassroomTeacher#2026');await a.locator('#teacherAuthForm button[type="submit"]').click();await a.getByRole('link',{name:'教师与班级管理',exact:true}).click();
  await a.locator('#managedUsername').fill('new_teacher');await a.locator('#managedPassword').fill('TeacherPassword#2026');await a.locator('#teacherAccountForm button[type="submit"]').click();await a.locator('#managedTeacher option').filter({hasText:'new_teacher'}).waitFor({state:'attached'});
  const teacherId=await a.locator('#managedTeacher option').filter({hasText:'new_teacher'}).getAttribute('value');await a.locator('#managedTeacher').selectOption(teacherId);await a.locator('#viewTeacherPassword').click();await a.waitForFunction(()=>document.querySelector('#managedPassword').type==='text');assert.equal(await a.locator('#managedPassword').inputValue(),'TeacherPassword#2026');
  await a.locator('#managedClassName').fill('三年级二班');await a.locator('#managedClassTeacher').selectOption(teacherId);await a.locator('#managedGroupCount').fill('5');await a.locator('#managedGroupCount').dispatchEvent('change');await a.locator('#managedCode5').waitFor();await a.locator('#classAccountForm button[type="submit"]').click();await a.locator('#managedClass option').filter({hasText:'三年级二班'}).waitFor({state:'attached'});
  const cid=await a.locator('#managedClass').inputValue();assert(cid&&cid!=='default');assert.equal(await a.locator('#managedCode5').inputValue(),'05');
  for(const width of [1680,1024,390,320]){await a.setViewportSize({width,height:945});assert(await a.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await a.screenshot({path:path.join(root,'.test-tmp',`accounts-admin-${width}.png`),fullPage:true})}
  await s.goto(base+'/?current');await s.getByRole('button',{name:'退出登录',exact:true}).click();await s.locator('.class-entry-list').waitFor();
  await s.goto(base+'/?class='+cid);await s.locator('#groupId').selectOption('5');await s.locator('#groupCode').fill('05');await s.locator('#studentAuthForm button[type="submit"]').click();await s.locator('.project-picker').waitFor();assert.equal(await s.locator('[data-project-id]').count(),0);
  const studentState=await student.request.get(base+'/api/session');assert.equal((await studentState.json()).classId,cid);
  await t.goto(base+'/?current');await t.getByRole('tab',{name:'教师端',exact:true}).click();await t.locator('#teacherUser').fill('new_teacher');await t.locator('#teacherPass').fill('TeacherPassword#2026');await t.locator('#teacherAuthForm button[type="submit"]').click();await t.locator('#newProjectName').fill('二班采购项目');await t.locator('#createProjectForm button[type="submit"]').click();await t.locator('.main').waitFor();
  const projects=(await (await teacher.request.get(base+'/api/projects')).json()).projects;assert.equal(projects.length,1);assert.equal(projects[0].name,'二班采购项目');assert.equal((await teacher.request.get(base+'/api/admin/access')).status(),403);
  await a.locator('#managedCode5').fill('55');await a.locator('#classAccountForm button[type="submit"]').click();await a.getByText('班级与小组已保存',{exact:true}).waitFor();assert.equal((await (await student.request.get(base+'/api/session')).json()).authenticated,false);
  await s.goto(base+'/?class='+cid);await s.locator('#groupId').selectOption('5');await s.locator('#groupCode').fill('55');await s.locator('#studentAuthForm button[type="submit"]').click();await s.locator('.project-picker').waitFor();
  assert.deepEqual(errors,[]);console.log('Account browser acceptance passed: two-field login, teacher setup/view, class assignment, editable group codes, scoped project creation and session invalidation.');
 }finally{if(browser)await browser.close();server.stdin.end();await new Promise(resolve=>server.on('exit',resolve))}
})().catch(error=>{console.error(error);process.exitCode=1});
