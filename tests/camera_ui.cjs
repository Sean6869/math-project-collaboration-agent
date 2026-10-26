const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require('playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),server=spawn(process.env.TEST_PYTHON||'python',['tests/classroom_ui_server.py','--camera-ui'],{cwd:root,stdio:['pipe','pipe','pipe']});
 let errors='',browser,diagnosticPage;server.stderr.on('data',data=>errors+=data);
 const base=await new Promise((resolve,reject)=>{server.stdout.on('data',data=>{const match=String(data).match(/http:\/\/127\.0\.0\.1:\d+/);if(match)resolve(match[0])});server.on('exit',code=>reject(Error(`server ${code}: ${errors}`)))});
 try{
  browser=await chromium.launch({headless:true,args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream'],...(fs.existsSync(chromium.executablePath())?{}:{channel:'msedge'})});
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  assert((await context.request.post(base+'/api/login',{data:{role:'student',classId:'default',groupId:1,studentCode:'9512'}})).ok());
  await context.addInitScript(()=>{
   window.cameraRequests=[];window.cameraStreams=[];
   window.cameraCanvases=[];
   navigator.mediaDevices.getUserMedia=async constraints=>{
    window.cameraRequests.push(constraints);
    if(window.denyCamera)throw new DOMException('denied','NotAllowedError');
    const paper=document.createElement('canvas');paper.width=1280;paper.height=720;window.cameraCanvases.push(paper);const stream=paper.captureStream(5),brush=paper.getContext('2d');brush.fillStyle='white';brush.fillRect(0,0,1280,720);brush.fillStyle='black';brush.font='42px sans-serif';brush.fillText('Budget 100 yuan; 8 x 10 = 80',60,120);window.cameraStreams.push(stream);
    if(window.holdCamera)await new Promise(resolve=>window.releaseCamera=resolve);
    return stream;
   };
  });
  const page=await context.newPage(),pageErrors=[];diagnosticPage=page;page.on('pageerror',error=>pageErrors.push(error.message));
  page.on('dialog',dialog=>dialog.accept('补充照片'));
  const stopped=async()=>assert(await page.evaluate(()=>window.cameraStreams.every(stream=>stream.getTracks().every(track=>track.readyState==='ended'))));
  const shoot=async input=>{
   const controls=page.locator('[data-camera-for="'+input+'"]');await controls.getByRole('button',{name:'拍照记录',exact:true}).click();
   await page.locator('[data-camera-shutter]:enabled').waitFor();
   assert.equal(await page.evaluate(()=>document.permissionsPolicy?.allowsFeature('camera')??document.featurePolicy.allowsFeature('camera')),true);
   assert(await page.evaluate(()=>window.cameraRequests[0].video.facingMode.exact==='environment'&&window.cameraRequests.every(request=>request.audio===false)));
   await page.locator('[data-camera-shutter]').click();await page.locator('[data-camera-use]').waitFor({state:'visible'});await stopped();
   if(input==='thoughtPhoto'){await page.locator('[data-camera-retake]').click();await page.locator('[data-camera-shutter]:enabled').waitFor();await page.locator('[data-camera-shutter]').click();await page.locator('[data-camera-use]').waitFor({state:'visible'});await stopped()}
   await page.locator('[data-camera-use]').click();await page.locator('.camera-dialog').waitFor({state:'detached'});
  };
  await page.goto(base+'/student/stage2-understanding.html');await page.locator('#thoughtMode').selectOption('photo');
  await shoot('thoughtPhoto');assert.equal(await page.locator('#thoughtPhoto').evaluate(input=>input.files[0].type),'image/jpeg');
  await page.locator('#saveThoughtOne').click();await page.getByText('已记录1/4位成员的第一轮想法').waitFor();
  let state=(await (await context.request.get(base+'/api/state')).json()).state;
  assert.equal(state.thoughtHistory[0].mode,'photo');assert(state.thoughtHistory[0].fileId);
  const photo=await context.request.get(base+'/api/files/'+state.thoughtHistory[0].fileId);assert(photo.ok());assert.equal(photo.headers()['content-type'],'image/jpeg');assert.equal((await photo.body()).subarray(0,2).toString('hex'),'ffd8');
  await page.goto(base+'/student/stage3-model.html');await page.locator('#modelReason').fill('补充纸笔方案');
  await shoot('modelPhoto');await shoot('modelPhoto');assert.equal(await page.locator('#modelPhoto').evaluate(input=>input.files.length),2);
  await page.locator('#saveModelNew').click();await page.locator('#saveModelNew').filter({hasText:'V3'}).waitFor();
  state=(await (await context.request.get(base+'/api/state')).json()).state;assert.equal(state.workspace.modelFiles.length,2);
  // Closing while camera permission/startup is pending must also release late streams.
  await page.evaluate(()=>{window.holdCamera=true});await page.locator('[data-open-camera]').click();
  await page.waitForFunction(()=>typeof window.releaseCamera==='function');await page.locator('[data-camera-close]').click();
  await page.evaluate(()=>{window.holdCamera=false;window.releaseCamera()});await page.waitForFunction(()=>window.cameraStreams.every(stream=>stream.getTracks().every(track=>track.readyState==='ended')));
  // Denied permission preserves inputs and offers both retry and the system camera.
  await page.locator('#modelReason').fill('保留正在编辑的内容');await page.evaluate(()=>{window.denyCamera=true});await page.locator('[data-open-camera]').click();
  await page.getByText('相机未获授权，请允许相机权限后重试。').waitFor();await page.getByRole('button',{name:'重试',exact:true}).waitFor();
  await page.locator('[data-camera-close]').click();assert.equal(await page.locator('#modelReason').inputValue(),'保留正在编辑的内容');
  await page.goto(base+'/student/stage4-evidence.html');await page.locator('#addEvidence').click();await shoot('newEvidenceFile');
  await page.locator('#evidenceFileName').filter({hasText:'已选择 1 个附件'}).waitFor();await page.locator('#commitEvidence').click();await page.getByRole('heading',{name:'已有证据'}).waitFor();
  await page.waitForFunction(()=>!document.querySelector('#commitEvidence'));
  state=(await (await context.request.get(base+'/api/state')).json()).state;assert.equal(state.evidence.length,1);assert(state.evidence[0].file.id);
  // File selection remains available alongside the camera; OCR is explicit.
  await page.goto(base+'/student/ai.html');await page.locator('#aiPrompt').fill('帮我们核对图中的单位');
  const select=page.waitForEvent('filechooser');await page.locator('[data-select-photo]').click();const fileChooser=await select;
  assert.equal(await fileChooser.element().getAttribute('capture'),null);
  await fileChooser.setFiles({name:'paper.jpg',mimeType:'image/jpeg',buffer:await photo.body()});
  await page.locator('.ai-photo-item img').waitFor();await page.locator('#recognizePhotos').click();await page.locator('#imageTranscript').waitFor();
  assert.equal(await page.locator('#imageTranscript').inputValue(),'预算100元；单价8元；数量10个。');await page.locator('#useTranscript').click();
  assert((await page.locator('#aiPrompt').inputValue()).includes('预算100元'));
  await page.screenshot({path:path.join(root,'.test-tmp','camera-ai-mobile.png'),fullPage:true});
  await page.route('**/api/ai/chat/stream',route=>route.abort('failed'));const originalPrompt=await page.locator('#aiPrompt').inputValue();await page.locator('#sendAi').click();await page.locator('#sendAi:enabled').waitFor();assert.equal(await page.locator('#aiPrompt').inputValue(),originalPrompt);assert.equal(await page.locator('.ai-photo-item img').count(),1);await page.unroute('**/api/ai/chat/stream');
  await page.locator('#sendAi').click();await page.locator('.ai-attached-image').waitFor();
  state=(await (await context.request.get(base+'/api/state')).json()).state;assert(state.freeAi.conversations.at(-1).messages[0].files.length===1);
  await page.reload();await page.locator('.ai-attached-image').waitFor();
  // Native camera fallback uses a single rear-camera capture, even for multi-photo records.
  await page.goto(base+'/student/stage3-model.html');await page.evaluate(()=>Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:undefined}));
  const chosen=page.waitForEvent('filechooser');await page.locator('[data-open-camera]').click();const chooser=await chosen;
  assert.equal(await chooser.element().getAttribute('capture'),'environment');assert.equal(chooser.isMultiple(),false);
  await chooser.setFiles({name:'system-camera.jpg',mimeType:'image/jpeg',buffer:await photo.body()});
  assert.equal(await page.locator('#modelPhoto').evaluate(input=>input.files.length),1);
  await page.screenshot({path:path.join(root,'.test-tmp','camera-mobile.png'),fullPage:true});
  assert((await context.request.post(base+'/api/login',{data:{role:'teacher',username:'ui_teacher',password:'ClassroomTeacher#2026'}})).ok());
  await page.goto(base+'/teacher/project.html');await page.locator('[data-camera-for="resourceFile"]').waitFor();
  const materialPicker=page.waitForEvent('filechooser');await page.locator('#resourceSource .evidence-file-button').click();const materials=await materialPicker;
  assert((await materials.element().getAttribute('accept')).includes('.pdf'));assert.equal(await materials.element().getAttribute('capture'),null);
  await materials.setFiles({name:'paper.jpg',mimeType:'image/jpeg',buffer:await photo.body()});await shoot('resourceFile');
  assert.equal(await page.locator('#resourceFile').evaluate(input=>input.files.length),2);
  await page.locator('#resourceUpload').click();await page.locator('#resourceFileStatus').filter({hasText:''}).waitFor();
  await page.waitForFunction(()=>document.querySelector('#resourceFileStatus').value==='');assert((await page.locator('#resourceList').innerText()).includes('paper.jpg'));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  assert.deepEqual(pageErrors,[]);console.log('Camera browser acceptance passed: rear camera constraints, capture/upload, multiple photos, pending cleanup, denied permission, native fallback.');
 }catch(error){if(diagnosticPage){console.error(await diagnosticPage.locator('body').innerText());await diagnosticPage.screenshot({path:path.join(root,'.test-tmp','camera-failure.png'),fullPage:true})}throw error}finally{if(browser)await browser.close();server.stdin.end();await new Promise(resolve=>server.on('exit',resolve))}
})().catch(error=>{console.error(error);process.exitCode=1});
