const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require('playwright'),root=path.resolve(__dirname,'..');
(async()=>{
 const server=http.createServer((req,res)=>{
  const file=path.join(root,new URL(req.url,'http://localhost').pathname);
  try{res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.html')?'text/html':'image/png');res.end(fs.readFileSync(file))}catch(_){res.writeHead(404);res.end()}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port;let browser;
 try{
  browser=await chromium.launch({headless:true,...(fs.existsSync(chromium.executablePath())?{}:{channel:'msedge'})});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  let state={project:'确认流程测试',groupCount:1,groupId:1,currentStage:1},pending,requests=0;
  await page.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url());let result={};
   if(url.pathname==='/api/session')result={authenticated:true,role:'student',groupId:1,groupCount:1};
   else if(url.pathname==='/api/state'){
    if(req.method()==='PUT'){requests++;pending={route,state:req.postDataJSON().state};return}
    result={state};
   }
   await route.fulfill({contentType:'application/json',body:JSON.stringify(result)});
  });
  await page.goto(base+'/student/stage1-materials.html');
  for(const id of ['prepGoal','prepMaterials','prepRules'])await page.locator('#'+id).check();
  await page.locator('#savePreparation').click();
  await page.waitForFunction(()=>document.querySelector('#savePreparation').disabled);
  assert(page.url().endsWith('/student/stage1-materials.html'),'must wait for server confirmation');
  await page.waitForRequest(()=>false,{timeout:100}).catch(()=>{});
  assert(pending,'save request sent');
  assert.deepEqual(pending.state.preparationChecks,{goal:true,materials:true,rules:true});
  await page.locator('#savePreparation').evaluate(button=>button.click());
  assert.equal(requests,1,'duplicate clicks must not save twice');
  await pending.route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error:'测试保存失败'})});
  await page.waitForFunction(()=>!document.querySelector('#savePreparation').disabled);
  assert(page.url().endsWith('/student/stage1-materials.html'),'failed save must stay on page');
  assert.match(await page.locator('.toast').textContent(),/保存失败/);
  for(const id of ['prepGoal','prepMaterials','prepRules'])assert(await page.locator('#'+id).isChecked());
  const retry=page.waitForRequest(req=>req.method()==='PUT');
  await page.locator('#savePreparation').click();await retry;
  await new Promise(resolve=>setImmediate(resolve));
  state=pending.state;
  await pending.route.fulfill({contentType:'application/json',body:JSON.stringify({state})});
  await page.waitForFunction(()=>!document.querySelector('#savePreparation').disabled);
  assert(page.url().endsWith('/student/stage1-materials.html'),'successful save must stay on the current task');
  assert.match(await page.locator('.toast').textContent(),/已保存/);
  assert.equal(requests,2,'save queue must recover after failure');
  await page.getByText('进入角色确认',{exact:true}).click();
  await page.waitForURL(base+'/student/stage1-roles.html');
  await page.goto(base+'/student/stage1-materials.html');
  for(const id of ['prepGoal','prepMaterials','prepRules'])assert(await page.locator('#'+id).isChecked(),'saved checks survive navigation');
  assert.deepEqual(errors,[]);
  console.log('PASS: wait for save, prevent duplicate clicks, retain failed checks, retry, explicit next-step navigation, persist confirmation');
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve))}
})().catch(error=>{console.error(error);process.exitCode=1});
