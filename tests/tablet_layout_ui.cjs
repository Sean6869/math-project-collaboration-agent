const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require('playwright');

const measure=()=>{
 const issues=[],visible=el=>el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden';
 const rect=el=>el.getBoundingClientRect(),header=document.querySelector('.topbar'),bounds=rect(header);
 if(document.documentElement.scrollWidth>innerWidth+2)issues.push('page overflows horizontally');
 const controls=[...header.querySelectorAll('.app-brand,.project-pill,.ai-status,.top-group-badge,.operator-picker,.top-account>.pill,.top-account>.btn')].filter(visible);
 for(const [index,el] of controls.entries()){
  const box=rect(el),name=el.getAttribute('aria-label')||el.innerText;
  if(box.left<bounds.left-1||box.right>bounds.right+1||box.top<bounds.top-1||box.bottom>bounds.bottom+1)issues.push('header control outside header: '+name);
  if(el.scrollWidth>el.clientWidth+2||el.scrollHeight>el.clientHeight+2)issues.push('header content clipped: '+name);
  for(const next of controls.slice(index+1)){
   const other=rect(next);
   if(Math.min(box.right,other.right)-Math.max(box.left,other.left)>2&&Math.min(box.bottom,other.bottom)-Math.max(box.top,other.top)>2)issues.push('header controls overlap: '+name);
  }
 }
 if(innerWidth<=1280)for(const el of document.querySelectorAll('.main .btn')){
  if(visible(el)&&rect(el).height<43)issues.push('touch button too short: '+el.innerText);
 }
 for(const el of document.querySelectorAll('.help-level')){
  const title=el.querySelector('b'),quota=el.querySelector('strong'),box=rect(el);
  if(rect(title).width<130||rect(title).height>100)issues.push('help title squeezed vertically');
  if(rect(quota).right>box.right-8||rect(quota).left<rect(title).right)issues.push('help quota overlaps title');
 }
 for(const el of document.querySelectorAll('.table-wrap')){
  if(el.scrollWidth>el.clientWidth+2&&!['auto','scroll'].includes(getComputedStyle(el).overflowX))issues.push('wide table cannot scroll');
 }
 const sidebar=document.querySelector('.sidebar');
 if(getComputedStyle(sidebar).position==='sticky'&&Math.abs(rect(sidebar).top-bounds.bottom)>2)issues.push('sidebar under header');
 return issues;
};

(async()=>{
 const root=path.resolve(__dirname,'..'),server=spawn(process.env.TEST_PYTHON||'python',['tests/classroom_ui_server.py'],{cwd:root,stdio:['pipe','pipe','pipe']});
 let browser,logs='';server.stderr.on('data',chunk=>logs+=chunk);
 const base=await new Promise((resolve,reject)=>{server.stdout.on('data',chunk=>{const match=String(chunk).match(/http:\/\/127\.0\.0\.1:\d+/);if(match)resolve(match[0])});server.on('exit',code=>reject(Error(code+logs)))});
 const failures=[],errors=[];let checked=0;
 try{
  browser=await chromium.launch({headless:true,channel:'msedge'});
  for(const role of ['student','teacher']){
   const context=await browser.newContext({isMobile:true,hasTouch:true});await context.route('https://**',route=>route.abort());
   const login=role==='student'?{role,classId:'default',groupId:1,studentCode:'9512'}:{role,username:'ui_teacher',password:'ClassroomTeacher#2026'};
   assert((await context.request.post(base+'/api/login',{data:login})).ok());
   const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
   const names=fs.readdirSync(path.join(root,role)).filter(name=>name.endsWith('.html'));
   for(const [width,height] of [[1100,720],[720,1100],[872,805]]){
    await page.setViewportSize({width,height});
    for(const name of names){
     await page.goto(base+'/'+role+'/'+name);await page.locator('.main').waitFor();
     if(name==='project.html')await page.locator('#fieldConfiguration').waitFor();
     const issues=await page.evaluate(measure);checked++;
     if(issues.length)failures.push({role,name,width,issues});
     if(role==='student'&&name==='help.html'){
      assert.equal(await page.locator('.help-level strong').count(),4);
      assert(!(await page.locator('.eyebrow').innerText()).includes('学习学习'));
      fs.mkdirSync(path.join(root,'.test-tmp'),{recursive:true});
      await page.screenshot({path:path.join(root,'.test-tmp',`tablet-help-${width}.png`),fullPage:true});
     }
    }
    console.log(`${role} ${width}x${height}: ${names.length} pages`);
   }
   // Boundary and desktop checks target the controls changed by this fix.
   for(const [width,height] of [[1280,800],[1024,768],[800,1280],[390,844],[1920,1080]]){
    await page.setViewportSize({width,height});
    for(const name of role==='student'?['dashboard.html','help.html']:['dashboard.html','project.html']){
     await page.goto(base+'/'+role+'/'+name);await page.locator('.main').waitFor();
     const issues=await page.evaluate(measure);checked++;if(issues.length)failures.push({role,name,width,issues});
    }
   }
   await context.close();
  }
  fs.writeFileSync(path.join(root,'.test-tmp','tablet-layout-report.json'),JSON.stringify({checked,failures,errors},null,2));
  console.log(JSON.stringify({checked,failures,errors}));assert.deepEqual(failures,[]);assert.deepEqual(errors,[]);
 }finally{if(browser)await browser.close();server.stdin.end()}
})().catch(error=>{console.error(error);process.exitCode=1});
