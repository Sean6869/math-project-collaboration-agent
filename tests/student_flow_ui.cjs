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
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  let state={project:'流程测试',groupCount:1,groupId:1,currentStage:3,members:['甲','乙'],roles:['建模者','质疑者']};
  await page.route('**/api/**',async route=>{
   const request=route.request(),url=new URL(request.url());let result={};
   if(url.pathname==='/api/session')result={authenticated:true,role:'student',groupId:1,groupCount:1};
   else if(url.pathname==='/api/state'){
    if(request.method()==='PUT')state=request.postDataJSON().state;
    result={state};
   }else if(url.pathname==='/api/group/members'){
    const body=request.postDataJSON();
    if(body.action==='save')state={...state,members:body.members,roles:['建模者','质疑者'],membersLocked:false};
    if(body.action==='submit')state={...state,membersLocked:true};
    result={ok:true,state,members:state.members,roles:state.roles,membersLocked:state.membersLocked};
   }
   await route.fulfill({contentType:'application/json',body:JSON.stringify(result)});
  });

  await page.goto(base+'/student/dashboard.html');
  const taskStyles=await page.locator('.task-reading strong').evaluate(el=>{const child=el.querySelector('.reading-total'),a=getComputedStyle(el),b=getComputedStyle(child);return [a.fontSize,b.fontSize,a.color,b.color]});
  assert.equal(taskStyles[0],taskStyles[1]);assert.equal(taskStyles[2],taskStyles[3]);
  const hintStyles=await page.locator('.hint-reading strong').evaluate(el=>{const child=el.querySelector('.reading-total'),a=getComputedStyle(el),b=getComputedStyle(child);return [a.fontSize,b.fontSize,a.color,b.color]});
  assert.equal(hintStyles[0],hintStyles[1]);assert.equal(hintStyles[2],hintStyles[3]);
  const beforeHover=hintStyles[2];await page.locator('.hint-reading').hover();
  assert.equal(await page.locator('.hint-reading strong').evaluate(el=>getComputedStyle(el).color),beforeHover);

  await page.goto(base+'/student/stage3-model.html');
  await page.locator('#m-var').fill('x');await page.locator('#m-rel').fill('y=x²');await page.locator('#m-model').fill('y=x²');await page.locator('#m-range').fill('-2≤x≤2');
  await page.locator('#saveModel').click();await page.waitForURL(base+'/student/stage3-graph.html');
  for(const [id,value] of [['graphA','1'],['graphB','0'],['graphC','0'],['graphMin','-2'],['graphMax','2'],['graphJudge','图像符合范围']])await page.locator('#'+id).fill(value);
  await page.locator('#saveGraphJudge').click();await page.waitForURL(base+'/student/stage3-compare.html');
  for(const input of await page.locator('[data-v]').all())await input.fill('已填写');
  await page.locator('#saveCompare').click();await page.waitForURL(base+'/student/stage3-check.html');
  for(const input of await page.locator('[data-check]').all())await input.check();
  await page.locator('#saveCheck').click();await page.waitForURL(base+'/student/stage3.html');

  state={project:'成员测试',groupCount:1,groupId:1,currentStage:1,members:[],roles:[]};
  await page.goto(base+'/student/stage1-roles.html');
  const names=['小明','小红','小刚','小兰'];
  for(let i=0;i<names.length;i++)await page.locator(`[data-member="${i}"]`).fill(names[i]);
  await page.locator('#saveMembers').click();
  await page.waitForFunction(()=>document.querySelector('#lockMembers')&&!document.querySelector('#lockMembers').disabled);
  assert.deepEqual(await page.locator('[data-member]').allTextContents(),['','','','']);
  assert.deepEqual(await page.locator('[data-member]').evaluateAll(inputs=>inputs.map(input=>input.value)),names);
  assert.deepEqual(errors,[]);
  console.log('PASS: dashboard typography, hover color, stage-3 save flow, and first member save');
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve))}
})().catch(error=>{console.error(error);process.exitCode=1});
