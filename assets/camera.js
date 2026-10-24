/* Shared capture flow; photos enter the existing evidence upload pipeline. */
(()=>{
 'use strict';
 let active=null;
 const close=()=>active?.close();
 function nativeCapture(input){
  const picker=document.createElement('input');picker.type='file';picker.accept='image/*';picker.setAttribute('capture','environment');
  picker.hidden=true;document.body.append(picker);
  const remove=()=>picker.remove();
  picker.addEventListener('change',()=>{if(picker.files.length)selectPhotos(input,picker.files);remove()},{once:true});
  picker.addEventListener('cancel',remove,{once:true});picker.click();
 }
 function selectPhotos(input,photos){
  if(!input.isConnected)return;
  const transfer=new DataTransfer();
  if(input.multiple)for(const file of input.files)transfer.items.add(file);
  for(const file of photos){transfer.items.add(file);if(!input.multiple)break}
  input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));
 }
 async function open(input,trigger){
  close();
  if(!navigator.mediaDevices?.getUserMedia||typeof HTMLDialogElement==='undefined'||!HTMLDialogElement.prototype.showModal){nativeCapture(input);return}
  const dialog=document.createElement('dialog');dialog.className='camera-dialog';
  dialog.setAttribute('aria-label','拍照记录');
  dialog.innerHTML='<div class="page-head"><h2>拍照记录</h2><button type="button" class="btn sm" data-camera-close>关闭</button></div><p role="status" data-camera-status>正在打开相机……</p><video autoplay muted playsinline aria-label="相机取景"></video><img hidden alt="刚拍摄的照片"><div class="toolbar"><button type="button" class="btn primary" data-camera-shutter disabled>拍摄</button><button type="button" class="btn" data-camera-retake hidden>重拍</button><button type="button" class="btn green" data-camera-use hidden>使用照片</button><button type="button" class="btn" data-camera-native>使用系统相机</button></div>';
  document.body.append(dialog);
  const video=dialog.querySelector('video'),preview=dialog.querySelector('img'),status=dialog.querySelector('[role="status"]'),shutter=dialog.querySelector('[data-camera-shutter]'),retake=dialog.querySelector('[data-camera-retake]'),use=dialog.querySelector('[data-camera-use]');
  let disposed=false,stream=null,photo=null,previewUrl='',generation=0;
  const stop=()=>{stream?.getTracks().forEach(track=>track.stop());stream=null;video.srcObject=null};
  const release=()=>{if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl='';preview.removeAttribute('src')};
  const finish=()=>{if(disposed)return;disposed=true;generation++;stop();release();observer.disconnect();dialog.close();dialog.remove();active=null;if(trigger.isConnected)trigger.focus()};
  const observer=new MutationObserver(()=>{if(!input.isConnected)finish()});observer.observe(document.body,{childList:true,subtree:true});
  active={close:finish};dialog.addEventListener('cancel',e=>{e.preventDefault();finish()});
  dialog.querySelector('[data-camera-close]').onclick=finish;
  dialog.querySelector('[data-camera-native]').onclick=()=>{finish();nativeCapture(input)};
  const start=async()=>{
   const current=++generation;stop();release();photo=null;preview.hidden=true;video.hidden=false;retake.hidden=true;use.hidden=true;shutter.hidden=false;shutter.disabled=true;status.textContent='正在打开相机……';
   try{
    let acquired;
    try{acquired=await navigator.mediaDevices.getUserMedia({audio:false,video:{facingMode:{exact:'environment'},width:{ideal:1920},height:{ideal:1080}}})}
    catch(error){if(disposed||current!==generation)return;if(!['OverconstrainedError','NotFoundError'].includes(error.name))throw error;acquired=await navigator.mediaDevices.getUserMedia({audio:false,video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}}})}
    if(disposed||current!==generation){acquired.getTracks().forEach(track=>track.stop());return}
    stream=acquired;video.srcObject=stream;await video.play();
    if(disposed||current!==generation)return;
    shutter.disabled=false;status.textContent='对准纸面，拍下清晰的记录。';
   }catch(error){if(disposed||current!==generation)return;stop();video.hidden=true;shutter.hidden=true;retake.hidden=false;retake.textContent='重试';status.textContent=error.name==='NotAllowedError'?'相机未获授权，请允许相机权限后重试。':error.name==='NotFoundError'?'没有找到可用的摄像头。':'相机暂时无法打开，请重试或使用系统相机。'}
  };
  shutter.onclick=async()=>{
   if(!video.videoWidth||!video.videoHeight)return;
   shutter.disabled=true;
   try{
    const canvas=document.createElement('canvas'),scale=Math.min(1,1920/Math.max(video.videoWidth,video.videoHeight));
    canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);
    canvas.getContext('2d',{alpha:false}).drawImage(video,0,0,canvas.width,canvas.height);
    const blob=await new Promise((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(Error('capture')),'image/jpeg',.9));
    if(disposed)return;
    photo=new File([blob],`纸笔记录-${Date.now()}.jpg`,{type:'image/jpeg'});previewUrl=URL.createObjectURL(blob);preview.src=previewUrl;preview.hidden=false;video.hidden=true;stop();shutter.hidden=true;retake.hidden=false;retake.textContent='重拍';use.hidden=false;status.textContent='检查照片是否清晰。';
   }catch(_){if(!disposed){shutter.disabled=false;status.textContent='没有拍摄成功，请重试。'}}
  };
  retake.onclick=start;use.onclick=()=>{if(photo){selectPhotos(input,[photo]);finish()}};
  dialog.showModal();await start();
 }
 function install(root=document){
  root.querySelectorAll('input[type="file"]').forEach(input=>{
   if(!/image\/|\.(?:png|jpe?g|webp|gif|heic|heif)(?:,|$)/i.test(input.accept))return;
   if(input.dataset.cameraReady)return;input.dataset.cameraReady='true';const imagesOnly=input.accept.split(',').every(type=>/^(image\/|\.(png|jpe?g|webp|gif|heic|heif)$)/i.test(type.trim()));if(imagesOnly)input.hidden=true;
   const controls=document.createElement('div');controls.className='camera-controls';
   controls.dataset.cameraFor=input.id;controls.innerHTML='<button type="button" class="btn primary" data-open-camera>拍照记录</button>'+(imagesOnly?'<button type="button" class="btn" data-select-photo>选择图片</button>':'')+'<span class="muted small" role="status"></span>';
   (input.closest('label')||input).after(controls);
   controls.querySelector('[data-open-camera]').onclick=e=>open(input,e.currentTarget);
   if(imagesOnly)controls.querySelector('[data-select-photo]').onclick=()=>{const picker=document.createElement('input');picker.type='file';picker.accept='image/*';picker.multiple=input.multiple;picker.hidden=true;document.body.append(picker);picker.onchange=()=>{if(picker.files.length)selectPhotos(input,picker.files);picker.remove()};picker.oncancel=()=>picker.remove();picker.click()};
   input.addEventListener('change',()=>{const status=controls.querySelector('[role="status"]');status.textContent=input.files.length?`已选择 ${input.files.length} ${imagesOnly?'张图片':'个附件'}`:''});
  });
 }
 window.addEventListener('pagehide',close);document.addEventListener('visibilitychange',()=>{if(document.hidden)close()});
 window.MPT_CAMERA={install,close};
})();
