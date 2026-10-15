const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require('playwright'),root=path.resolve(__dirname,'..');

(async()=>{
 const css=fs.readFileSync(path.join(root,'assets','styles.css'),'utf8');
 assert(css.includes('--space-1:4px;--space-2:8px;--space-3:16px;--space-4:24px'),'the UI must use the shared four-step spacing scale');
 assert(css.includes('--radius-inner:8px;--radius:24px'),'outer card radius must equal inner radius plus the 16px card spacing');
 assert(css.includes('--ink:#1a1a1a'),'body copy must use the approved softened black');
 const types={'.js':'text/javascript','.css':'text/css','.html':'text/html','.png':'image/png','.webp':'image/webp','.woff':'font/woff','.woff2':'font/woff2'};
 const server=http.createServer((req,res)=>{const file=path.join(root,new URL(req.url,'http://localhost').pathname);try{res.setHeader('Content-Type',types[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file))}catch(_){res.writeHead(404);res.end()}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port;let browser;
 try{
  browser=await chromium.launch({headless:true,...(fs.existsSync(chromium.executablePath())?{}:{channel:'msedge'})});
  const page=await browser.newPage({viewport:{width:1680,height:945},deviceScaleFactor:1});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const state={project:'跨学科数学探究项目',className:'初一（3）班',groupCount:3,groupId:2,currentStage:1,aiOpen:true,members:['张明','李华','王芳','陈强','刘悦'],roles:['协调者','资料员','建模者','记录者','展示者'],membersLocked:true,preparationChecks:{goal:true,materials:false,rules:false},stageSubmitted:{1:false,2:false,3:false,4:false,5:false},hintPolicy:{L1:20,L2:10,L3:5,L4:3},hintUsage:{L1:0,L2:0,L3:0,L4:0},events:[{ts:new Date().toISOString(),type:'preparation_confirm',actor:'张明'},{ts:new Date().toISOString(),type:'members_locked',actor:'李华'}],currentLesson:{projectInfo:{projectSummary:'用数学的眼光发现真实问题，用数据和模型提出可行方案。'},supportMaterials:[{id:'pdf-1',name:'项目背景资料.pdf',mime:'application/pdf'},{id:'doc-1',name:'项目计划书模板.md',mime:'text/markdown'}]}};
  await page.route('**/api/**',async route=>{const pathname=new URL(route.request().url()).pathname;const result=pathname==='/api/session'?{authenticated:true,role:'student',groupId:2,groupCount:3,projectLabel:'2026-10-14 - 跨学科数学探究项目'}:{state};await route.fulfill({contentType:'application/json',body:JSON.stringify(result)})});
  await page.goto(base+'/student/dashboard.html');await page.locator('.dashboard-hero-art').waitFor();
  const metrics=await page.evaluate(()=>{const box=selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom}};const roleRows=[...document.querySelectorAll('.dashboard-role-row')].map(row=>box('.dashboard-role-row:nth-child('+([...row.parentElement.children].indexOf(row)+1)+')'));const typeItems=[...document.querySelectorAll('.student-dashboard-grid *')].filter(el=>el.offsetParent!==null&&!el.classList.contains('bi')&&[...el.childNodes].some(node=>node.nodeType===3&&node.textContent.trim())).map(el=>({size:Math.round(parseFloat(getComputedStyle(el).fontSize)),tag:el.tagName,className:el.className}));const typeSizes=typeItems.map(item=>item.size).filter((size,index,list)=>list.indexOf(size)===index).sort((a,b)=>a-b);const typeOutliers=typeItems.filter(item=>![14,16,24,32].includes(item.size));return {viewport:innerHeight,scroll:document.documentElement.scrollHeight,primary:box('.dashboard-primary'),secondary:box('.dashboard-secondary'),status:box('.dashboard-status'),task:box('.dashboard-task-card'),role:box('.dashboard-role-card'),progress:box('.dashboard-progress-card'),activity:box('.dashboard-activity-card'),main:box('.main'),font:getComputedStyle(document.body).fontFamily,heroLoaded:document.querySelector('.dashboard-hero-art').complete&&document.querySelector('.dashboard-hero-art').naturalWidth>0,navCount:document.querySelectorAll('.dashboard-nav>a').length,roleRows,typeSizes,typeOutliers}});
  console.log(JSON.stringify(metrics));
  assert(metrics.heroLoaded,'the universal dashboard illustration must load');
  assert.equal(metrics.navCount,8,'the dashboard must show the eight reference navigation entries');
  assert(Math.abs(metrics.task.height-metrics.role.height)<1,'the two primary cards must share one height');
  assert.equal(metrics.roleRows.length,5,'all five group roles must remain visible');
  assert(metrics.roleRows.every(row=>row.bottom<=metrics.role.bottom+1),'group role rows must not be clipped by the card');
  assert(Math.abs(metrics.progress.height-metrics.activity.height)<1,'the two lower cards must share one height');
  assert(metrics.status.height>=78&&metrics.status.height<=100,'the learning journey must keep the compact reference height');
  assert(metrics.progress.bottom<=metrics.viewport,'the full dashboard must fit in the reference viewport without scrolling');
  assert(metrics.scroll<=metrics.viewport+1,'the reference viewport must not have page-level vertical overflow');
  assert(metrics.primary.width/metrics.main.width>.65&&metrics.primary.width/metrics.main.width<.68,'the primary and assistant columns must keep the reference proportion');
  assert(metrics.font.includes('Microsoft YaHei'),'the interface must use Microsoft YaHei');
  assert(metrics.typeSizes.every(size=>[14,16,24,32].includes(size)),'dashboard typography must use only 14, 16, 24, or 32px');
  const out=process.env.DASHBOARD_QA_SCREENSHOT;if(out)await page.screenshot({path:out,fullPage:true});
  for(const viewport of [{width:1366,height:768},{width:768,height:900},{width:390,height:844}]){await page.setViewportSize(viewport);await page.reload();await page.locator('.dashboard-status').waitFor();const overflow=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth,task:document.querySelector('.dashboard-task-card').scrollWidth,taskBox:document.querySelector('.dashboard-task-card').clientWidth}));assert(overflow.page<=overflow.viewport+1,`dashboard must not overflow horizontally at ${viewport.width}px`);assert(overflow.task<=overflow.taskBox+1,`task card must not clip horizontally at ${viewport.width}px`)}
  assert.deepEqual(errors,[]);
  console.log('PASS: dashboard matches the reference proportions and fits one 1680x945 viewport');
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve))}
})().catch(error=>{console.error(error);process.exitCode=1});
