const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require('playwright');
const measure=()=>{
 const issues=[];
 for(const header of document.querySelectorAll('.main > .page-head')){
  const actions=header.querySelector(':scope > .actions');
  if([...header.children].some(el=>el.matches('.btn')))issues.push('button outside action group');
  const bounds=header.getBoundingClientRect(),buttons=[...(actions?.querySelectorAll(':scope > .btn')||[])].filter(el=>el.getClientRects().length);
  const boxes=buttons.map(el=>({text:el.innerText,rect:el.getBoundingClientRect()}));
  for(let i=0;i<boxes.length;i++){
   const {text,rect}=boxes[i];
   if(rect.left<bounds.left-2||rect.right>bounds.right+2)issues.push('button overflows heading: '+text);
   const next=boxes[i+1]?.rect;
   if(next&&Math.abs(next.top-rect.top)<2&&next.left-rect.right>20)issues.push('wide button gap: '+text);
  }
 }
 return issues;
};
(async()=>{
 const root=path.resolve(__dirname,'..'),server=spawn(process.env.TEST_PYTHON||'python',['tests/classroom_ui_server.py'],{cwd:root,stdio:['pipe','pipe','pipe']});
 let browser,logs='';server.stderr.on('data',data=>logs+=data);
 const base=await new Promise((resolve,reject)=>{server.stdout.on('data',data=>{const match=String(data).match(/http:\/\/127\.0\.0\.1:\d+/);if(match)resolve(match[0])});server.on('exit',code=>reject(Error(code+logs)))});
 const failures=[],errors=[];let checked=0;
 try{
  browser=await chromium.launch({headless:true,channel:'msedge'});
  for(const role of ['student','teacher']){
   const context=await browser.newContext();await context.route('https://**',route=>route.abort());
   const login=role==='student'?{role,classId:'default',groupId:1,studentCode:'9512'}:{role,username:'ui_teacher',password:'ClassroomTeacher#2026'};
   assert((await context.request.post(base+'/api/login',{data:login})).ok());
   const page=await context.newPage();page.on('pageerror',error=>errors.push({url:page.url(),message:error.message}));
   const names=fs.readdirSync(path.join(root,role)).filter(name=>name.endsWith('.html'));
   for(const width of [1920,1280,1024,768,390]){
    await page.setViewportSize({width,height:900});
    for(const name of names){
     await page.goto(base+'/'+role+'/'+name);await page.locator('.main').waitFor();
     if(name==='project.html')await page.locator('#fieldConfiguration').waitFor();
     const issues=await page.evaluate(measure);checked++;if(issues.length)failures.push({role,name,width,issues});
    }
    console.log(role+' '+width+'px: '+names.length+' pages');
   }
   await context.close();
  }
  console.log(JSON.stringify({checked,failures,errors}));assert.deepEqual(failures,[]);assert.deepEqual(errors,[]);
 }finally{if(browser)await browser.close();server.stdin.end()}
})().catch(error=>{console.error(error);process.exitCode=1});
