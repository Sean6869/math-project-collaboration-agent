/* Teacher preparation: parsing, editing and publishing are separate actions. */
(function(){
'use strict';
const lessonKeys={title:'本课时名称',mathFocus:'本课时数学重点',objectives:'本课时学习目标',activities:'本课时活动与时间',resources:'本课时所需材料',output:'本课时产出'};
const blankLesson=n=>({lessonNo:n,title:`第${n}课时`,durationMinutes:0,mathFocus:'',objectives:'',activities:'',resources:'',output:'',stageMappings:[],sourcePages:[]});
const copy=x=>JSON.parse(JSON.stringify(x));
async function mount({s,api,esc,toast,refreshState,teacherId}){
 const $=id=>document.getElementById(id);
 let snapshot=await api('/api/lesson-plan'),revision=snapshot.published.revision||0,job=snapshot.job;
 let plan=snapshot.published.plan?copy(snapshot.published.plan):{
  title:s.project||'',grade:'',mathTopic:s.teacherConfig.mathTopic||'',standard:s.teacherConfig.standard||'',finalProduct:s.teacherConfig.finalProduct||'',projectType:s.projectType||'',allocationBasis:'教师手动编排',warnings:[],lessons:[blankLesson(1)]};
 let selected=snapshot.published.selectedLesson||1,dirty=false,parseBusy=false,staleDraft=null;
 const cacheKey=`mpt:lesson-draft:v1:${teacherId}`;
 try{const cached=JSON.parse(sessionStorage.getItem(cacheKey)||'null');if(cached&&cached.revision===revision){plan=cached.plan;selected=cached.selected;dirty=true}else if(cached)staleDraft=cached}catch(_){}
 const template=new URLSearchParams(location.search).get('template');if(['微型项目','标准项目','深度项目'].includes(template)){plan.projectType=template;dirty=true;toast('项目类型已填入草稿，请核对课时后保存')}
 const initialClass=s.className||'';
 const projectCard=$('pcProject').closest('section'),right=projectCard.nextElementSibling;
 const stack=document.createElement('div');stack.className='lesson-stack';right.replaceWith(stack);stack.appendChild(right);
 const sourceCard=document.createElement('section');sourceCard.className='card';sourceCard.innerHTML=`<h2>教案智能解析</h2><label for="planFile">上传课前教案</label><input id="planFile" type="file" accept=".pdf,.docx"><p class="small muted">PDF / DOCX，最大10MB。PDF最多40页，其中视觉识别最多20页。原教案不会加入学生知识库。只保留最近5次上传。</p><div class="toolbar"><button id="planAnalyze" class="btn primary">开始解析</button><button id="planRetry" class="btn" hidden>重新解析</button></div><p id="planStatus" role="status" aria-live="polite"></p><div id="planPreview"></div>`;stack.appendChild(sourceCard);
 const selector=document.createElement('div');selector.className='field';selector.innerHTML='<label for="pcLesson">备课课时（保存后生效）</label><select id="pcLesson"></select>';projectCard.querySelector('.form-grid').appendChild(selector);
 const editor=document.createElement('fieldset');editor.className='lesson-editor';editor.innerHTML=`<legend>本课时任务</legend><div class="form-grid"><div class="field"><label for="planTotal">总课时数（1—30）</label><input id="planTotal" type="number" min="1" max="30"></div><div class="field"><label for="planMinutes">本课时时长（分钟，0为未说明）</label><input id="planMinutes" type="number" min="0" max="240"></div><div class="field"><label for="planGrade">教案适用年级</label><input id="planGrade" maxlength="100"></div></div><div id="planLessonFields"></div><details id="planTiming" class="lesson-timing" hidden><summary>核对原文活动时间分配</summary><div id="planTimingFields"></div></details><div class="field"><label for="planStages">对应项目阶段（可多选）</label><select id="planStages" multiple size="5"><option value="1">1 项目准备</option><option value="2">2 任务理解</option><option value="3">3 方案建模</option><option value="4">4 协作探究</option><option value="5">5 成果反思</option></select></div><p id="planSources" class="small muted"></p><div id="planWarnings" class="lesson-warning"></div><label class="lesson-review"><input id="planReviewed" type="checkbox">我已核对年级、课时划分与内容，并确认任务中不包含应对学生保密的答案</label><p class="small muted">课时不是项目阶段。切换备课课时不会发布，也不会清空学生记录或自动完成任务。</p><button type="button" id="planRestore" class="btn" hidden>恢复旧版本未发布草稿</button><p id="planSaveStatus" role="status" aria-live="polite"></p>`;
 projectCard.insertBefore(editor,projectCard.querySelector('.toolbar'));
 $('planLessonFields').innerHTML=Object.entries(lessonKeys).map(([key,label])=>`<div class="field mt8"><label for="lesson-${key}">${label}</label>${key==='title'?`<input id="lesson-${key}" maxlength="2000">`:`<textarea id="lesson-${key}" rows="3" maxlength="12000"></textarea>`}</div>`).join('');
 if(staleDraft){$('planRestore').hidden=false;$('planSaveStatus').textContent='课堂配置已有新版本。旧草稿未丢失，可选择恢复后重新核对。';$('planRestore').onclick=()=>{if(!confirm('将旧草稿恢复到编辑区？保存前请与最新课堂配置核对。'))return;plan=copy(staleDraft.plan);selected=staleDraft.selected;dirty=true;paint();stash();$('planReviewed').checked=false;$('planRestore').hidden=true}}
 $('pcType').previousElementSibling.textContent='项目类型';$('pcType').querySelector('option[value=""]').textContent='请选择项目类型';
 function stash(){try{sessionStorage.setItem(cacheKey,JSON.stringify({revision,plan,selected}))}catch(_){} }
 function read(){
  Object.assign(plan,{title:$('pcProject').value.trim(),mathTopic:$('pcTopic').value.trim(),standard:$('pcStd').value.trim(),finalProduct:$('pcProduct').value.trim(),projectType:$('pcType').value,grade:$('planGrade').value.trim()});
  const lesson=plan.lessons[selected-1];for(const key of Object.keys(lessonKeys))lesson[key]=$(`lesson-${key}`).value.trim();
  lesson.durationMinutes=Number($('planMinutes').value);if(plan.sourceActivities?.length)lesson.activityAllocations=Array.from(document.querySelectorAll('[data-plan-minutes]')).map(el=>({activity:Number(el.dataset.planMinutes),minutes:Number(el.value)})).filter(x=>x.minutes!==0);lesson.stageMappings=Array.from($('planStages').selectedOptions).map(x=>Number(x.value));
 }
 function paint(){
  $('pcProject').value=plan.title;$('pcTopic').value=plan.mathTopic;$('pcStd').value=plan.standard;$('pcProduct').value=plan.finalProduct;$('pcType').value=plan.projectType;
  $('pcLesson').innerHTML=plan.lessons.map((l,i)=>`<option value="${i+1}">第${i+1}课时 / 共${plan.lessons.length}课时 · ${esc(l.title)}</option>`).join('');$('pcLesson').value=selected;
  $('planTotal').value=plan.lessons.length;$('planGrade').value=plan.grade;
  const lesson=plan.lessons[selected-1];for(const key of Object.keys(lessonKeys))$(`lesson-${key}`).value=lesson[key];$('planMinutes').value=lesson.durationMinutes;
  $('planTiming').hidden=!plan.sourceActivities?.length;$('planTimingFields').innerHTML=(plan.sourceActivities||[]).map((activity,i)=>{const minutes=(lesson.activityAllocations||[]).filter(x=>x.activity===i+1).reduce((sum,x)=>sum+x.minutes,0);return `<div class="field mt8"><label for="activity-${i}">${esc(activity.title)}（原文${activity.durationMinutes||'未说明'}分钟）</label><input id="activity-${i}" type="number" min="0" max="240" data-plan-minutes="${i+1}" value="${minutes}" aria-label="${esc(activity.title)}本课时分配分钟"></div>`}).join('');$('planTimingFields').querySelectorAll('input').forEach(el=>el.addEventListener('input',changed));
  Array.from($('planStages').options).forEach(o=>o.selected=lesson.stageMappings.includes(Number(o.value)));
  $('planSources').textContent=lesson.sourcePages.length?`来源：原教案第 ${lesson.sourcePages.join('、')} 页`:'来源：手动编排或原文未提供可靠页码';
  $('planWarnings').textContent=[plan.allocationBasis,...plan.warnings,plan.grade?`教案年级：${plan.grade}；当前班级：${$('pcClass').value||initialClass||'未填写'}。导入不会改变班级代码确定的学生AI数学范围。`:'教案未说明适用年级，请教师核对。'].filter(Boolean).join('\n');
 }
 function changed(){read();dirty=true;$('planReviewed').checked=false;$('planSaveStatus').textContent='有未发布的修改';stash()}
 projectCard.querySelectorAll('input,textarea,select').forEach(el=>{if(['pcLesson','planTotal','planReviewed'].includes(el.id))return;el.addEventListener('input',changed);el.addEventListener('change',changed)});
 $('pcLesson').onchange=()=>{read();selected=Number($('pcLesson').value);dirty=true;paint();stash();$('planSaveStatus').textContent='正在编辑该课时；点击保存后才会发布给学生'};
 $('planTotal').onchange=()=>{read();const total=Number($('planTotal').value);if(!Number.isInteger(total)||total<1||total>30){toast('总课时数应为1—30');$('planTotal').value=plan.lessons.length;return}if(total<plan.lessons.length&&!confirm('减少课时将从备课草稿中移除末尾课时，是否继续？学生历史记录不会删除。')){$('planTotal').value=plan.lessons.length;return}plan.lessons=Array.from({length:total},(_,i)=>plan.lessons[i]||blankLesson(i+1));selected=Math.min(selected,total);dirty=true;paint();stash();$('planReviewed').checked=false};
 function preview(){
  $('planStatus').textContent=job?`${job.name}：${job.detail}`:'上传后点击开始解析，解析结果需审核后再填入。';
  parseBusy=Boolean(job&&['extracting','analyzing'].includes(job.status));$('planAnalyze').disabled=parseBusy;$('planRetry').hidden=!job||parseBusy;$('planRetry').disabled=parseBusy;
  $('planPreview').innerHTML=job?.status==='ready'?`<h3>${esc(job.result.title)}</h3><p>识别为 ${job.result.lessons.length} 课时 · ${esc(job.result.grade||'年级未说明')}</p><ol>${job.result.lessons.map(l=>`<li>${esc(l.title)}${l.durationMinutes?`（${l.durationMinutes}分钟）`:''}</li>`).join('')}</ol><p class="small">${esc(job.result.allocationBasis)}</p>${job.result.warnings.map(w=>`<p class="lesson-warning">${esc(w)}</p>`).join('')}<p><a class="link" target="_blank" rel="noopener" href="/api/lesson-plan/source?id=${encodeURIComponent(job.id)}">查看原教案</a></p><button class="btn primary" id="planUse">填入备课草稿</button>`:'';
  if($('planUse'))$('planUse').onclick=()=>{if(dirty&&!confirm('用本次解析结果替换未发布的备课草稿？'))return;plan=copy(job.result);selected=1;dirty=true;paint();stash();$('planReviewed').checked=false;$('planSaveStatus').textContent='解析内容已填入，请逐课时核对后保存发布';toast('已填入草稿，尚未发布')};
 }
 async function poll(){if(!$('planStatus'))return;try{const data=await api('/api/lesson-plan');job=data.job;preview();if(parseBusy)setTimeout(poll,2000)}catch(e){$('planStatus').textContent=`状态读取失败：${e.message}。重新解析前可刷新页面查看任务。`;parseBusy=false;$('planAnalyze').disabled=false}}
 async function start(retry=false){
  const file=$('planFile').files[0];if(!retry&&(!file||file.size>10*1024*1024||!file.size)){toast('请选择10MB以内的非空教案');return}
  $('planAnalyze').disabled=true;$('planRetry').disabled=true;$('planStatus').textContent='正在上传教案…';
  try{const body=retry?{retryId:job.id}:{name:file.name,content:await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败'));reader.readAsDataURL(file)})};await api('/api/lesson-plan/analyze',{method:'POST',body:JSON.stringify(body)});await poll()}catch(e){$('planStatus').textContent=e.message;$('planAnalyze').disabled=false;$('planRetry').disabled=false}
 }
 $('planAnalyze').onclick=()=>start();$('planRetry').onclick=()=>start(true);
 $('saveProject').onclick=async()=>{
  read();const count=Number($('pcGroupCount').value);if(!Number.isInteger(count)||count<1||count>20){toast('小组数量应为1—20');return}
  if(!plan.title||plan.lessons.some(l=>!l.title||!Number.isInteger(l.durationMinutes)||l.durationMinutes<0||l.durationMinutes>240)){toast('请填写项目、课时名称和有效时长');return}
  if(!$('planReviewed').checked){toast('请先核对并勾选教案审核确认');return}
  if(count<s.groupCount&&!confirm(`减少到${count}组将删除被移除小组的课堂数据，是否继续？`))return;
  const button=$('saveProject');button.disabled=true;$('planSaveStatus').textContent='正在保存…';let groupChanged=false;
  try{
   // Group accounts retain their existing, explicit access-code workflow.
   if(count!==s.groupCount){const result=await api('/api/groups/configure',{method:'POST',body:JSON.stringify({groupCount:count})});groupChanged=true;s.groupCount=count;if(result.addedCodes?.length)alert('请保存新增访问码：\n'+result.addedCodes.map(x=>`${x.groupNo}组：${x.code}`).join('\n'))}
   const saved=await api('/api/lesson-plan/publish',{method:'POST',body:JSON.stringify({plan,selectedLesson:selected,revision,className:$('pcClass').value.trim(),reviewed:true})});revision=saved.revision;dirty=false;try{sessionStorage.removeItem(cacheKey)}catch(_){}$('planSaveStatus').textContent=`已发布第${selected}课时 / 共${plan.lessons.length}课时，未改变学生历史与阶段状态`;toast('项目与课时已保存');await refreshState();
  }catch(e){$('planSaveStatus').textContent=(groupChanged?'小组数量已更新，但项目与课时未发布：':'保存失败：')+e.message}finally{button.disabled=false}
 };
 window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue=''}});
 paint();preview();if(parseBusy)setTimeout(poll,2000);
}
window.MPT_PLAN={mount,blankLesson};
})();
