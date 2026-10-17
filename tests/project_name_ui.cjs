const assert=require('node:assert/strict');
const fs=require('node:fs');
const http=require('node:http');
const path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');

(async()=>{
 const types={'.js':'text/javascript','.css':'text/css','.html':'text/html','.png':'image/png','.webp':'image/webp','.woff2':'font/woff2'};
 const server=http.createServer((request,response)=>{
  const file=path.join(root,new URL(request.url,'http://localhost').pathname);
  try{response.setHeader('Content-Type',types[path.extname(file)]||'application/octet-stream');response.end(fs.readFileSync(file))}
  catch(_){response.writeHead(404);response.end()}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 let browser;
 try{
  browser=await chromium.launch({headless:true,...(fs.existsSync(chromium.executablePath())?{}:{channel:'msedge'})});
  const page=await browser.newPage({viewport:{width:1280,height:800}});
  let projectName='原项目名',renameCount=0;
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/**',async route=>{
   const pathname=new URL(route.request().url()).pathname;
   if(pathname==='/api/projects'&&route.request().method()==='POST'){
    const body=route.request().postDataJSON();assert.equal(body.action,'rename');assert.equal(body.projectId,'project-1');projectName=body.name;renameCount++;
    return route.fulfill({contentType:'application/json',body:JSON.stringify({ok:true,renamed:true,projectId:'project-1',projectName,projectLabel:'2026-10-14 - '+projectName})});
   }
   const result=pathname==='/api/session'?{authenticated:true,role:'teacher',username:'教师',teacherId:1,projectId:'project-1',projectName,projectLabel:'2026-10-14 - '+projectName,groupCount:1}:pathname==='/api/state'?{state:{project:projectName,className:'测试班',groupCount:1,groups:[],queue:[],common:[],currentStage:1,aiOpen:true}}:{};
   return route.fulfill({contentType:'application/json',body:JSON.stringify(result)});
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/teacher/dashboard.html`);
  await page.locator('[data-project-menu]').click();
  const input=page.locator('#renameProjectName');await input.waitFor();assert.equal(await input.inputValue(),'原项目名');
  await input.fill('数学探究新项目');await page.getByRole('button',{name:'保存名称'}).click();
  await page.locator('.project-pill-name').getByText('2026-10-14 - 数学探究新项目').waitFor();
  assert.equal(renameCount,1);assert.deepEqual(errors,[]);
  console.log('PASS: teacher can edit the project title from the top bar');
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve))}
})().catch(error=>{console.error(error);process.exitCode=1});
