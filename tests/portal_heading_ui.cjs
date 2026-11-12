const assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const {chromium}=require('playwright');

(async()=>{
 const server=spawn(process.env.TEST_PYTHON||'python',['tests/classroom_ui_server.py'],{stdio:['pipe','pipe','pipe']});
 let browser,logs='';server.stderr.on('data',chunk=>logs+=chunk);
 const base=await new Promise((resolve,reject)=>{server.stdout.on('data',chunk=>{const match=String(chunk).match(/http:\/\/127\.0\.0\.1:\d+/);if(match)resolve(match[0])});server.on('exit',code=>reject(Error(code+logs)))});
 const results=[];
 try{
  browser=await chromium.launch({headless:true,channel:'msedge'});
  const scenarios=[
   {name:'static-html',options:{javaScriptEnabled:false}},
   {name:'anonymous-login',ready:'.auth-login-card'},
   {name:'session-unavailable',endpoint:'session',ready:'.hero'},
   {name:'classes-unavailable',endpoint:'classes',ready:'.hero'},
   {name:'setup',setup:true,ready:'#setupForm'},
   {name:'student-projects',login:{role:'student',groupId:1,studentCode:'9512'},ready:'.project-picker'},
   {name:'teacher-projects',login:{role:'teacher',username:'ui_teacher',password:'ClassroomTeacher#2026'},ready:'.project-picker'},
  ];
  for(const scenario of scenarios){
   const context=await browser.newContext(scenario.options||{}),page=await context.newPage();
   await context.route('https://**',route=>route.abort());
   if(scenario.login)assert((await context.request.post(base+'/api/login',{data:scenario.login})).ok());
   if(scenario.endpoint)await page.route('**/api/'+scenario.endpoint,route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'暂时无法连接'})}));
   if(scenario.setup)await page.route('**/api/session',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({initialized:false,authenticated:false})}));
   const response=await page.goto(base+'/');
   assert.equal(response.status(),200);
   assert((await response.text()).includes('<h1>迈谱数学项目式学习平台</h1>'));
   if(scenario.ready)await page.locator(scenario.ready).waitFor();
   results.push({name:scenario.name,headings:await page.locator('h1').allTextContents(),visible:await page.locator('h1').count()===1&&await page.locator('h1').isVisible()});
   assert.equal(await page.locator('link[rel="canonical"]').getAttribute('href'),'https://www.mapleclass.online/');
   assert(await page.locator('meta[name="description"]').getAttribute('content'));
   assert.equal(await page.locator('.footer-beian a').count(),2);
   assert.equal(await page.locator('.footer-beian a').nth(1).innerText(),'粤公网安备44162602000110号');
   if(scenario.name==='anonymous-login'){
    await page.getByRole('tab',{name:'教师端',exact:true}).click();
    assert.equal(await page.locator('h1').count(),1);
   }
   if(scenario.name.endsWith('projects')){
    for(const width of [1100,720,390]){
     await page.setViewportSize({width,height:900});
     const metrics=await page.locator('.project-picker-head h1').evaluate(el=>({size:parseFloat(getComputedStyle(el).fontSize),width:document.documentElement.scrollWidth,viewport:innerWidth}));
     assert(metrics.size>=24&&metrics.size<=33);assert(metrics.width<=metrics.viewport+2);
    }
   }
   await context.close();
  }
  console.log(JSON.stringify(results));
  assert(results.every(row=>row.visible&&row.headings[0].trim()),'Every portal state must retain one visible H1');
 }finally{if(browser)await browser.close();server.stdin.end()}
})().catch(error=>{console.error(error);process.exitCode=1});
