const assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const {chromium}=require('playwright');

(async()=>{
 const server=spawn(process.env.TEST_PYTHON||'python',['tests/classroom_ui_server.py'],{stdio:['pipe','pipe','pipe']});
 let browser,logs='';server.stderr.on('data',chunk=>logs+=chunk);
 const base=await new Promise((resolve,reject)=>{server.stdout.on('data',chunk=>{const match=String(chunk).match(/http:\/\/127\.0\.0\.1:\d+/);if(match)resolve(match[0])});server.on('exit',code=>reject(Error(code+logs)))});
 try{
  browser=await chromium.launch({headless:true,channel:'msedge'});
  const student=await browser.newContext(),teacher=await browser.newContext();
  await student.route('https://**',route=>route.abort());
  assert((await student.request.post(base+'/api/login',{data:{role:'student',classId:'default',groupId:1,studentCode:'9512'}})).ok());
  assert((await teacher.request.post(base+'/api/login',{data:{role:'teacher',username:'ui_teacher',password:'ClassroomTeacher#2026'}})).ok());
  const page=await student.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const save=async(button,endpoint='/api/state')=>{const response=page.waitForResponse(response=>response.url().endsWith(endpoint)&&['POST','PUT'].includes(response.request().method()));await button.click();const result=await response;assert.equal(result.status(),200,await result.text());return result.json()};

  await page.goto(base+'/student/help.html');
  await page.locator('[data-help-level="1"] strong').waitFor();
  assert.equal(await page.locator('[data-help-level="1"] strong').innerText(),'0/20');
  assert.equal(await page.locator('[data-help-level="2"] strong').innerText(),'0/10');
  assert.equal(await page.locator('[data-help-level="3"] strong').innerText(),'0/5');
  assert.equal(await page.locator('[data-help-level="4"] strong').innerText(),'0/2');

  await page.goto(base+'/student/stage2-understanding.html');
  await page.locator('#under-problem').fill('尚未提交的小组讨论结果');
  await page.locator('#thoughtContent').fill('先检查单位');
  await save(page.locator('#saveThoughtOne'),'/api/classroom/thought');
  await page.waitForFunction(()=>document.querySelector('#thoughtContent')?.value==='');
  assert.equal(await page.locator('#under-problem').inputValue(),'尚未提交的小组讨论结果');
  await page.reload();await page.locator('#under-problem').waitFor();
  assert.equal(await page.locator('#under-problem').inputValue(),'尚未提交的小组讨论结果');
  const groupSave=await save(page.locator('#saveUnderstandingNew'));
  assert.equal(groupSave.state.workspace.problem,'尚未提交的小组讨论结果');

  const teacherState=(await (await teacher.request.get(base+'/api/state')).json()).state;
  assert((await teacher.request.post(base+'/api/classroom/config',{data:{revision:teacherState._classRevision,fieldRules:{'compare-claim':{hint:'请帮助检查方案'},'rehearsal-check-0':{title:'上台前检查第1项'},'rehearsal-check-1':{title:'核对本班展示单位'}}}})).ok());
  await page.goto(base+'/student/stage3-compare.html?stage=4');
  await page.locator('[data-field-help="compare-0-claim"]').click();await page.locator('#aiPrompt').waitFor();
  assert((await page.locator('#aiPrompt').inputValue()).includes('阶段：4'));
  await page.goto(base+'/student/stage3-compare.html?stage=4');
  await page.locator('#compare-0-claim').fill('第一种测量方案');
  await page.locator('#compare-0-basis').fill('测量三次');
  await page.locator('#compare-1-claim').fill('第二种测量方案');
  await page.locator('#compare-1-basis').fill('计算结果一致');
  const compared=await save(page.locator('#saveCompareNew'));
  assert.equal(compared.state.fieldHistory.filter(row=>row.fieldId==='compare-claim').at(-1).stage,4);

  await page.goto(base+'/student/stage5-rehearsal.html');
  await page.locator('[data-rehearsal-check="0"]').waitFor();
  assert((await page.locator('[data-rehearsal-check="0"]').locator('..').innerText()).includes('每位成员知道自己讲什么'));
  assert((await page.locator('[data-rehearsal-check="1"]').locator('..').innerText()).includes('核对本班展示单位'));
  await page.locator('[data-rehearsal-role="0"]').fill('说明图表');
  await page.locator('#issueProblem').fill('图表没有单位');
  await page.locator('#issueSolution').fill('准备补充单位');
  const created=await save(page.locator('#addRehearsalIssue'));
  const issue=created.state.product.rehearsalIssues[0];
  assert.equal(created.state.product.rehearsal.split('\n').length,2);
  await page.locator('[data-recheck-rehearsal-issue="0"]').check();
  const rechecked=await save(page.locator('#saveRehearsal'));
  assert.equal(rechecked.state.product.rehearsalIssues[0].id,issue.id);
  assert.equal(rechecked.state.product.rehearsalIssues[0].rechecked,true);
  await page.locator('[data-edit-rehearsal-issue="0"]').click();
  await page.reload();await page.locator('#issueSolution').waitFor();
  assert.equal(await page.locator('#rehearsalEditingIssue').inputValue(),issue.id);
  await page.locator('#issueSolution').fill('已补充单位并重新核对');
  const edited=await save(page.locator('#addRehearsalIssue'));
  assert.equal(edited.state.product.rehearsalIssues.length,1);
  assert.equal(edited.state.product.rehearsalIssues[0].id,issue.id);
  assert.equal(edited.state.product.rehearsalIssues[0].time,issue.time);
  assert.equal(edited.state.product.rehearsalIssues[0].solution,'已补充单位并重新核对');
  assert.equal(edited.state.productHistory.at(-1).product.rehearsalIssues[0].rechecked,true);
  assert.deepEqual(errors,[]);
  console.log('DBR root-cause browser checks passed');
 }finally{if(browser)await browser.close();server.stdin.end()}
})().catch(error=>{console.error(error);process.exitCode=1});
