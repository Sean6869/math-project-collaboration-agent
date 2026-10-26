const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require('playwright');
const measureButtons=()=>{
      const list=[],visible=el=>el.getClientRects().length&&el.getBoundingClientRect().width>0&&getComputedStyle(el).visibility!=='hidden';
      if(document.documentElement.scrollWidth>innerWidth+2)list.push({kind:'page-overflow',width:document.documentElement.scrollWidth});
      for(const el of document.querySelectorAll('button,a.btn,label.btn,input[type="submit"]')){
       if(!visible(el))continue;
       const box=el.getBoundingClientRect(),label=(el.innerText||el.getAttribute('aria-label')||el.value||'').trim().slice(0,65);
       if(el.clientWidth>0&&(el.scrollWidth>el.clientWidth+2||el.scrollHeight>el.clientHeight+2))list.push({kind:'text-overflow',label,width:box.width,height:box.height});
       for(let ancestor=el.parentElement;ancestor&&ancestor!==document.body;ancestor=ancestor.parentElement){
        const style=getComputedStyle(ancestor),clip=ancestor.getBoundingClientRect();
        if((['hidden','clip'].includes(style.overflowX)&&(box.left<clip.left-2||box.right>clip.right+2))||(['hidden','clip'].includes(style.overflowY)&&(box.top<clip.top-2||box.bottom>clip.bottom+2))){list.push({kind:'clipped',label,ancestor:ancestor.className||ancestor.id});break}
       }
      }
      return list;
     };
(async()=>{
 const root=path.resolve(__dirname,'..'),server=spawn(process.env.TEST_PYTHON||'python',['tests/classroom_ui_server.py','--camera-ui'],{cwd:root,stdio:['pipe','pipe','pipe']});
 let logs='',browser;server.stderr.on('data',data=>logs+=data);
 const base=await new Promise((resolve,reject)=>{server.stdout.on('data',data=>{const match=String(data).match(/http:\/\/127\.0\.0\.1:\d+/);if(match)resolve(match[0])});server.on('exit',code=>reject(Error(`server ${code}: ${logs}`)))});
 const results=[],errors=[];
 try{
  browser=await chromium.launch({headless:true,...(fs.existsSync(chromium.executablePath())?{}:{channel:'msedge'})});
  for(const role of ['student','teacher']){
   const context=await browser.newContext();await context.route('https://**',route=>route.abort());
   const login=role==='student'?{role,classCode:'CLASSROOM',groupId:1,groupCode:'01'}:{role,username:'ui_teacher',password:'ClassroomTeacher#2026'};
   assert((await context.request.post(base+'/api/login',{data:login})).ok());
   const page=await context.newPage();page.on('pageerror',error=>errors.push({url:page.url(),message:error.message}));
   const pages=process.env.LAYOUT_SCOPE==='ai'?(role==='student'?['ai.html']:[]):fs.readdirSync(path.join(root,role)).filter(name=>name.endsWith('.html'));
   for(const [width,height] of [[1680,945],[1024,768],[768,1024],[390,844],[320,740]]){
    await page.setViewportSize({width,height});
    for(const name of pages){
     await page.goto(`${base}/${role}/${name}`);await page.locator('.main').waitFor();await page.evaluate(()=>document.fonts.ready);
     if(name==='project.html')await page.locator('#resourceFile').waitFor({state:'attached'});
     const issues=await page.evaluate(measureButtons);
     results.push({role,page:name,width,height,issues});
     if(name==='stage4-evidence.html'){
      await page.locator('#addEvidence').click();await page.locator('#commitEvidence').waitFor();
      results.push({role,page:name,width,height,state:'upload-dialog',issues:await page.evaluate(measureButtons)});
      await page.locator('.modal-mask [data-close]').click();
     }
     if(name==='ai.html'){
      await page.screenshot({path:path.join(root,'.test-tmp',`buttons-ai-${width}.png`),fullPage:true});
      const pixel=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64');
      await page.locator('#aiPhotos').setInputFiles([1,2,3].map(n=>({name:`草稿${n}.png`,mimeType:'image/png',buffer:pixel})));
      await page.locator('#aiPrompt').fill('请检查图片中的条件。\n数量、单位与预算需要核对。');
      await page.evaluate(()=>{document.querySelector('#aiPrompt').style.height='180px';document.querySelector('#recognizePhotos').textContent='正在识别……'});
      const geometry=await page.evaluate(()=>{const rect=el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height}};return {input:rect(document.querySelector('#aiPrompt')),send:rect(document.querySelector('#sendAi')),buttons:[...document.querySelectorAll('.ai-composer-actions button')].map(rect)}});
      assert.equal(geometry.buttons.length,5);assert(Math.abs(geometry.send.right-geometry.input.right)<2,'send belongs at the bottom right');
      for(const button of geometry.buttons){assert(button.bottom<=geometry.send.bottom+2);for(const sibling of geometry.buttons)if(Math.abs(button.y-sibling.y)<2)assert(Math.abs(button.height-sibling.height)<2,'each action row must align vertically')}
      results.push({role,page:name,width,height,state:'three-photos-and-expanded-input',issues:await page.evaluate(measureButtons)});
     }
     if(name==='ai.html')await page.screenshot({path:path.join(root,'.test-tmp',`buttons-ai-${width}-photos.png`),fullPage:true});
    }
    await page.goto(base+'/?projects');await page.locator('.project-picker').waitFor();
    results.push({role,page:'project-picker',width,height,issues:await page.evaluate(measureButtons)});
    console.log(`${role} ${width}px: checked ${pages.length} pages`);
   }
   await context.close();
  }
  const anonymous=await browser.newContext(),portal=await anonymous.newPage();
  for(const [width,height] of [[1680,945],[1024,768],[768,1024],[390,844],[320,740]]){
   await portal.setViewportSize({width,height});await portal.goto(base+'/?current');await portal.locator('#studentAuthForm').waitFor();
   results.push({role:'public',page:'student-login',width,height,issues:await portal.evaluate(measureButtons)});
   await portal.getByRole('tab',{name:'教师端',exact:true}).click();
   results.push({role:'public',page:'teacher-login',width,height,issues:await portal.evaluate(measureButtons)});
  }
  await anonymous.close();
  fs.writeFileSync(path.join(root,'.test-tmp','button-layout-report.json'),JSON.stringify({results,errors},null,2));
  const failures=results.filter(result=>result.issues.length);console.log(JSON.stringify({pagesChecked:results.length,failures:failures.slice(0,25),pageErrors:errors}));
  assert.deepEqual(errors,[]);assert.equal(failures.length,0,'Button layout issues: see .test-tmp/button-layout-report.json');
 }finally{if(browser)await browser.close();server.stdin.end();await new Promise(resolve=>server.on('exit',resolve))}
})().catch(error=>{console.error(error.message);process.exitCode=1});
