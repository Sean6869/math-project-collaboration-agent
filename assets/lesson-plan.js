/* Project information is the editable contract; source sharing requires publication. */
(function(){
'use strict';
const copy=x=>JSON.parse(JSON.stringify(x));
function info(value){
 const result={...value};
 result.teachingUnit=value.teachingUnit??value.mathTopic??'';
 result.learningObjectives=value.learningObjectives??value.standard??'';
 return result;
}
function normalize(source){
 const plan=copy(source);plan.schemaVersion=2;
 plan.lessons=plan.lessons.map(l=>({projectInfo:info(l.projectInfo||{...plan,title:plan.title||l.title,mathTopic:l.mathFocus||plan.mathTopic||'',standard:l.objectives||plan.standard||''})}));
 return plan;
}
async function mount({s,api,esc,toast,refreshState,teacherId}){
 const $=id=>document.getElementById(id),snapshot=await api('/api/lesson-plan');
 const fields=snapshot.fields,fieldId=key=>key==='title'?'pcProject':'pc-'+key;
 let revision=snapshot.published.revision||0,job=snapshot.job,selected=snapshot.published.selectedLesson||1,publishedSourceJobId=snapshot.published.sourceJobId||null;
 const initial=info({title:s.project||'',grade:'',mathTopic:s.teacherConfig.mathTopic||'',standard:s.teacherConfig.standard||'',projectType:s.projectType||''});
 let plan=normalize(snapshot.published.plan||{...initial,lessons:[{projectInfo:initial}],warnings:[]});
 let dirty=false,busy=false,inspectedJob=null,appliedJob=snapshot.published.sourceJobId||null,pendingJob=null,sourceJobId=null,shareSource=!!snapshot.published.supportMaterial;
 let resources=Array.isArray(snapshot.resources)?snapshot.resources:[],resourceIds=(snapshot.published.supportResources||[]).map(item=>item.id);
 const cacheKey='mpt:lesson-draft:v2:'+teacherId;
 try{const saved=JSON.parse(sessionStorage.getItem(cacheKey)||'null');if(saved&&saved.revision===revision){plan=normalize(saved.plan);selected=saved.selected;appliedJob=saved.appliedJob;pendingJob=saved.pendingJob;sourceJobId=saved.sourceJobId||null;shareSource=saved.shareSource??shareSource;resourceIds=Array.isArray(saved.resourceIds)?saved.resourceIds:resourceIds;dirty=!!saved.dirty}}catch(_){}
 selected=Math.max(1,Math.min(selected,plan.lessons.length));
 $('projectInfoFields').innerHTML=Object.entries(fields).map(([key,label])=>{
  const multiline=['teachingUnit','projectSummary','learningObjectives'].includes(key);
  return '<div class="field'+(multiline?' span-2':'')+'"><label for="'+fieldId(key)+'">'+esc(label)+'</label>'+(multiline?'<textarea rows="4" id="'+fieldId(key)+'"></textarea>':'<input id="'+fieldId(key)+'"'+(key==='title'?' maxlength="80"':'')+'>')+'</div>';
 }).join('');
 const source=$('planSource');source.innerHTML='<h2>教案智能解析</h2><div class="field"><label for="planTotal">总课时数</label><input id="planTotal" type="number" min="1" max="30"></div><div class="field mt12"><label for="planFile">教案文件（PDF / DOCX，最大10MB）</label><div class="file-upload"><input id="planFileStatus" class="file-name" type="text" readonly placeholder="尚未选择教案"><div class="file-upload-actions"><label class="btn evidence-file-button">选择文件<input id="planFile" type="file" accept=".pdf,.docx" hidden></label><button id="planAnalyze" class="btn primary">解析教案</button></div></div></div><p id="planStatus" role="status" aria-live="polite"></p><div id="planPreview"></div><button class="btn mt12" id="planApply" hidden>填入解析结果</button>';
 const resourceSource=$('resourceSource');resourceSource.innerHTML='<h2>课程支持材料</h2><div class="field"><label for="resourceFile">无需AI解析，可上传多份；勾选后随本课时发布给学生</label><div class="file-upload"><input id="resourceFileStatus" class="file-name" type="text" readonly placeholder="尚未选择材料"><div class="file-upload-actions"><label class="btn evidence-file-button">选择文件<input id="resourceFile" type="file" multiple accept=".pdf,.docx,.txt,.md,.csv,.png,.jpg,.jpeg,.webp" hidden></label><button id="resourceUpload" class="btn primary">上传材料</button></div></div></div><div id="resourceList" class="task-list"></div>';
 $('resourceFile').onchange=()=>{$('resourceFileStatus').value=[...$('resourceFile').files].map(file=>file.name).join('、')};
 $('planTotal').value=plan.lessons.length;
 const template=new URLSearchParams(location.search).get('template');if(['微型项目','标准项目','深度项目'].includes(template)){plan.lessons[selected-1].projectInfo.projectType=template;dirty=true}
 function stash(){try{sessionStorage.setItem(cacheKey,JSON.stringify({revision,plan,selected,appliedJob,pendingJob,sourceJobId,shareSource,resourceIds,dirty}))}catch(_){}}
 function renderResources(){
  $('resourceList').innerHTML=resources.length?resources.map(item=>{const url='/api/lesson-plan/material?id='+encodeURIComponent(item.id),checked=resourceIds.includes(item.id);return '<div class="task resource-item"><input type="checkbox" aria-label="随本课时发布 '+esc(item.name)+'" title="勾选后随本课时发布给学生" data-resource="'+esc(item.id)+'" '+(checked?'checked':'')+'><span><b>'+esc(item.name)+'</b></span><span class="resource-actions"><button type="button" class="btn sm" data-material-preview="'+esc(url)+'">预览</button><a class="btn sm" href="'+esc(url)+'&amp;download=1">下载</a><button type="button" class="btn sm danger" data-resource-delete="'+esc(item.id)+'">删除</button></span></div>'}).join(''):'<div class="empty compact-empty">尚未上传材料</div>';
  document.querySelectorAll('[data-resource]').forEach(input=>input.onchange=()=>{resourceIds=[...document.querySelectorAll('[data-resource]:checked')].map(el=>el.dataset.resource);dirty=true;stash();$('planSaveStatus').textContent='未保存'});
  document.querySelectorAll('[data-resource-delete]').forEach(button=>button.onclick=async()=>{if(!confirm('删除这份课程材料吗？'))return;button.disabled=true;try{await api('/api/lesson-plan/resources/'+encodeURIComponent(button.dataset.resourceDelete),{method:'DELETE'});resources=resources.filter(item=>item.id!==button.dataset.resourceDelete);resourceIds=resourceIds.filter(id=>id!==button.dataset.resourceDelete);dirty=true;stash();renderResources();$('planSaveStatus').textContent='材料待发布';toast('课程材料已删除')}catch(error){button.disabled=false;toast(error.message)}});
 }
 function read(){const value=plan.lessons[selected-1].projectInfo;for(const key of Object.keys(fields))value[key]=$(fieldId(key)).value.trim();value.mathTopic=value.teachingUnit;value.standard=value.learningObjectives}
 function paint(){
  for(const key of Object.keys(fields))$(fieldId(key)).value=plan.lessons[selected-1].projectInfo[key]||'';
  $('pcLesson').innerHTML=plan.lessons.map((_,i)=>'<option value="'+(i+1)+'">第'+(i+1)+'课时 / 共'+plan.lessons.length+'课时</option>').join('');$('pcLesson').value=selected;
 }
 function changed(){read();dirty=true;stash();$('planSaveStatus').textContent='未保存'}
 $('projectCard').querySelectorAll('input,textarea').forEach(el=>el.addEventListener('input',changed));
 $('pcLesson').onchange=()=>{read();selected=Number($('pcLesson').value);paint();dirty=true;stash();$('planSaveStatus').textContent='未保存'};
 $('resourceUpload').onclick=async()=>{
  const files=[...$('resourceFile').files];if(!files.length){toast('请选择课程材料');return}
  if(files.some(file=>!file.size||file.size>10*1024*1024)){toast('每份材料必须为10MB以内的非空文件');return}
  const button=$('resourceUpload');button.disabled=true;button.textContent='正在上传…';
  try{
   for(const file of files){
    const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败'));reader.readAsDataURL(file)});
    const saved=await api('/api/lesson-plan/resources',{method:'POST',body:JSON.stringify({name:file.name,content})});resources.unshift(saved);resourceIds.unshift(saved.id);
   }
   dirty=true;stash();renderResources();$('resourceFile').value='';$('resourceFileStatus').value='';$('planSaveStatus').textContent='材料待发布';toast('课程材料已上传');
  }catch(e){toast(e.message)}finally{button.disabled=false;button.textContent='上传材料'}
 };
 async function inspect(file){
  if(!file)return;
  if(!file.size||file.size>10*1024*1024){toast('请选择10MB以内的非空教案');$('planFile').value='';$('planFileStatus').value='';return}
  busy=true;$('planAnalyze').disabled=true;$('planTotal').disabled=true;$('planStatus').textContent='正在上传教案，AI将先判断合适的总课时数…';
  try{
   const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败'));reader.readAsDataURL(file)});
   const result=await api('/api/lesson-plan/inspect',{method:'POST',body:JSON.stringify({name:file.name,content})});pendingJob=result.id;sourceJobId=result.id;await poll();
  }catch(e){$('planStatus').textContent=e.message;busy=false;$('planAnalyze').disabled=false;$('planTotal').disabled=false}
 }
 $('planFile').onchange=()=>{const file=$('planFile').files[0];$('planFileStatus').value=file?.name||'';if(file)inspect(file)};
 function applyResult(force=false){
  if(job?.status!=='ready'||appliedJob===job.id)return;
  if(dirty&&pendingJob!==job.id&&!force){$('planApply').hidden=false;return}
  plan=normalize(job.result);selected=1;appliedJob=job.id;sourceJobId=job.id;pendingJob=null;shareSource=true;dirty=true;
  $('planTotal').value=plan.lessons.length;$('planApply').hidden=true;paint();stash();$('planSaveStatus').textContent='已填入，待保存';
 }
 $('planApply').onclick=()=>{if(!dirty||confirm('替换当前未保存的项目基本信息？'))applyResult(true)};
 function preview(){
  busy=Boolean(job&&['inspecting','extracting','analyzing'].includes(job.status));
  if(job?.status==='inspected'&&inspectedJob!==job.id){inspectedJob=job.id;$('planTotal').value=job.lessonCount}
  $('planAnalyze').disabled=busy||!job?.id;$('planTotal').disabled=busy;
  const publishedJob=revision>0&&job?.status==='ready'&&publishedSourceJobId===job.id;
  $('planStatus').textContent=publishedJob&&!dirty?'解析完成，已发布第'+selected+'课时':job?.detail||'';
  applyResult();
  const material=job||snapshot.published.supportMaterial;
  const url=material?'/api/lesson-plan/'+(job?'source':'material')+'?id='+encodeURIComponent(material.id):'';
  $('planPreview').innerHTML=material?'<div class="task resource-item"><input type="checkbox" aria-label="随本课时发布 '+esc(material.name)+'" title="勾选后随本课时发布给学生" data-plan-share '+(shareSource?'checked':'')+'><span><b>'+esc(material.name)+'</b></span><span class="resource-actions"><button class="btn sm" data-material-preview="'+esc(url)+'">预览</button><a class="btn sm" href="'+esc(url)+'&amp;download=1">下载</a>'+(job?'<button class="btn sm danger" data-plan-delete="'+esc(job.id)+'">删除</button>':'')+'</span></div><small class="muted">勾选后随本课时发布给学生</small>':'';
  document.querySelector('[data-plan-share]')?.addEventListener('change',event=>{shareSource=event.currentTarget.checked;sourceJobId=job?.id||sourceJobId;dirty=true;stash();$('planSaveStatus').textContent='未保存'});
  document.querySelector('[data-plan-delete]')?.addEventListener('click',async event=>{if(!confirm('删除这份教案吗？'))return;event.currentTarget.disabled=true;try{await api('/api/lesson-plan/source/'+encodeURIComponent(event.currentTarget.dataset.planDelete),{method:'DELETE'});job=null;sourceJobId=null;pendingJob=null;shareSource=false;dirty=true;stash();preview();toast('教案已删除')}catch(error){event.currentTarget.disabled=false;toast(error.message)}});
 }
 async function poll(){if(!$('planStatus'))return;try{job=(await api('/api/lesson-plan')).job;preview();if(busy)setTimeout(poll,2000)}catch(e){$('planStatus').textContent='读取失败：'+e.message;setTimeout(poll,4000)}}
 async function start(){
  if(busy)return;
  const lessonCount=Number($('planTotal').value);
  if(!Number.isInteger(lessonCount)||lessonCount<1||lessonCount>30){toast('总课时数应为1—30');return}
  if(!job?.id){toast('请先选择教案，等待AI给出课时建议');return}
  if(dirty&&!confirm('解析成功后替换备课草稿？已发布信息不变。'))return;
  busy=true;$('planAnalyze').disabled=true;$('planTotal').disabled=true;$('planStatus').textContent='正在按'+lessonCount+'课时解析教案…';
  try{
   const result=await api('/api/lesson-plan/analyze',{method:'POST',body:JSON.stringify({retryId:job.id,lessonCount})});pendingJob=result.id;sourceJobId=result.id;shareSource=true;dirty=true;stash();await poll();
  }catch(e){$('planStatus').textContent=e.message;busy=false;$('planAnalyze').disabled=false;$('planTotal').disabled=false}
 }
 $('planAnalyze').onclick=start;
 $('saveProject').onclick=async()=>{
  read();const count=Number($('pcGroupCount').value);
  if(!Number.isInteger(count)||count<1||count>20){toast('小组数量应为1—20');return}
  if(!plan.lessons[selected-1].projectInfo.title){toast('请填写项目名称');return}
  const projectName=plan.lessons[selected-1].projectInfo.title;
  plan.lessons.forEach(lesson=>{lesson.projectInfo.title=projectName});
  if(count<s.groupCount&&!confirm('减少小组将删除被移除小组的数据，是否继续？'))return;
  const button=$('saveProject');button.disabled=true;let groupChanged=false;
  try{
   if(count!==s.groupCount){const result=await api('/api/groups/configure',{method:'POST',body:JSON.stringify({groupCount:count})});groupChanged=true;s.groupCount=count;if(result.addedCodes?.length)alert('请保存新增访问码：\n'+result.addedCodes.map(x=>x.groupNo+'组：'+x.code).join('\n'))}
   const previousRevision=revision,publishBody={plan,selectedLesson:selected,revision,className:$('pcClass').value.trim(),reviewed:true,sourceJobId,shareSource,resourceIds};
   let saved;
   try{saved=await api('/api/lesson-plan/publish',{method:'POST',body:JSON.stringify(publishBody)})}
   catch(error){
    // The server may commit before a connection drops. Reconcile against the
    // authoritative snapshot so a successful publish is never shown as failed.
    if(error instanceof TypeError||/Failed to fetch|网络|连接/.test(error.message||'')){
     try{const latest=await api('/api/lesson-plan'),published=latest.published||{},info=published.plan?.lessons?.[published.selectedLesson-1]?.projectInfo;if(published.revision>previousRevision&&published.selectedLesson===selected&&info?.title===projectName)saved=published}catch(_){}
    }
    if(!saved)throw error;
   }
   revision=saved.revision;publishedSourceJobId=saved.sourceJobId||sourceJobId||publishedSourceJobId;sourceJobId=null;dirty=false;stash();$('planSaveStatus').textContent='已发布第'+selected+'课时';$('planStatus').textContent='解析完成，已发布第'+selected+'课时';toast('项目配置已保存');await refreshState();if(projectName!==s.project)location.reload();
  }catch(e){$('planSaveStatus').textContent=(groupChanged?'小组已更新，但项目保存失败：':'保存失败：')+e.message}finally{button.disabled=false}
 };
 window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue=''}});
 paint();preview();renderResources();if(revision>0&&!dirty)$('planSaveStatus').textContent='已发布第'+selected+'课时';if(busy)setTimeout(poll,2000);
}
window.MPT_PLAN={mount};
})();
