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
 let revision=snapshot.published.revision||0,job=snapshot.job,selected=snapshot.published.selectedLesson||1;
 const initial=info({title:s.project||'',grade:'',mathTopic:s.teacherConfig.mathTopic||'',standard:s.teacherConfig.standard||'',projectType:s.projectType||''});
 let plan=normalize(snapshot.published.plan||{...initial,lessons:[{projectInfo:initial}],warnings:[]});
 let dirty=false,busy=false,appliedJob=snapshot.published.sourceJobId||null,pendingJob=null,sourceJobId=null,shareSource=!!snapshot.published.supportMaterial;
 const cacheKey='mpt:lesson-draft:v2:'+teacherId;
 try{const saved=JSON.parse(sessionStorage.getItem(cacheKey)||'null');if(saved&&saved.revision===revision){plan=normalize(saved.plan);selected=saved.selected;appliedJob=saved.appliedJob;pendingJob=saved.pendingJob;sourceJobId=saved.sourceJobId||null;shareSource=saved.shareSource??shareSource;dirty=!!saved.dirty}}catch(_){}
 selected=Math.max(1,Math.min(selected,plan.lessons.length));
 $('projectInfoFields').innerHTML=Object.entries(fields).map(([key,label])=>{
  const multiline=['teachingUnit','projectSummary','learningObjectives'].includes(key);
  return '<div class="field'+(multiline?' span-2':'')+'"><label for="'+fieldId(key)+'">'+esc(label)+'</label>'+(multiline?'<textarea rows="4" id="'+fieldId(key)+'"></textarea>':'<input id="'+fieldId(key)+'">')+'</div>';
 }).join('');
 const source=$('planSource');source.innerHTML='<h2>课程支持材料</h2><div class="field"><label for="planTotal">总课时数</label><input id="planTotal" type="number" min="1" max="30"></div><div class="field mt12"><label for="planFile">上传教案（PDF / DOCX，最大10MB）</label><input id="planFile" type="file" accept=".pdf,.docx"></div><div class="toolbar"><button id="planAnalyze" class="btn primary">上传并解析</button><button id="planRetry" class="btn" hidden>重新解析</button></div><p id="planStatus" role="status" aria-live="polite"></p><div id="planPreview"></div><label class="lesson-review"><input type="checkbox" id="planShare">保存时向学生提供此文件</label><div id="planWarnings" class="lesson-warning" role="status"></div><button class="btn mt12" id="planApply" hidden>填入解析结果</button>';
 $('planTotal').value=plan.lessons.length;
 const template=new URLSearchParams(location.search).get('template');if(['微型项目','标准项目','深度项目'].includes(template)){plan.lessons[selected-1].projectInfo.projectType=template;dirty=true}
 function stash(){try{sessionStorage.setItem(cacheKey,JSON.stringify({revision,plan,selected,appliedJob,pendingJob,sourceJobId,shareSource,dirty}))}catch(_){}}
 function read(){const value=plan.lessons[selected-1].projectInfo;for(const key of Object.keys(fields))value[key]=$(fieldId(key)).value.trim();value.mathTopic=value.teachingUnit;value.standard=value.learningObjectives}
 function paint(){
  for(const key of Object.keys(fields))$(fieldId(key)).value=plan.lessons[selected-1].projectInfo[key]||'';
  $('pcLesson').innerHTML=plan.lessons.map((_,i)=>'<option value="'+(i+1)+'">第'+(i+1)+'课时 / 共'+plan.lessons.length+'课时</option>').join('');$('pcLesson').value=selected;
  $('planWarnings').textContent=(plan.warnings||[]).join('\n');$('planWarnings').hidden=!plan.warnings?.length;
  $('planShare').checked=shareSource;
 }
 function changed(){read();dirty=true;stash();$('planSaveStatus').textContent='未保存'}
 $('projectCard').querySelectorAll('input,textarea').forEach(el=>el.addEventListener('input',changed));
 $('pcLesson').onchange=()=>{read();selected=Number($('pcLesson').value);paint();dirty=true;stash();$('planSaveStatus').textContent='未保存'};
 $('planShare').onchange=()=>{shareSource=$('planShare').checked;sourceJobId=job?.id||sourceJobId;dirty=true;stash()};
 function applyResult(force=false){
  if(job?.status!=='ready'||appliedJob===job.id)return;
  if(dirty&&pendingJob!==job.id&&!force){$('planApply').hidden=false;return}
  plan=normalize(job.result);selected=1;appliedJob=job.id;sourceJobId=job.id;pendingJob=null;shareSource=true;dirty=true;
  $('planTotal').value=plan.lessons.length;$('planApply').hidden=true;paint();stash();$('planSaveStatus').textContent='已填入，待保存';
 }
 $('planApply').onclick=()=>{if(!dirty||confirm('替换当前未保存的项目基本信息？'))applyResult(true)};
 function preview(){
  busy=Boolean(job&&['extracting','analyzing'].includes(job.status));
  $('planAnalyze').disabled=busy;$('planRetry').hidden=!job;$('planRetry').disabled=busy;$('planTotal').disabled=busy;
  $('planStatus').textContent=job?job.name+'：'+job.detail:'';
  const material=job||snapshot.published.supportMaterial;
  const url=material?'/api/lesson-plan/'+(job?'source':'material')+'?id='+encodeURIComponent(material.id):'';
  $('planPreview').innerHTML=material?'<div class="material-file"><b>'+esc(material.name)+'</b><div class="toolbar"><button class="btn" data-material-preview="'+esc(url)+'">预览</button><a class="btn" href="'+esc(url)+'&amp;download=1">下载</a></div></div>':'';
  $('planShare').disabled=!material;applyResult();
 }
 async function poll(){if(!$('planStatus'))return;try{job=(await api('/api/lesson-plan')).job;preview();if(busy)setTimeout(poll,2000)}catch(e){$('planStatus').textContent='读取失败：'+e.message;setTimeout(poll,4000)}}
 async function start(retry=false){
  if(busy)return;
  const lessonCount=Number($('planTotal').value),file=$('planFile').files[0];
  if(!Number.isInteger(lessonCount)||lessonCount<1||lessonCount>30){toast('总课时数应为1—30');return}
  if(!retry&&(!file||!file.size||file.size>10*1024*1024)){toast('请选择10MB以内的非空教案');return}
  if(dirty&&!confirm('解析成功后替换备课草稿？已发布信息不变。'))return;
  busy=true;$('planAnalyze').disabled=true;$('planRetry').disabled=true;$('planTotal').disabled=true;$('planStatus').textContent='正在上传解析…';
  try{
   const body=retry?{retryId:job.id,lessonCount}:{lessonCount,name:file.name,content:await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败'));reader.readAsDataURL(file)})};
   const result=await api('/api/lesson-plan/analyze',{method:'POST',body:JSON.stringify(body)});pendingJob=result.id;sourceJobId=result.id;shareSource=true;dirty=true;stash();await poll();$('planShare').checked=shareSource;
  }catch(e){$('planStatus').textContent=e.message;busy=false;$('planAnalyze').disabled=false;$('planRetry').disabled=false;$('planTotal').disabled=false}
 }
 $('planAnalyze').onclick=()=>start();$('planRetry').onclick=()=>start(true);
 $('saveProject').onclick=async()=>{
  read();const count=Number($('pcGroupCount').value);
  if(!Number.isInteger(count)||count<1||count>20){toast('小组数量应为1—20');return}
  if(plan.lessons.some(l=>!l.projectInfo.title)){toast('请填写各课时项目名称');return}
  if(count<s.groupCount&&!confirm('减少小组将删除被移除小组的数据，是否继续？'))return;
  const button=$('saveProject');button.disabled=true;let groupChanged=false;
  try{
   if(count!==s.groupCount){const result=await api('/api/groups/configure',{method:'POST',body:JSON.stringify({groupCount:count})});groupChanged=true;s.groupCount=count;if(result.addedCodes?.length)alert('请保存新增访问码：\n'+result.addedCodes.map(x=>x.groupNo+'组：'+x.code).join('\n'))}
   const saved=await api('/api/lesson-plan/publish',{method:'POST',body:JSON.stringify({plan,selectedLesson:selected,revision,className:$('pcClass').value.trim(),reviewed:true,sourceJobId,shareSource})});revision=saved.revision;sourceJobId=null;dirty=false;stash();$('planSaveStatus').textContent='已发布第'+selected+'课时';toast('项目配置已保存');await refreshState();
  }catch(e){$('planSaveStatus').textContent=(groupChanged?'小组已更新，但项目保存失败：':'保存失败：')+e.message}finally{button.disabled=false}
 };
 window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue=''}});
 paint();preview();if(busy)setTimeout(poll,2000);
}
window.MPT_PLAN={mount};
})();
