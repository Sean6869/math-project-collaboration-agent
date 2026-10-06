const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
(async()=>{
 const server=http.createServer((req,res)=>{
  const pathname=new URL(req.url,'http://localhost').pathname;
  const files={'/assets/data.js':['assets/data.js','text/javascript'],'/teacher/knowledge.html':['teacher/knowledge.html','text/html'], '/assets/app.js':['assets/app.js','text/javascript'], '/assets/styles.css':['assets/styles.css','text/css']};
  const file=files[pathname];if(!file){res.writeHead(404);res.end();return}
  res.setHeader('Content-Type',file[1]+'; charset=utf-8');res.end(fs.readFileSync(path.join(root,file[0])));
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 let browser;
 try{
  browser=await chromium.launch({headless:true,...(fs.existsSync(chromium.executablePath())?{}:{channel:'msedge'})});
  const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  let calls=0,retryCalls=0,fail=false,ready=false;
  await page.route('**/api/**',async route=>{
   const url=new URL(route.request().url());
   let body={};
   if(url.pathname==='/api/session')body={authenticated:true,role:'teacher',isAdmin:true};
   if(url.pathname==='/api/state')body={state:{currentStage:1}};
   if(url.pathname==='/api/knowledge/retry'){retryCalls++;body={ok:true,message:'已开始重新识别未完成资料'};}
   if(url.pathname==='/api/knowledge'){
    calls++;
    if(fail){await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'test unavailable'})});return}
    body={checkedAt:new Date().toISOString(),syncRunning:!ready,summary:{total:1,indexed:ready?1:0,bytes:12},files:[{id:'fixture',name:'扫描教材.pdf',size:12,mime:'application/pdf',uploadedBy:'builtin-knowledge',searchable:ready,statusLabel:ready?'部分内容可供参考':'暂未识别出可用内容',statusDetail:ready?'已完成 1/5 部分；部分识别失败':'已完成 0/5 部分；排队等待识别'}]};
    body.files.push({...body.files[0],id:'short',name:'短文件.pdf',statusLabel:'暂未识别出可用内容',statusDetail:'排队等待识别'});
   }
   await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/teacher/knowledge.html`);
  await page.getByText('暂未识别出可用内容',{exact:true}).first().waitFor();
  await page.getByRole('button',{name:'重新识别未完成资料'}).click();
  assert.equal(retryCalls,1);
  await page.locator('#knowledgeFile').evaluate(node=>node.dataset.retained='yes');
  ready=true;
  await page.getByText('部分内容可供参考',{exact:true}).waitFor({timeout:20000});
  assert(calls>=2);
  assert.equal(await page.locator('#knowledgeFile').getAttribute('data-retained'),'yes');
  assert.equal(await page.getByRole('link',{name:'预览',exact:true}).first().getAttribute('href'),'/api/files/fixture');
  for(const width of [390,768,1280]){
   await page.setViewportSize({width,height:900});
   const lefts=await page.locator('.knowledge-file-status').evaluateAll(nodes=>nodes.map(node=>node.getBoundingClientRect().left));
   assert(Math.abs(lefts[0]-lefts[1])<1,`status columns not aligned at ${width}`);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),`overflow at ${width}`);
  }
  fail=true;
  await page.getByText(/状态刷新失败，下面为上次结果/).waitFor({timeout:30000});
  assert(await page.getByText('部分内容可供参考',{exact:true}).isVisible());
  assert.deepEqual(errors,[]);
  console.log('PASS: live refresh, failure notice, retained upload input, links, mobile/tablet layout');
 }finally{if(browser)await browser.close();server.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
