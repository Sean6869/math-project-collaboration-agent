/* Explicit image recognition and attachments for the existing AI conversation. */
(()=>{
 let dispose=()=>{};
 const close=()=>{dispose();dispose=()=>{}};
 const markup=files=>(files||[]).filter(file=>/^[A-Za-z0-9]+$/.test(file.id)).map(file=>`<a href="/api/files/${file.id}" target="_blank" rel="noopener"><img class="ai-attached-image" src="/api/files/${file.id}" alt="本组上传的图片" loading="lazy"></a>`).join('');
 function mount({api,modal,toast,upload,input}){
  close();const picker=document.querySelector('#aiPhotos'),list=document.querySelector('#aiPhotoList'),recognize=document.querySelector('#recognizePhotos'),cache=new WeakMap();
  let previewUrls=[],busy=false;
  const release=()=>{previewUrls.forEach(URL.revokeObjectURL);previewUrls=[]};dispose=release;
  const draw=()=>{
   release();list.replaceChildren();
   [...picker.files].forEach((file,index)=>{const item=document.createElement('div'),image=document.createElement('img'),remove=document.createElement('button');item.className='ai-photo-item';const url=URL.createObjectURL(file);previewUrls.push(url);image.src=url;image.alt='待发送的图片';remove.type='button';remove.className='btn sm';remove.textContent='移除';remove.disabled=busy;remove.onclick=()=>{const selection=new DataTransfer();[...picker.files].filter((_,i)=>i!==index).forEach(file=>selection.items.add(file));picker.files=selection.files;draw()};item.append(image,remove);list.append(item)});
   recognize.disabled=busy||!picker.files.length;
  };
  const setBusy=value=>{busy=value;document.querySelectorAll('#aiPhotoTools button,#aiPhotoTools input').forEach(element=>element.disabled=value);recognize.disabled=value||!picker.files.length;draw()};
  picker.addEventListener('change',()=>{if(picker.files.length>3){const selection=new DataTransfer();[...picker.files].slice(0,3).forEach(file=>selection.items.add(file));picker.files=selection.files;toast('每次最多选择3张图片')}draw()});
  const uploadImages=async()=>{const files=[...picker.files];if(files.length>3)throw Error('每次最多选择3张图片');const missing=files.filter(file=>!cache.has(file));if(missing.length){const saved=await upload(missing);saved.forEach((result,index)=>cache.set(missing[index],result.id))}return files.map(file=>cache.get(file))};
  recognize.onclick=async()=>{
   if(busy||!picker.files.length)return;setBusy(true);recognize.textContent='正在识别……';
   try{
    const ids=await uploadImages(),result=await api('/api/ai/images/recognize',{method:'POST',body:JSON.stringify({imageFileIds:ids})});
    if(!input.isConnected)return;
    modal('核对识别内容','<div class="field"><label for="imageTranscript">图片中的文字与数据</label><textarea id="imageTranscript" rows="8"></textarea></div><div class="toolbar"><button type="button" class="btn green" id="useTranscript">填入输入框</button></div>');
    document.querySelector('#imageTranscript').value=result.text;document.querySelector('#useTranscript').onclick=()=>{input.value=[input.value.trim(),document.querySelector('#imageTranscript').value.trim()].filter(Boolean).join('\n');input.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#useTranscript').closest('.modal-mask').remove();input.focus()};
   }catch(error){toast(error.message)}finally{if(picker.isConnected){setBusy(false);recognize.textContent='识别图片'}}
  };
  draw();return {upload:uploadImages,setBusy,hasFiles:()=>picker.files.length>0,isBusy:()=>busy};
 }
 window.addEventListener('pagehide',close);window.MPT_IMAGE_CHAT={mount,close,markup};
})();
