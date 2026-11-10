/* Classroom workflows. All persisted decisions are checked again by the server. */
window.MPT_CLASSROOM=function(C){
 'use strict';
 const {load,api,save,setRoot,shell,head,esc,toast,modal,refreshState,acceptState,deepClone,STAGES,stageTasks,stageLinks,stageTaskReady,uploadEvidenceFiles,event}=C;
 const $=id=>document.getElementById(id),session=()=>C.session(),value=id=>$(id)?.value?.trim()||'';
 const sub=['先看任务和分工','弄清问题和条件','用数学设计方案','一起比较并修改','展示理由，回顾收获'];
 const localTime=displayDateTime;
 function fieldSpec(id){
  const key=id.replace(/^compare-\d+-/,'compare-');
  return load().fieldCatalog?.find(spec=>spec.id===key)||{};
 }
 function fieldRule(id,override=null){
  const spec=fieldSpec(id);
  return {...spec,...(load().teacherConfig?.fieldRules?.[spec.id||id]||{}),...(override||{})};
 }
 function controlMarkup(id,text,rule,original=null){
  const kind=rule.type||'textarea',attrs=`id="${esc(id)}" maxlength="${Number(rule.maxLength)||1000}"`,options=String(rule.options||'').split('|').map(part=>part.trim()).filter(Boolean),selected=String(text??'');
  if(kind==='file')return original?.outerHTML||`<input type="file" ${attrs} accept="image/*,.pdf,.docx" capture="environment">`;
  if(kind==='checkbox')return `<input type="checkbox" ${attrs} ${text===true||selected==='true'?'checked':''}>`;
  if(kind==='select'){
   const choices=options.length?options:[...(original?.options||[])].map(option=>option.value).filter(Boolean);
   if(selected&&!choices.includes(selected))choices.unshift(selected);
   return `<select ${attrs}><option value=""></option>${choices.map(option=>`<option value="${esc(option)}" ${selected===option?'selected':''}>${esc(option)}</option>`).join('')}</select>`;
  }
  if(kind==='radio'||kind==='multiselect'){
   const chosen=selected.split('、');
   return `<input type="hidden" ${attrs} value="${esc(selected)}"><div class="task-list" data-choice-for="${esc(id)}">${options.map((option,i)=>`<label class="check"><input id="${esc(id)}-choice-${i}" type="${kind==='radio'?'radio':'checkbox'}" name="${esc(id)}-choices" value="${esc(option)}" ${chosen.includes(option)?'checked':''}>${esc(option)}</label>`).join('')}</div>`;
  }
  return kind==='textarea'?`<textarea ${attrs}>${esc(selected)}</textarea>`:`<input type="${kind==='number'?'number':'text'}" ${attrs} value="${esc(selected)}">`;
 }
 const field=(id,label,text='',type='textarea',override=null)=>{
  const rule=fieldRule(id,override),title=rule.title||label,kind=rule.type||type,help=rule.hintMode==='none'?'':rule.help||'',hint=rule.hintMode==='none'?'':rule.hint||'';
  return `<div class="field mt12" data-field-id="${id}"><label for="${id}">${esc(title)}${rule.required?'（必须完成）':'（可以跳过）'}</label>${help?`<small class="field-help">${esc(rule.hintMode==='short'?help.split(/[。！？]/)[0]:help)}</small>`:''}${controlMarkup(id,text,{...rule,type:kind})}${hint?`<a class="link" data-field-help="${id}" href="ai.html">问问迈谱助手</a>`:''}</div>`;
 };
 document.addEventListener('change',event=>{
  const group=event.target.closest?.('[data-choice-for]');if(!group)return;
  const control=$(group.dataset.choiceFor);if(control){control.value=[...group.querySelectorAll('input:checked')].map(input=>input.value).join('、');control.dispatchEvent(new Event('input',{bubbles:true}))}
 });
 document.addEventListener('click',event=>{
  const link=event.target.closest?.('[data-field-help]');if(!link)return;
  const id=link.dataset.fieldHelp,rule=fieldRule(link.dataset.schemaId||id),s=load(),control=$(id);
  const prompt=[`项目：${s.project}；阶段：${Number(document.body.dataset.page?.match(/^student-stage([1-5])/)?.[1]||s.currentStage)}；填写项：${rule.title||id}`,`已有内容：${control?.value||'还未填写'}`,rule.hint||'先解释这一项，再提醒我下一步怎样检查。'].join('\n');
  link.href='ai.html?field='+encodeURIComponent(id)+'&prompt='+encodeURIComponent(prompt);
 });
 const button=(id,label,cls='primary')=>`<button type="button" class="btn ${cls}" id="${id}">${label}</button>`;
 const links=()=>'<a class="btn" href="dashboard.html">返回课堂首页</a>';
 const page=(role,key,title,body,actions=links())=>setRoot(shell(role,key,head(title,'',actions)+body));
 async function request(action,body={},base=load()){
  try{
   C.saveStatus('正在保存……');
   const result=await api('/api/classroom/'+action,{method:'POST',body:JSON.stringify({operator:C.currentOperator(),pageStage:Number(document.body.dataset.page?.match(/^student-stage([1-5])/)?.[1]||load().currentStage),revision:body.groupRevision?base._revision:base._classRevision,...body})});
   if(result.state)acceptState(result.state);
   C.clearDraft();C.saveStatus('已保存到服务器');return result;
  }catch(error){C.saveStatus(error.code==='revision_conflict'?'内容已有更新，请比较后保存':'保存失败，请重试');if(error.code==='revision_conflict')C.showConflict(error,body,async fresh=>{await request(action,body,fresh)});else toast(error.message);return null}
 }
 function readProductDraft(x){document.querySelectorAll('[data-w]').forEach(el=>x.workspace[el.dataset.w]=el.value);document.querySelectorAll('[data-p]').forEach(el=>x.product[el.dataset.p]=el.value)}
 function productPhotos(s,editable=false){
  return `<section class="card mt16" id="productPhotoGallery"><h2>成果照片</h2>${(s.product.photos||[]).map((photo,i)=>`<article class="task"><a href="/api/files/${encodeURIComponent(photo.id)}" target="_blank" rel="noopener">${photo.mime?.startsWith('image/')?`<img src="/api/files/${encodeURIComponent(photo.id)}" alt="${esc(photo.name||'成果照片')}" loading="lazy" style="max-width:100%;max-height:240px;object-fit:contain">`:''}<p>${esc(photo.name||'查看成果照片')}</p></a>${editable?`<button class="btn sm" type="button" data-remove-product-photo="${i}">移除这张照片</button>`:''}</article>`).join('')||'<p>尚未保存成果照片</p>'}</section>`;
 }
 function timeline(s){
  return `<section class="card mt16"><h2>我们的方案是怎样改过来的？</h2><div class="version-compare">${s.versions?.length?s.versions.map(v=>{
   const evidence=(s.evidence||[]).filter(row=>row.modelVersion===v.v);
   const laterChecks=(s.fieldHistory||[]).filter(row=>row.modelVersion===v.v&&/^(math-check-|representation)/.test(row.fieldId));
   const checks=Object.entries(v.checks||{}).filter(([,checked])=>checked).map(([index])=>(s.fieldCatalog||[]).find(field=>field.id==='math-check-'+index)?.title||'已检查第'+(Number(index)+1)+'项');
   return `<details class="task" data-model-version="${esc(v.v)}"><summary>${esc(String(v.v).replace(/^V(\d+)$/,'第$1版方案'))} · ${esc(localTime(v.time))} · ${esc(v.actor||'小组')} · ${esc(v.reason||v.changeSummary||'保存数学方案')}</summary><dl><dt>数量与数据</dt><dd>${esc(v.variables||'')}</dd><dt>关系</dt><dd>${esc(v.relation||'')}</dd><dt>数学方案</dt><dd>${esc(v.model||'')}</dd><dt>条件</dt><dd>${esc(v.constraints||'')}</dd></dl><h3>保存时的检查记录</h3>${checks.map(text=>`<p>${esc(text)} · 已检查</p>`).join('')}${v.representation?.check?`<p>${esc(v.representation.check)}</p>`:''}${!checks.length&&!v.representation?.check?'<p>保存时尚无检查记录</p>':''}${laterChecks.map(row=>`<p>${esc(localTime(row.time))} · ${esc(row.actor||'小组')} · ${esc(row.title)}：${esc(row.after.map(value=>value===true?'已检查':value===false?'重新检查':String(value||'')).join('；'))}</p>`).join('')}<h3>这一版方案的相关记录</h3>${evidence.map(row=>`<p>${esc(row.text||row.file?.name||'')} · ${row.invalid?'已标记无效':row.verified?'已检查':'还需要检查'}</p>`).join('')||'<p>尚无关联记录</p>'}${(v.fileIds||[]).map(id=>`<a class="btn sm" href="/api/files/${encodeURIComponent(id)}" target="_blank" rel="noopener">查看草稿</a>`).join('')}</details>`;
  }).join(''):'<div class="empty">尚无方案版本</div>'}</div></section>`;
 }
 function materials(){
  const s=load(),info=s.currentLesson?.projectInfo||{},b={situation:info.projectSummary||'',problem:info.drivingQuestion||s.teacherConfig?.drivingQuestion||s.project||'',conditions:info.knownInformation||info.conditions||s.teacherConfig?.knownInformation||'',output:info.finalProduct||s.teacherConfig?.finalProduct||s.currentLesson?.output||'',...(!s.briefNeedsReview?Object.fromEntries(Object.entries(s.taskBrief||{}).filter(([k,v])=>['situation','problem','conditions','output'].includes(k)&&v!=='')): {})},confirmed=true,keys=[['situation','这次发生了什么？'],['problem','我们要解决什么问题？'],['conditions','题目告诉了我们什么？'],['output','最后要提交什么？']];
  page('student','s1','项目任务与材料',`<div class="grid cols-2">${confirmed?keys.map(([key,label])=>{const lines=String(b[key]||({situation:s.project,conditions:'先阅读老师发布的任务材料，标出数据、单位和限制。',output:'按本阶段任务卡整理并提交小组成果。'})[key]||s.project).split('\n').filter(Boolean);return `<section class="card"><h2>${label}</h2>${lines.slice(0,3).map(t=>`<p>${esc(t)}</p>`).join('')}${lines.length>3?`<details><summary>还有${lines.length-3}条重要条件</summary>${lines.slice(3).map(t=>`<p>${esc(t)}</p>`).join('')}</details>`:''}</section>`}).join(''):`<section class="card"><h2>项目概要</h2><p>${esc(s.currentLesson?.projectInfo?.projectSummary||'')}</p><p class="muted">${b.confirmed?'简明任务待核对':'教师尚未配置简明版'}</p></section>`}</div><section class="card mt16"><details${confirmed?'':' open'}><summary>查看完整任务与材料</summary><p>${esc(s.currentLesson?.projectInfo?.projectSummary||'')}</p>${C.stage1MaterialsList(s)}</details><div class="task-list mt16">${[['goal','已阅读项目目标与成果要求'],['materials','已核对材料与设备'],['rules','已确认课堂规则']].map(([key,label])=>`<label class="check"><input id="prep-${key}" type="checkbox" ${s.preparationChecks?.[key]?'checked':''}>${label}</label>`).join('')}</div><div class="toolbar">${button('savePrep','保存确认')}<a class="btn" href="stage1-roles.html">确认分工</a></div></section>`);
  $('savePrep').onclick=async()=>{const x=deepClone(load());x.preparationChecks=Object.fromEntries(['goal','materials','rules'].map(k=>[k,$('prep-'+k).checked]));if(await save(x))toast('已保存到服务器')};
 }
 function understanding(){
  const s=load(),members=s.members||[],history=s.thoughtHistory||[];
  page('student','s2','任务理解表',`<div class="grid cols-2"><section class="card"><h2>先自己想一想</h2><p>先自己想，再轮流说。可以写在纸上。</p><div class="field"><label for="thoughtMember">成员</label><select id="thoughtMember">${members.map((m,i)=>`<option value="${i}">${esc(m)}</option>`).join('')}</select></div>${field('thoughtContent','我认为要解决的问题是……')}${field('thoughtUncertain','我还不确定的地方是……')}<div class="field mt12"><label for="thoughtMode">记录方式</label><select id="thoughtMode"><option value="self">本人输入</option><option value="proxy">记录员代录</option><option value="photo">纸笔照片</option></select></div><label class="check mt12" id="proxyRow" hidden><input id="thoughtConfirmed" type="checkbox">已向这位同学复述并确认</label><div class="field mt12" id="thoughtPhotoRow" hidden><label for="thoughtPhoto">纸笔照片</label><input id="thoughtPhoto" type="file" accept="image/*" capture="environment"></div><div class="toolbar">${button('saveThoughtOne','保存这位成员的想法')}</div><p>已记录${members.filter((_,i)=>Boolean(s.firstThoughts?.[i])).length}/${members.length}位成员的第一轮想法</p><details><summary>更多记录</summary>${history.map(r=>`<article class="task"><b>${esc(r.memberName||members[Number(r.member)]||'成员')} · 第${r.version}版</b><small>${esc(localTime(r.time))} · ${{self:'本人输入',proxy:'记录员代录',photo:'纸笔照片'}[r.mode]||'文字'}</small><p>${esc(r.content)}</p>${r.uncertain?`<p>还不确定：${esc(r.uncertain)}</p>`:''}${r.fileId?`<a href="/api/files/${encodeURIComponent(r.fileId)}" target="_blank" rel="noopener">查看照片</a>`:''}</article>`).join('')}</details></section><section class="card"><h2>讨论后，我们组这样理解</h2>${field('under-problem','我们要解决什么？',s.workspace.problem)}${field('under-known','题目给了什么信息？',s.workspace.known)}${field('under-constraints','还必须满足哪些条件？',s.workspace.constraints)}<details class="mt12"><summary>更多记录</summary>${field('under-unknown','还有哪些信息要核对？',s.workspace.unknown)}</details><div class="toolbar">${button('saveUnderstandingNew','保存小组讨论结果','green')}<a class="btn" href="stage2-question-tree.html">进入问题树</a></div></section></div>`);
  const content=$('thoughtContent');content.style.webkitTextSecurity='disc';content.insertAdjacentHTML('afterend','<label class="check mt8"><input type="checkbox" id="showOwnThought">显示正在填写的内容</label>');$('showOwnThought').onchange=()=>{content.style.webkitTextSecurity=$('showOwnThought').checked?'none':'disc'};
  const missing=members.filter((_,i)=>!String(s.firstThoughts?.[i]||'').trim());const historyPanel=document.querySelector('.main details');if(historyPanel){historyPanel.hidden=true;historyPanel.insertAdjacentHTML('beforebegin',`<p>${missing.length?'尚未记录：'+missing.map(esc).join('、'):'每位成员都已记录'}</p>${button('revealThoughtHistory','全部完成后公开','ghost')}`);$('revealThoughtHistory').disabled=missing.length>0;$('revealThoughtHistory').onclick=()=>{historyPanel.hidden=!historyPanel.hidden;$('revealThoughtHistory').textContent=historyPanel.hidden?'全部完成后公开':'重新隐藏';if(!historyPanel.hidden)historyPanel.open=true}};
  $('thoughtMode').onchange=()=>{$('proxyRow').hidden=$('thoughtMode').value!=='proxy';$('thoughtPhotoRow').hidden=$('thoughtMode').value!=='photo'};
  $('thoughtMember').onchange=()=>{$('thoughtContent').value='';$('thoughtUncertain').value='';$('thoughtConfirmed').checked=false;$('showOwnThought').checked=false;content.style.webkitTextSecurity='disc'};
  $('saveThoughtOne').onclick=async()=>{
   const mode=value('thoughtMode');if(mode==='proxy'&&!$('thoughtConfirmed').checked){toast('请先向这位成员复述并确认');return}
   let fileId='';if(mode==='photo'&&$('thoughtPhoto').files.length){try{const files=await uploadEvidenceFiles([...$('thoughtPhoto').files]);fileId=files[0]?.id||''}catch(error){toast(error.message);return}}
   const result=await request('thought',{groupRevision:true,member:Number(value('thoughtMember')),content:value('thoughtContent'),uncertain:value('thoughtUncertain'),mode,fileId,confirmed:$('thoughtConfirmed').checked},s);
   if(result){toast(result.draft?'草稿内容还在':'已保存到服务器');understanding()}
  };
  $('saveUnderstandingNew').onclick=async()=>{const x=deepClone(load());for(const key of ['problem','known','unknown','constraints'])x.workspace[key]=value('under-'+key);event(x,'understanding_save','保存小组讨论结果');if(await save(x))toast('已保存到服务器')};
 }
 const quantityKeys=()=>['name','symbol','meaning','unit','source'].sort((a,b)=>Number(fieldRule('quantity-'+a).order)-Number(fieldRule('quantity-'+b).order));
 function quantityRow(row){return '<tr>'+quantityKeys().map(key=>`<td><input data-quantity="${key}" aria-label="${({name:'数量',symbol:'符号',meaning:'表示什么',unit:'单位',source:'数据来自哪里'})[key]}" value="${esc(row[key]||'')}"></td>`).join('')+'<td><button type="button" class="btn sm" data-remove-quantity>移除</button></td></tr>'}
 function model(){
  const s=load(),w=s.workspace||{},number=(s.versions?.length||0)+1;
  page('student','s3','设计数学方案',`<div class="grid cols-2"><section class="card"><h2>1 · 用到哪些数量或数据？</h2><div class="table-wrap"><table class="table"><thead><tr>${quantityKeys().map(key=>{const rule=fieldRule('quantity-'+key);return `<th title="${esc(rule.help||'')}">${esc(rule.title)}${rule.required?'（必须完成）':'（可以跳过）'}</th>`}).join('')}<th>操作</th></tr></thead><tbody id="quantityRows">${(w.quantityRows?.length?w.quantityRows:[{name:w.variables||''}]).map(row=>quantityRow(row)).join('')}</tbody></table></div>${button('addQuantity','增加一个数量','ghost')}<h2 class="mt16">2 · 这些量怎样联系？</h2><div class="symbol-toolbar" role="toolbar" aria-label="数学符号">${['x','y','a','b','²','³','+','−','×','÷','=','≠','<','>','≤','≥','(',')','|','√','π','≈'].map(v=>`<button type="button" class="btn sm" data-symbol="${esc(v)}">${esc(v)}</button>`).join('')}</div>${field('modelRelation','写式子或列表',w.relation)}<div class="field mt12"><label for="modelPhoto">纸笔照片</label><input id="modelPhoto" type="file" accept="image/*" multiple capture="environment"></div><h2 class="mt16">3 · 在什么条件下成立？</h2><div class="task-list">${['符合题目给出的信息','单位保持一致','研究量在允许范围内'].map((label,i)=>`<label class="check"><input type="checkbox" data-model-condition="${i}" ${w.conditionChecks?.[i]?'checked':''}>${label}</label>`).join('')}</div>${field('modelConditions','补充条件与允许范围',w.constraints)}${field('modelReason','这次改了什么？')}${button('saveModelNew',`保存数学方案第${number}版`)}<a class="btn" href="stage3-graph.html">用图、表或算式说明</a></section><section class="card"><h2>已有方案</h2>${timeline(s)}</section></div>`);
  $('quantityRows').onclick=event=>{const button=event.target.closest('[data-remove-quantity]');if(button){button.closest('tr').remove();C.installDraftAutosave()}};
  $('addQuantity').onclick=()=>{$('quantityRows').insertAdjacentHTML('beforeend',quantityRow({}));decorateFields($('quantityRows'));C.installDraftAutosave()};
  document.querySelectorAll('[data-symbol]').forEach(b=>{b.onpointerdown=e=>e.preventDefault();b.onclick=()=>{const input=$('modelRelation');if(input.tagName!=='TEXTAREA'&&input.type!=='text'){toast('请使用当前填写项的选项');return}const at=input.selectionStart,end=input.selectionEnd;input.setRangeText(b.dataset.symbol,at,end,'end');input.focus();input.dispatchEvent(new Event('input',{bubbles:true}))}});
  $('saveModelNew').onclick=async()=>{
   if(number>1&&s.teacherConfig?.fieldRules?.modelReason?.required===true&&!value('modelReason')){toast('请填写这次改了什么');return}
   let files=[];try{if($('modelPhoto').files.length)files=await uploadEvidenceFiles([...$('modelPhoto').files])}catch(error){toast(error.message);return}
   const x=deepClone(load());x.workspace.quantityRows=[...document.querySelectorAll('#quantityRows tr')].map(row=>Object.fromEntries([...row.querySelectorAll('[data-quantity]')].map(el=>[el.dataset.quantity,el.value.trim()]))).filter(row=>Object.values(row).some(Boolean));x.workspace.variables=x.workspace.quantityRows.map(row=>[row.name,row.symbol,row.meaning,row.unit,row.source].filter(Boolean).join(' · ')).join('\n');x.workspace.conditionChecks=[...document.querySelectorAll('[data-model-condition]')].map(el=>el.checked);x.workspace.relation=value('modelRelation').replaceAll('＝','=').replaceAll('−','-');x.workspace.constraints=value('modelConditions');x.workspace.revisionReason=value('modelReason');x.workspace.modelFiles=[...(w.modelFiles||[]),...files.map(f=>f.id)];x.workspace.model=x.workspace.relation|| (x.workspace.modelFiles.length?'纸笔方案':'');
   if(await save(x)){toast(`${load().versions.at(-1)?.v||'方案'}已保存`);model()}
  };
 }
 function compare(){
  const s=load(),v=s.viewpoints?.length>=2?s.viewpoints:[{},{}],comp=s.comparison||{},compareStage=new URLSearchParams(location.search).get('stage')==='4'?4:3;
  page('student',compareStage===4?'s4':'s3',compareStage===4?'协作探究 · 比较两种方案':'比较两种方案',`<section class="card"><h2>两种方案限定同样的条件</h2>${field('compareConditions','共同条件',comp.conditions)}</section><div class="grid cols-2 mt16">${v.slice(0,2).map((row,i)=>`<section class="card"><h2>方案${'AB'[i]}</h2>${[['claim','方案描述'],['basis','计算、测量或数据依据'],['conditions','这个方案在什么条件下成立？'],['risk','还需要核对什么？']].map(([key,label])=>field(`compare-${i}-${key}`,label,row[key]||'')).join('')}</section>`).join('')}</div><section class="card mt16"><div class="field"><label for="compareChoice">我们现在的选择</label><select id="compareChoice">${[['','还需要证据'],['A','方案A'],['B','方案B']].map(([val,label])=>`<option value="${val}" ${comp.choice===val?'selected':''}>${label}</option>`).join('')}</select></div>${field('compareReason','为什么这样选？请写出数学依据。',comp.reason)}<label class="check mt12"><input id="compareChecked" type="checkbox" ${comp.checked?'checked':''}>已核对两种方案的共同条件</label><div class="toolbar">${button('saveCompareNew','保存比较草稿')}${button('aiCompareNew','请AI检查条件','green')}</div><div id="compareReply" class="mt12">${esc(s.comparisonAi?.answer||'')}</div></section><section class="card mt16"><h2>比较记录</h2>${(s.comparisonHistory||[]).length?(s.comparisonHistory||[]).slice().reverse().map(r=>`<details class="task"><summary>${esc(localTime(r.time))} · 阶段${esc(r.comparison?.stage||3)} · ${esc(r.comparison?.choice||'待选择')}</summary><p>${esc(r.comparison?.conditions||'')}</p><p>${esc(r.comparison?.reason||'')}</p>${(r.viewpoints||[]).map((v,i)=>`<p>方案${'AB'[i]||i+1}：${esc(v.claim||'')} ${esc(v.basis||'')}</p>`).join('')}</details>`).join(''):'<div class="empty">尚无比较记录</div>'}</section>`);
  $('compareConditions').oninput=()=>{$('compareChecked').checked=false};
  const read=()=>v.slice(0,2).map((r,i)=>Object.fromEntries(['claim','basis','conditions','risk'].map(key=>[key,value(`compare-${i}-${key}`)])));
  $('saveCompareNew').onclick=async()=>{const x=deepClone(load());x.viewpoints=read();x.comparison={stage:compareStage,conditions:value('compareConditions'),choice:value('compareChoice'),reason:value('compareReason'),checked:$('compareChecked').checked,modelVersion:s.versions?.at(-1)?.v};if(x.comparison.choice&&!x.comparison.reason){toast('请写出选择的数学依据');return}event(x,'compare_save','保存方案比较','',compareStage);if(await save(x)){toast('已保存到服务器');compare()}};
  $('aiCompareNew').onclick=async()=>{try{const result=await api('/api/ai/compare',{method:'POST',body:JSON.stringify({stage:new URLSearchParams(location.search).get('stage')==='4'?4:3,viewpoints:read(),conditions:value('compareConditions')})});$('compareReply').textContent=result.answer}catch(error){toast(error.message)}};
 }
 function evidence(){
  const s=load();
  page('student','s4','计算和图表记录',`<section class="card">${s.evidence.map((row,i)=>`<article class="task"><b>${esc(row.type||'其他')} · ${esc(row.text||row.file?.name||'')}</b><p>阶段${row.stage||4} · ${esc(String(row.modelVersion||'尚未关联方案').replace(/^V(\d+)$/,'第$1版方案'))} · ${row.invalid?'已标记无效':row.verified?'已检查':'还需要检查'}</p>${row.file?`<a class="btn sm" href="/api/files/${encodeURIComponent(row.file.id)}" target="_blank" rel="noopener">查看上传内容</a>`:''}<button class="btn sm" data-edit-record="${i}">编辑记录</button></article>`).join('')}<div class="toolbar">${button('newRecord','增加一条记录')}</div></section>`);
  const open=index=>{
   const row=s.evidence[index]||{};
   modal(index<0?'增加记录':'编辑记录',`<div class="field"><label>记录类型</label><select id="recordType">${['计算','图表','测量','资料','其他'].map(type=>`<option ${row.type===type?'selected':''}>${type}</option>`).join('')}</select></div>${field('recordText','它说明了什么',row.text)}<div class="field mt12"><label>对应哪一步</label><select id="recordStage">${STAGES.map(st=>`<option value="${st.n}" ${Number(row.stage||4)===st.n?'selected':''}>${st.n} ${esc(st.name)}</option>`).join('')}</select></div><div class="field mt12"><label>这条记录对应的方案版本</label><select id="recordVersion"><option value="">暂不关联</option>${s.versions.map(v=>`<option value="${esc(v.v)}" ${row.modelVersion===v.v?'selected':''}>${esc(String(v.v).replace(/^V(\d+)$/,'第$1版方案'))}</option>`).join('')}</select></div><div class="field mt12"><label>拍照或上传内容（可不填）</label><input type="file" accept="image/*,.pdf,.docx,.csv" capture="environment" id="recordFile"></div><label class="check mt12"><input id="recordVerified" type="checkbox" ${row.verified?'checked':''}>已检查</label><label class="check mt12"><input id="recordInvalid" type="checkbox" ${row.invalid?'checked':''}>这条记录无效</label>${button('saveRecord','保存记录')}`);
   $('saveRecord').onclick=async()=>{
    const x=deepClone(load()),files=$('recordFile').files;
    let attachment=row.file;try{if(files.length)attachment=(await uploadEvidenceFiles([...files]))[0]}catch(error){toast(error.message);return}
    if(!value('recordText')&&!attachment){toast('请填写记录说明或上传内容');return}
    const item={...row,type:value('recordType'),text:value('recordText')||attachment.name,stage:Number(value('recordStage')),modelVersion:value('recordVersion'),verified:$('recordVerified').checked&&!$('recordInvalid').checked,invalid:$('recordInvalid').checked,file:attachment||null};
    if(index<0)x.evidence.push(item);else x.evidence[index]=item;
    event(x,index<0?'evidence_add':'evidence_edit',item.text,null,4);
    if(await save(x)){document.querySelector('.modal-mask')?.remove();evidence()}
   };
  };
  $('newRecord').onclick=()=>open(-1);document.querySelectorAll('[data-edit-record]').forEach(el=>el.onclick=()=>open(Number(el.dataset.editRecord)));
 }
 function reflection(){
  const s=load(),legacy=String(s.product.reflection||'').split('\n'),r=s.product.reflectionFields||{changed:legacy[0],reason:legacy[1],next:legacy[2]},fields=[['changed','这次修改了什么'],['reason','为什么修改'],['helps','使用过哪些帮助'],['rotated','分工是否轮换'],['checked','哪些根据已经检查'],['next','下次先检查什么']];
  page('student','s5','回顾这次是怎样完成的',`<section class="card">${fields.map(([key,label])=>field('reflect-'+key,label,r[key]||'')).join('')}<div class="toolbar">${button('saveReflectionFields','保存回顾')}</div></section>`);
  if(s.stageSubmitted?.[5]){document.querySelectorAll('textarea,button').forEach(el=>el.disabled=true);return}
  $('saveReflectionFields').onclick=async()=>{const x=deepClone(load());x.product.reflectionFields=Object.fromEntries(fields.map(([key])=>[key,value('reflect-'+key)]));x.product.reflection=[value('reflect-changed'),value('reflect-reason'),value('reflect-next')].join('\n');event(x,'reflection_save','保存回顾');if(await save(x))toast('已保存')};
 }
 function discussion(){
  const s=load(),skipped=s.debate.some(row=>row.skipped);
  page('student','s4','讨论不同想法',`<section class="card"><label class="check"><input id="skipDiscussion" type="checkbox" ${skipped?'checked':''}>没有不同意见，跳过本项</label><div id="discussionForm" ${skipped?'hidden':''}><div class="field mt12"><label>由谁记录</label><select id="discussionMember">${s.members.map(m=>`<option>${esc(m)}</option>`).join('')}</select></div>${field('discussionClaim','我的看法')}${field('discussionBasis','我的理由或根据')}${field('discussionCheck','我希望大家一起检查的地方')}${field('discussionDecision','讨论后的决定')}</div><div class="toolbar">${button('saveDiscussion','保存讨论记录')}</div></section><section class="card mt16"><h2>讨论记录</h2>${s.debate.map(row=>`<article class="task"><b>${esc(row.who||'小组')}</b><p>${esc(row.skipped?'没有不同意见，跳过本项':row.text)}</p></article>`).join('')}</section>`);
  $('skipDiscussion').onchange=()=>$('discussionForm').hidden=$('skipDiscussion').checked;
  $('saveDiscussion').onclick=async()=>{const skip=$('skipDiscussion').checked,parts=['discussionClaim','discussionBasis','discussionCheck','discussionDecision'].map(value);if(!skip&&!parts[0]){toast('请写下你的看法');return}const x=deepClone(load());x.debate.push({who:value('discussionMember'),skipped:skip,text:skip?'没有不同意见':parts.join('\n'),claim:parts[0],basis:parts[1],check:parts[2],decision:parts[3],time:new Date().toISOString()});event(x,'debate_save',skip?'无分歧跳过':parts[0],null,4);if(await save(x))discussion()};
 }
 function questions(){
  const s=load(),rows=(s.questionTree||[]).slice(1).filter(Boolean);
  page('student','s2','问题拆分表',`<section class="card">${field('questionMain','共同要解决的问题',s.questionTree?.[0]||s.workspace.problem)}<div id="questionRows">${rows.map((row,i)=>`<div class="field mt12"><label>小问题${i+1}</label><textarea data-question-row>${esc(row)}</textarea><select data-question-link><option value="">选择接下来要做的事</option><option value="stage3-model.html">设计数学方案</option><option value="stage3-graph.html">画图或计算</option><option value="stage4-evidence.html">收集检查根据</option></select><button type="button" class="btn sm" data-remove-question>移除这个小问题</button></div>`).join('')}</div><div class="toolbar">${button('addQuestion','添加一个小问题','ghost')}${button('saveQuestions','保存问题拆分表')}</div></section>`);
  const bind=()=>document.querySelectorAll('[data-question-link]').forEach((el,i)=>{el.value=s.workspace.questionLinks?.[i]||''});
  $('questionRows').onclick=event=>{const button=event.target.closest('[data-remove-question]');if(button){const field=button.closest('.field');(field.closest('[data-field-fold]')||field).remove();C.installDraftAutosave()}};
  bind();$('addQuestion').onclick=()=>{$('questionRows').insertAdjacentHTML('beforeend','<div class="field mt12"><label>小问题</label><textarea data-question-row></textarea><select data-question-link><option value="">选择接下来要做的事</option><option value="stage3-model.html">设计数学方案</option><option value="stage3-graph.html">画图或计算</option><option value="stage4-evidence.html">收集检查根据</option></select><button type="button" class="btn sm" data-remove-question>移除这个小问题</button></div>');decorateFields($('questionRows'));C.installDraftAutosave()};
  $('saveQuestions').onclick=async()=>{const x=deepClone(load());const rows=[...document.querySelectorAll('[data-question-row]')].map(el=>({text:el.value.trim(),link:el.parentElement.querySelector('[data-question-link]').value})).filter(row=>row.text);x.questionTree=[value('questionMain'),...rows.map(row=>row.text)];x.workspace.questionLinks=rows.map(row=>row.link);event(x,'question_tree_save','保存问题拆分表');if(await save(x)){toast('已保存');questions()}};
 }
 function roles(){
  const s=load(),names=C.roleNames.slice(0,s.members.length);
  page('student','group','这一轮谁负责想、说、记录、检查？',`<section class="card"><h2>本阶段分工</h2>${s.members.map((member,i)=>`<div class="role-edit"><b>${esc(member)}</b><select aria-label="${esc(member)}的分工" data-role-index="${i}">${names.map(r=>`<option ${s.roles[i]===r?'selected':''}>${r}</option>`).join('')}</select><small data-role-duty="${i}">${esc(C.roleDuties[C.roleNames.indexOf(s.roles[i])]||'共同完成数学任务')}</small></div>`).join('')}<label class="check mt12"><input id="keepRoles" type="checkbox">这次不调整分工</label><div id="keepRoleReason" hidden>${field('roleReason','这次不调整分工的原因（可不填）')}</div><div class="toolbar">${button('confirmRoles','我们确认了这轮分工')}<a class="btn" href="stage1-roles.html">成员名单</a></div></section><section class="card mt16"><h2>以前的分工记录</h2>${(s.roleAssignments||[]).map(r=>`<details class="task"><summary>阶段${r.stage} · 第${r.round}轮 · ${esc(localTime(r.time))}</summary><p>${r.members.map((m,i)=>esc(m)+'：'+esc(r.roles[i])).join('；')}</p><p>${esc(r.reason||'')}</p></details>`).join('')}</section>`);
  document.querySelectorAll('[data-role-index]').forEach(el=>el.onchange=()=>{document.querySelector('[data-role-duty="'+el.dataset.roleIndex+'"]').textContent=C.roleDuties[C.roleNames.indexOf(el.value)]||''});
  $('keepRoles').onchange=()=>{$('keepRoleReason').hidden=!$('keepRoles').checked};
  $('confirmRoles').onclick=async()=>{if(await request('roles',{groupRevision:true,roles:[...document.querySelectorAll('[data-role-index]')].map(el=>el.value),reason:value('roleReason')},s)){toast('已保存到服务器');roles()}};
 }
 function evaluation(){
  const s=load(),gid=Number(new URLSearchParams(location.search).get('g')||1),ev=s.evaluations?.[gid]||{},scores=ev.scores||{},dimensions=[['model','模型是否合理'],['evidence','证据与核验'],['explain','数学解释'],['collab','小组协作']];
  page('teacher','eval','学习成果评价',`<div class="field"><label for="evaluationGroup">小组</label><select id="evaluationGroup">${s.groups.map(g=>`<option value="${g.id}" ${gid===g.id?'selected':''}>${g.id}组</option>`).join('')}</select></div><div class="grid cols-2 mt16"><section class="card"><h2>材料是否齐全</h2><div class="mini-stat"><span>阶段提交</span><b>${Object.values(s.stageSubmitted).filter(Boolean).length}/5</b></div><div class="mini-stat"><span>方案版本</span><b>${s.versions.length}</b></div><div class="mini-stat"><span>证据</span><b>${s.evidence.length}</b></div><a class="btn" id="evaluationEvidenceLink" href="evaluation-evidence.html?g=${gid}&from=evaluation">查看这项评价的数学依据</a>${timeline(s)}</section><section class="card"><h2>教师对数学学习的判断</h2>${dimensions.map(([key,label])=>`<div class="field mt12"><label for="score-${key}">${label}</label><select id="score-${key}"><option value="">资料不足，暂不评分</option>${[1,2,3,4].map(n=>`<option value="${n}" ${scores[key]===n?'selected':''}>${n}</option>`).join('')}</select></div>`).join('')}${field('evaluationComment','教师评语',ev.comment)}<div class="toolbar">${button('evaluationDraft','保存评价草稿')}${button('evaluationPublish','发布评价给小组','green')}${ev.status==='published'?button('evaluationWithdraw','撤回发布','danger'):''}</div><p>${{draft:'草稿',published:'已发布',withdrawn:'已撤回'}[ev.status]||''}</p></section></div>`);
  const draftKey='maple-evaluation:'+session().projectId+':'+gid,draft=JSON.parse(sessionStorage.getItem(draftKey)||'null');
  if(draft){for(const [key] of dimensions)$('score-'+key).value=draft.scores[key]??'';$('evaluationComment').value=draft.comment||''}
  const retainDraft=()=>sessionStorage.setItem(draftKey,JSON.stringify({scores:Object.fromEntries(dimensions.map(([key])=>[key,value('score-'+key)])),comment:value('evaluationComment')}));
  document.querySelectorAll('[id^="score-"],#evaluationComment').forEach(el=>el.addEventListener('input',retainDraft));
  $('evaluationEvidenceLink').onclick=retainDraft;
  $('evaluationGroup').onchange=e=>location.href='evaluation.html?g='+e.target.value;
  const commit=async status=>{const reason=status==='withdrawn'?prompt('撤回原因')||'':'';if(status==='published'&&!confirm(`将评价发布给${gid}组？`))return;const result=await request('evaluation',{group:gid,status,reason,scores:Object.fromEntries(dimensions.map(([key])=>[key,Number(value('score-'+key))||null])),comment:value('evaluationComment'),evidenceRefs:s.evidence.map(e=>e.id).filter(Boolean)},s);if(result){sessionStorage.removeItem(draftKey);toast(status==='published'?'评价已发布':'已保存到服务器');evaluation()}};
  $('evaluationDraft').onclick=()=>commit('draft');$('evaluationPublish').onclick=()=>commit('published');$('evaluationWithdraw')?.addEventListener('click',()=>commit('withdrawn'));
 }
 function stages(){
  const s=load();
  page('teacher','stages','课堂阶段控制',`<section class="card"><h2>全班进度</h2><div class="chips">${STAGES.map(st=>`<span class="chip ${st.n===s.classTargetStage?'active':''}">${st.n} ${st.name}</span>`).join('')}</div><p>当前全班阶段：${s.classTargetStage} · ${esc(STAGES[s.classTargetStage-1].name)}</p><div class="toolbar">${button('advanceClass','进入下一阶段')}${button('rewindClass','返回上一阶段','ghost')}${button('followAll','全班进度推进','ghost')}${button('teacherRotate','开始轮换','ghost')}${button('pauseClass',s.stagePaused?'恢复全班':'暂停全班','warn')}</div></section><section class="card mt16"><h2>各组课堂进度</h2><div class="table-wrap"><table class="table"><thead><tr><th>小组</th><th>现在到哪一步</th><th>成果</th><th>需要帮助</th><th>操作</th></tr></thead><tbody>${s.groups.map(g=>`<tr><td>${g.id}组</td><td>${g.stage} · ${esc(STAGES[g.stage-1].name)}${g.hold?' · 暂缓':''}</td><td>${g.currentSubmitted?'已提交':'待提交'}</td><td>${s.queue.filter(q=>q.group===g.id&&!q.resolved).length}</td><td><select data-control="${g.id}" aria-label="${g.id}组操作"><option value="">选择操作</option><option value="hold">暂缓本组</option><option value="next">允许本组继续</option><option value="back">回到上一阶段</option><option value="follow">恢复跟随全班</option></select></td></tr>`).join('')}</tbody></table></div></section>`);
  document.querySelectorAll('[data-control]').forEach(el=>el.onchange=async()=>{const group=Number(el.dataset.control),action=el.value;if(!action)return;const g=s.groups.find(g=>g.id===group),target=action==='back'?Math.max(1,g.stage-1):action==='next'?Math.min(5,g.stage+1):action==='follow'?s.classTargetStage:g.stage;const reason='';if(!confirm(`${group}组：${el.selectedOptions[0].text}，阶段${target}？`)){el.value='';return}if(await request('control',{group,action,reason},s))stages()});
  $('followAll').onclick=async()=>{if(confirm('将落后的小组推进到当前全班阶段？已保存的作品和提交记录会保留。')&&await request('control',{action:'follow_all'},s))stages()};
  $('rewindClass').disabled=s.classTargetStage<=1;
  $('rewindClass').onclick=async()=>{if(confirm('返回全班上一阶段？已保存的作品和提交记录会保留。')&&await request('rewind',{},s))stages()};
  $('pauseClass').onclick=async()=>{const x=deepClone(load());x.stagePaused=!s.stagePaused;if(await save(x))stages()};
  $('advanceClass').disabled=s.classTargetStage>=5;
  $('advanceClass').onclick=async()=>{if(!confirm('进入下一阶段，并将落后的小组推进到该阶段？已保存的作品和提交记录会保留。'))return;if(await request('advance',{},s))stages()};
 }
 function groups(){
  const s=load();
  page('teacher','situation','小组课堂情况',`<section class="card"><div class="toolbar"><select id="groupFilter" aria-label="筛选小组"><option value="all">全部</option><option value="help">求助中</option><option value="evidence">证据待核验</option><option value="idle">本阶段无更新</option>${STAGES.map(st=>`<option value="stage-${st.n}">阶段${st.n}</option>`).join('')}</select></div><div id="groupCards" class="grid cols-2"></div></section>`);
  const draw=()=>{$('groupCards').innerHTML=[...s.groups].sort((a,b)=>Number(b.status==='等待教师')-Number(a.status==='等待教师')).filter(g=>value('groupFilter')==='all'||value('groupFilter')==='help'&&s.queue.some(q=>q.group===g.id&&!q.resolved)||value('groupFilter')==='evidence'&&g.evidencePending||value('groupFilter')==='idle'&&(!g.updatedAt||Date.now()-Date.parse(g.updatedAt)>(Number(s.teacherConfig.idleMinutes)||10)*60000)||value('groupFilter')==='stage-'+g.stage).map(g=>`<article class="task"><h2>${g.id}组 · ${esc(STAGES[g.stage-1].name)}</h2><b>${esc(g.version||'尚无版本')}</b><h3>最近提交的算式／数据</h3><p class="line-two">${esc(g.math||'尚未记录')}</p><h3>还没核对的条件</h3><p class="line-two">${esc(g.pendingCheck||'尚未记录')}</p><a class="btn" href="group-detail.html?g=${g.id}&view=readonly">查看本组工作区</a></article>`).join('')};$('groupFilter').onchange=draw;draw();
 }
 function readonly(){
  const s=load(),gid=Number(new URLSearchParams(location.search).get('g')||1);
  page('teacher','groups',`${gid}组工作区 · 只读`,`<section class="card"><h2>任务与数学记录</h2>${Object.entries(s.workspace).filter(([,v])=>typeof v==='string'&&v).map(([k,v])=>`<dl><dt>${esc(({problem:'项目问题',known:'已知信息',unknown:'待核对信息',constraints:'条件',variables:'数量与数据',relation:'数量关系',model:'数学方案',next:'下一步'})[k]||'记录')}</dt><dd>${esc(v)}</dd></dl>`).join('')}</section>${timeline(s)}<section class="card mt16"><h2>证据</h2>${s.evidence.map(e=>`<article class="task"><p>${esc(e.text||'')}</p><span>${e.verified?'已核验':'待核验'}</span>${e.file?`<a class="btn" href="/api/files/${encodeURIComponent(e.file.id)}" target="_blank" rel="noopener">查看原始附件</a>`:''}</article>`).join('')}</section><section class="card mt16"><h2>本组设置</h2><div class="toolbar">${button('unlockGroupMembers','解锁成员与角色','ghost')}${button('groupAiOff','关闭本组自由AI','warn')}${button('groupAiOn','开放本组自由AI')}${button('groupExtraHints','调整分层额度')}</div></section>`);
  document.querySelector('.main')?.insertAdjacentHTML('beforeend',productPhotos(s));
  if(new URLSearchParams(location.search).get('from')==='evaluation'||document.body.dataset.page==='teacher-evaluation-evidence'){document.querySelector('.page-head')?.insertAdjacentHTML('beforeend',`<a class="btn primary" href="evaluation.html?g=${gid}">返回该组评价</a>`);$('unlockGroupMembers').closest('section').remove();return}
  $('unlockGroupMembers').onclick=async()=>{try{await api('/api/group/members',{method:'POST',body:JSON.stringify({action:'unlock',groupId:gid})});toast('已开放小组修改')}catch(error){toast(error.message)}};
  $('groupAiOff').onclick=async()=>{if(await request('ai-access',{group:gid,enabled:false},s))toast('已关闭')};$('groupAiOn').onclick=async()=>{if(await request('ai-access',{group:gid,enabled:true},load()))toast('已开放')};$('groupExtraHints').onclick=()=>location.href='scaffolds.html';
 }
 function queue(){
  const s=load(),items=[...s.queue].filter(q=>!q.resolved).sort((a,b)=>Number(!!b.urgent)-Number(!!a.urgent)||String(a.createdAt||a.time).localeCompare(String(b.createdAt||b.time))),topics={};items.forEach(q=>(topics[q.topic||q.type]??=[]).push(q.group));
  if(new URLSearchParams(location.search).get('display')==='1'){setRoot(`<main class="main"><h1>教师求助顺序</h1><section class="card"><table class="table"><thead><tr><th>顺序</th><th>组别</th><th>状态</th><th>等待时间</th></tr></thead><tbody>${items.map((row,i)=>`<tr><td>${i+1}</td><td>${row.group}组</td><td>${esc(({received:'已发送',viewed:'教师已查看',processing:'处理中',visiting:'处理中',broadcast:'已回应',handled:'已回应'})[row.handling]||'已发送')}</td><td>${Math.max(0,Math.floor((Date.now()-Date.parse(row.createdAt||new Date()))/60000))}分钟</td></tr>`).join('')}</tbody></table></section></main>`);clearInterval(window.mapleProjectionTimer);window.mapleProjectionTimer=setInterval(async()=>{if(document.hidden)return;await refreshState();queue()},5000);return}
  page('teacher','queue','需要老师帮助的小组',`<section class="card"><div class="table-wrap"><table class="table"><thead><tr><th>组号</th><th>阶段</th><th>卡在哪里</th><th>试过什么</th><th>已等待</th><th>处理状态</th><th>操作</th></tr></thead><tbody>${items.map(q=>`<tr><td>${q.group}组</td><td>${q.stage||''}</td><td>${esc(q.reason)}</td><td>${esc(q.attempt||'')}</td><td>${q.createdAt?Math.max(0,Math.floor((Date.now()-Date.parse(q.createdAt))/60000))+'分钟':esc(q.time)}</td><td>${esc(({received:'已发送',viewed:'教师已查看',processing:'处理中',visiting:'老师前往查看',broadcast:'已广播回应',handled:'已回应，待小组确认'})[q.handling]||'待回应')}</td><td><button class="btn sm" data-help-response="${esc(q.id)}">回复本组</button></td></tr>`).join('')}</tbody></table></div></section><section class="card mt16"><h2>相似求助</h2>${Object.entries(topics).filter(([,g])=>new Set(g).size>1).map(([topic,groups])=>`<article class="task"><b>${[...new Set(groups)].join('、')}组 · ${esc(topic)}</b><a class="btn" href="broadcast.html?groups=${[...new Set(groups)].join(',')}">提醒这些组</a></article>`).join('')}</section>`);
  document.querySelector('.page-head')?.insertAdjacentHTML('beforeend','<a class="btn" href="queue.html?display=1">脱敏投屏</a>');
  document.querySelectorAll('[data-help-response]').forEach(b=>b.onclick=async()=>{const q=items.find(q=>q.id===b.dataset.helpResponse);if(!await request('queue',{id:q.id,handling:'viewed',feedback:q.feedback||'',urgent:!!q.urgent,reason:q.priorityReason||''},load()))return;modal(`回复${q.group}组`,`<p>${esc(q.reason)}</p><p>${esc(q.attempt||'')}</p><div class="field"><label for="responseHandling">处理状态</label><select id="responseHandling"><option value="viewed">教师已查看</option><option value="processing">处理中</option><option value="visiting">我去看看</option><option value="broadcast">已通过广播回应</option><option value="handled">已处理</option></select></div>${field('responseText','给小组的反馈',q.feedback)}<label class="check mt12"><input type="checkbox" id="responseUrgent" ${q.urgent?'checked':''}>优先处理</label>${field('responseReason','优先处理原因',q.priorityReason)}${button('saveResponse','保存回应')}`);$('saveResponse').onclick=async()=>{if(await request('queue',{id:q.id,handling:value('responseHandling'),feedback:value('responseText'),urgent:$('responseUrgent').checked,reason:value('responseReason')},load())){document.querySelector('.modal-mask')?.remove();queue()}}});
 }
 function report(){
  const s=load(),r=s.teachingReview||{};
  page('teacher','report','课后教学总结',`<div class="grid cols-2">${[['本课各阶段提交情况',s.groups.map(g=>`${g.id}组：${g.submitted}/5`)],['需要再讲清的数学条件',s.common.map(c=>c.title)],['老师帮助过哪些组？',s.queue.filter(q=>q.feedback).map(q=>`${q.group}组：${q.feedback}`)],['方案改过几次、为什么？',s.groups.map(g=>`${g.id}组：${g.version||'尚无版本'}`)]].map(([title,rows])=>`<section class="card"><h2>${title}</h2>${rows.map(text=>`<p>${esc(text)}</p>`).join('')}${s.groups.map(g=>`<a class="chip" href="group-detail.html?g=${g.id}">${g.id}组</a>`).join('')}</section>`).join('')}</div><section class="card mt16"><h2>教师教学记录</h2>${field('reviewDifficulty','学生普遍卡在哪里？',r.difficulty)}${field('reviewResponse','我怎么帮助他们？',r.response)}${field('reviewNext','下次准备怎样调整？',r.next)}${button('saveReview','保存复盘草稿')}<a class="btn" href="/api/export?format=json">导出本项目学习过程</a></section>`);
  $('saveReview').onclick=async()=>{if(await request('review',{difficulty:value('reviewDifficulty'),response:value('reviewResponse'),next:value('reviewNext')},s))toast('已保存到服务器')};
 }
 async function templates(){
  const result=await api('/api/projects'),projects=result.projects||[];
  page('teacher','project','复制一个项目来修改',`<section class="card"><div class="grid cols-2">${[['templateGrade','年级','grade'],['templateTextbook','教材','textbook'],['templateTopic','数学主题','topic'],['templateLessons','预计课时','lessons']].map(([id,label,key])=>`<div class="field"><label for="${id}">${label}</label><select id="${id}"><option value="">全部</option>${[...new Set(projects.map(p=>p[key]).filter(Boolean))].map(v=>`<option>${esc(String(v))}</option>`).join('')}</select></div>`).join('')}</div><div id="templateList" class="grid cols-2 mt16"></div></section>`);
  const draw=()=>{$('templateList').innerHTML=projects.filter(p=>[['templateGrade','grade'],['templateTextbook','textbook'],['templateTopic','topic'],['templateLessons','lessons']].every(([id,key])=>!value(id)||String(p[key])===value(id))).map(p=>`<article class="task"><h2>${esc(p.name)}</h2><p>${[p.grade,p.textbook,p.topic,p.lessons?`${p.lessons}课时`:''].filter(Boolean).map(v=>esc(String(v))).join(' · ')}</p><button type="button" class="btn primary" data-copy-source="${esc(p.id)}">复制为新项目草稿</button></article>`).join('');document.querySelectorAll('[data-copy-source]').forEach(b=>b.onclick=async()=>{const name=prompt('新项目名称');if(!name)return;const result=await request('copy',{name,sourceProjectId:b.dataset.copySource},load());if(result){await api('/api/projects',{method:'POST',body:JSON.stringify({action:'select',projectId:result.projectId})});location.href='project.html'}})};
  for(const id of ['templateGrade','templateTextbook','templateTopic','templateLessons'])$(id).onchange=draw;draw();
 }
 function teacherConfig(){
  if($('classroomConfig'))return;const s=load(),c=s.teacherConfig||{},b=s.taskBrief||{},target=document.querySelector('.main');if(!target)return;
  target.insertAdjacentHTML('beforeend',`<section class="card mt16" id="classroomConfig"><h2>任务与课堂配置</h2>${field('learnedKnowledge','这个班已经学过哪些知识？',c.learnedKnowledge)}${field('toolSuggestions','工具建议',c.toolSuggestions)}${field('offlineMaterials','线下材料',c.offlineMaterials)}${field('stageMinutes','各阶段建议用时',c.stageMinutes)}${field('idleMinutes','未更新提醒（分钟）',c.idleMinutes||'10','input')}<h3 class="mt16">简明任务说明</h3>${[['situation','这次发生了什么？'],['problem','我们要解决什么问题？'],['conditions','已知数值、单位与限制条件'],['output','最后提交什么？']].map(([k,label])=>field('brief-'+k,label,b[k])).join('')}<label class="check mt12"><input type="checkbox" id="briefConfirmed">已核对原始材料中的数字、单位与条件</label><h3 class="mt16">阶段任务卡</h3>${STAGES.map(st=>`<details class="task"><summary>${st.n} ${st.name}</summary>${[['goal','目标'],['action','当前动作'],['output','成果']].map(([k,label])=>field(`task-${st.n}-${k}`,label,c.stageTasks?.[st.n]?.[k]||'')).join('')}</details>`).join('')}<div class="toolbar">${button('saveClassroomConfig','保存课堂配置')}${button('copyProjectNew','复制一个项目来修改','ghost')}<a class="btn" href="/?projects">新建空项目</a></div></section>`);
  $('saveClassroomConfig').onclick=async()=>{const body=Object.fromEntries(['learnedKnowledge','toolSuggestions','offlineMaterials','stageMinutes','idleMinutes'].map(k=>[k,value(k)]));body.brief=Object.fromEntries(['situation','problem','conditions','output'].map(k=>[k,value('brief-'+k)]));body.confirm=$('briefConfirmed').checked;body.stageTasks=Object.fromEntries(STAGES.map(st=>[st.n,Object.fromEntries(['goal','action','output'].map(k=>[k,value(`task-${st.n}-${k}`)]))]));if(await request('config',body,s))toast('已保存到服务器')};
  $('copyProjectNew').onclick=async()=>{const name=prompt('新项目名称',s.project+' 副本');if(!name)return;const result=await request('copy',{name},load());if(result){await api('/api/projects',{method:'POST',body:JSON.stringify({action:'select',projectId:result.projectId})});location.reload()}};
 }
 let readWorkflow=()=>load().teacherConfig?.workflow||{};
 function fieldConfig(){
  if($('fieldConfiguration'))return;
  const s=load(),rules=s.teacherConfig?.fieldRules||{},catalog=s.fieldCatalog||[];
  const types=[['textarea','简短说明'],['text','文字或公式'],['number','数字'],['select','单选下拉'],['radio','单选'],['multiselect','多选'],['checkbox','勾选'],['file','照片或文件']];
  const rows=catalog.map(item=>{
   const rule={...item,...rules[item.id]};
   return `<details class="task" data-config-id="${item.id}" data-config-stage="${item.stage}" data-config-task="${item.task}"><summary>阶段${item.stage||'通用'} · ${esc(rule.title)}</summary><div class="form-grid">
    <label>字段标题<input data-field-setting="title" value="${esc(rule.title)}"></label>
    <label>学生说明<input data-field-setting="help" value="${esc(rule.help||'')}"></label>
    <label>输入类型<select data-field-setting="type">${types.filter(([key])=>item.fixedType?key===item.type:!['file','checkbox'].includes(key)).map(([key,label])=>`<option value="${key}" ${rule.type===key?'selected':''}>${label}</option>`).join('')}</select></label>
    <label>选择项（用 | 分隔）<input data-field-setting="options" value="${esc(rule.options||'')}"></label>
    <label>字数上限<input type="number" min="1" max="4000" data-field-setting="maxLength" value="${rule.maxLength||1000}"></label>
    <label>显示顺序<input type="number" min="0" max="200" data-field-setting="order" value="${rule.order||0}"></label>
    <label><input type="checkbox" data-field-setting="required" ${rule.required?'checked':''}>必须完成</label>
    <label><input type="checkbox" data-field-setting="expanded" ${rule.expanded!==false?'checked':''}>默认展开</label>
    <label>快捷提问<input data-field-setting="hint" value="${esc(rule.hint||'')}"></label>
    <label>帮助呈现<select data-field-setting="hintMode">${[['full','完整说明'],['short','简短提示'],['none','自由填写']].map(([key,title])=>`<option value="${key}" ${(rule.hintMode||'full')===key?'selected':''}>${title}</option>`).join('')}</select></label>
   </div><div data-field-preview></div></details>`;
  }).join('');
  document.querySelector('.main')?.insertAdjacentHTML('beforeend',`<section class="card mt16" id="fieldConfiguration"><h2>学生填写模板</h2><div class="toolbar"><label>阶段<select id="fieldStage"><option value="">全部</option><option value="0">通用工作区</option>${STAGES.map(st=>`<option value="${st.n}">${st.n} ${esc(st.name)}</option>`).join('')}</select></label><label>任务<select id="fieldTask"><option value="">全部</option>${[0,1,2,3].map(n=>`<option value="${n}">任务${n+1}</option>`).join('')}</select></label></div><div class="toolbar"><label>已保存模板<select id="savedFieldTemplates"><option value="">选择填写模板</option></select></label>${button('applyFieldTemplate','复制到当前设置','ghost')}${button('storeFieldTemplate','另存为填写模板','ghost')}</div>${rows}<div class="toolbar">${button('previewStudentPage','预览学生页面','ghost')}${button('previewFields','预览字段样式','ghost')}${button('saveFieldsConfig','保存填写模板')}</div></section>`);
  const filter=()=>document.querySelectorAll('[data-config-id]').forEach(row=>row.hidden=(value('fieldStage')!==''&&row.dataset.configStage!==value('fieldStage'))||(value('fieldTask')!==''&&row.dataset.configTask!==value('fieldTask')));
  $('fieldStage').onchange=filter;$('fieldTask').onchange=filter;
  const read=()=>Object.fromEntries([...document.querySelectorAll('[data-config-id]')].map(row=>[row.dataset.configId,Object.fromEntries([...row.querySelectorAll('[data-field-setting]')].map(el=>[el.dataset.fieldSetting,el.type==='checkbox'?el.checked:el.type==='number'?Number(el.value):el.value]))]));
  $('previewStudentPage').onclick=async()=>{
   try{
    const result=await api('/api/classroom/preview',{method:'POST',body:JSON.stringify({group:s.groupId||1,fieldRules:read(),workflow:readWorkflow()})});
    sessionStorage.setItem('mpt:preview:'+session().projectId,JSON.stringify(result));
    const selectedStage=Number(value('fieldStage')||2),selectedTask=value('fieldTask'),defaults={0:'workspace.html',1:'stage1-materials.html',2:'stage2-understanding.html',3:'stage3-model.html',4:'stage4-evidence.html',5:'stage5-product.html'};
    const route=selectedTask!==''&&selectedStage?C.taskRoute(selectedStage,Number(selectedTask)):defaults[selectedStage],url=new URL('/student/'+route,location.origin);
    url.searchParams.set('preview','1');url.searchParams.set('g',s.groupId||1);
    modal('学生页面预览',`<iframe title="学生页面预览" src="${esc(url.href)}" style="width:100%;height:70vh;border:0"></iframe>`);
   }catch(error){toast(error.message)}
  };
  $('previewFields').onclick=()=>{const current=read();document.querySelectorAll('[data-config-id]').forEach(row=>{if(row.hidden)return;const rule=current[row.dataset.configId];row.querySelector('[data-field-preview]').innerHTML=field('preview-'+row.dataset.configId,rule.title,'',rule.type,rule);row.open=true})};
  $('saveFieldsConfig').onclick=async()=>{if(await request('config',{fieldRules:read()},load()))toast('填写模板已保存')};
  workflowConfig();
  let templates=[];
  const refreshTemplates=async()=>{try{const result=await api('/api/classroom/field-templates');templates=result.templates||[];$('savedFieldTemplates').innerHTML='<option value="">选择填写模板</option>'+templates.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('')}catch(error){toast(error.message)}};
  refreshTemplates();
  $('storeFieldTemplate').onclick=async()=>{const name=prompt('填写模板名称');if(!name?.trim())return;try{await api('/api/classroom/field-templates',{method:'POST',body:JSON.stringify({name,stage:value('fieldStage')===''?-1:Number(value('fieldStage')),task:value('fieldTask')===''?-1:Number(value('fieldTask')),fieldRules:read(),workflow:readWorkflow()})});await refreshTemplates();toast('填写模板已另存')}catch(error){toast(error.message)}};
  const fillSettings=(row,settings,prefix)=>{for(const [key,value] of Object.entries(settings)){const el=row?.querySelector('[data-'+prefix+'-setting="'+key+'"]');if(!el)continue;if(el.multiple)[...el.options].forEach(option=>option.selected=value.includes(option.value));else if(el.type==='checkbox')el.checked=value===true;else el.value=value}};
  $('applyFieldTemplate').onclick=()=>{const template=templates.find(item=>item.id===value('savedFieldTemplates'));if(!template)return;for(const [key,rule] of Object.entries(template.fieldRules))fillSettings(document.querySelector('[data-config-id="'+key+'"]'),rule,'field');for(const [key,rule] of Object.entries(template.workflow))fillSettings(document.querySelector('[data-flow-key="'+key+'"]'),rule,'flow');$('fieldStage').value=template.stage<0?'':template.stage;$('fieldTask').value=template.task<0?'':template.task;filter();C.saveStatus('模板已复制，可以预览后保存')};
 }
 function workflowConfig(){
  const s=load(),workflow=s.teacherConfig?.workflow||{},tasks=STAGES.flatMap(stage=>stageTasks[stage.n].map((title,i)=>({key:stage.n+'.'+i,title,stage:stage.n,index:i})));
  const fields=(s.fieldCatalog||[]).filter(item=>item.path?.length);
  const nextPages=['','dashboard.html','workspace.html','stage1.html','stage2.html','stage3.html','stage4.html','stage5.html','stage3-model.html','stage3-graph.html','stage4-evidence.html','stage5-product.html','stage5-reflection.html'];
  document.querySelector('.main')?.insertAdjacentHTML('beforeend',`<section class="card mt16" id="workflowConfiguration"><h2>任务流程与完成条件</h2>${tasks.map(item=>{const rule=workflow[item.key]||{};return `<details class="task" data-flow-key="${item.key}"><summary>阶段${item.stage} · 任务${item.index+1} · ${esc(item.title)}</summary><div class="form-grid">
   <label><input type="checkbox" data-flow-setting="required" ${rule.required!==false?'checked':''}>必须完成</label>
   <label>前置任务<select multiple data-flow-setting="dependsOn">${tasks.filter(other=>other.stage<=item.stage&&other.key!==item.key).map(other=>`<option value="${other.key}" ${rule.dependsOn?.includes(other.key)?'selected':''}>阶段${other.stage} 任务${other.index+1} ${esc(other.title)}</option>`).join('')}</select></label>
   <label>完成条件<select data-flow-setting="mode">${[['default','保留任务检查规则'],['all','所选字段全部完成'],['any','所选字段至少完成一项'],['count','记录数量达到要求']].map(([key,title])=>`<option value="${key}" ${(rule.mode||'default')===key?'selected':''}>${title}</option>`).join('')}</select></label>
   <label>条件字段<select multiple data-flow-setting="fields">${fields.filter(field=>field.stage<=item.stage).map(field=>`<option value="${field.id}" ${rule.fields?.includes(field.id)?'selected':''}>${esc(field.title)}</option>`).join('')}</select></label>
   <label>记录类型<select data-flow-setting="metric">${[['evidence','有效证据'],['checkedEvidence','已检查证据'],['versions','方案版本'],['questions','小问题'],['members','成员'],['discussions','讨论记录']].map(([key,title])=>`<option value="${key}" ${(rule.metric||'evidence')===key?'selected':''}>${title}</option>`).join('')}</select></label>
   <label>至少几条<input type="number" min="1" max="50" data-flow-setting="minimum" value="${rule.minimum||1}"></label>
   <label>提交后的下一步<select data-flow-setting="next">${nextPages.map(path=>`<option value="${path}" ${rule.next===path?'selected':''}>${path||'留在当前阶段'}</option>`).join('')}</select></label>
  </div></details>`}).join('')}<div class="toolbar">${button('saveWorkflow','保存任务流程')}</div></section>`);
  readWorkflow=()=>Object.fromEntries([...document.querySelectorAll('[data-flow-key]')].map(row=>[row.dataset.flowKey,Object.fromEntries([...row.querySelectorAll('[data-flow-setting]')].map(el=>[el.dataset.flowSetting,el.multiple?[...el.selectedOptions].map(option=>option.value):el.type==='checkbox'?el.checked:el.type==='number'?Number(el.value):el.value]))]));
  $('saveWorkflow').onclick=async()=>{if(await request('config',{workflow:readWorkflow()},load()))toast('任务流程已保存')};
 }
 function decorateFields(root=document.querySelector('.student-app')){
  if(session().role!=='student'||!root)return;
  const specFor=control=>{
   let key=control.id;
   if(control.dataset.quantity)key='quantity-'+control.dataset.quantity;
   else if(control.dataset.questionRow!==undefined)key='question-child';
   else if(control.dataset.rehearsalRole!==undefined)key='rehearsal-role';
   else if(control.dataset.rehearsalCheck!==undefined)key='rehearsal-check-'+control.dataset.rehearsalCheck;
   else if(control.dataset.check!==undefined)key='math-check-'+control.dataset.check;
   else if(control.dataset.modelCondition!==undefined)key='model-condition-'+control.dataset.modelCondition;
   else if(control.dataset.work)key='workspace-'+control.dataset.work;
   else if(control.dataset.w)key='product-'+control.dataset.w;
   else if(control.dataset.p)key='product-'+control.dataset.p;
   return load().fieldCatalog?.find(spec=>spec.id===key)||fieldSpec(key||'');
  };
  root.querySelectorAll('input,textarea,select').forEach((control,index)=>{
   if(control.closest('[data-field-id]')||control.closest('[data-choice-for]'))return;
   const spec=specFor(control);if(!spec.id)return;
   const rule=fieldRule(spec.id),wrapper=control.closest('.field,label.check,label.task')||control.parentElement;
   if(wrapper.dataset.fieldId)return;
   let id=control.id||spec.id+'-'+[...document.querySelectorAll('input,textarea,select')].indexOf(control);if(!control.id)while($(id))id+='-next';control.id=id;wrapper.dataset.fieldId=id;wrapper.dataset.schemaId=spec.id;
   const isTable=wrapper.tagName==='TD',originalText=control.type==='checkbox'?control.checked:control.value;
   if(!spec.fixedType&&rule.type!==control.type&&!(rule.type==='textarea'&&control.tagName==='TEXTAREA')){
    const template=document.createElement('template');template.innerHTML=controlMarkup(id,originalText,rule,control);
    const next=template.content.querySelector('#'+CSS.escape(id));
    for(const attr of control.attributes)if(attr.name.startsWith('data-'))next.setAttribute(attr.name,attr.value);
    next.disabled=control.disabled;next.readOnly=control.readOnly;next.onchange=control.onchange;next.oninput=control.oninput;
    control.replaceWith(template.content);
   }
   const active=$(id);
   active.setAttribute('aria-label',rule.title||spec.title);active.maxLength=Number(rule.maxLength)||1000;
   if(isTable)active.title=rule.help||'';
   if(!isTable){
   let label=wrapper.querySelector(':scope > label');
   if(wrapper.tagName==='LABEL'){
    for(const child of [...wrapper.childNodes])if(child.nodeType===3)child.remove();
    const span=document.createElement('span');span.textContent=(rule.title||spec.title)+(rule.required?'（必须完成）':'（可以跳过）');wrapper.append(span);
   }else{
    if(!label){label=document.createElement('label');wrapper.prepend(label)}
    label.htmlFor=id;const member=control.dataset.rehearsalRole!==undefined?load().members[Number(control.dataset.rehearsalRole)]+' · ':'';label.textContent=member+(rule.title||spec.title)+(rule.required?'（必须完成）':'（可以跳过）');
   }
   }
   if(rule.help&&rule.hintMode!=='none'){const help=document.createElement('small');help.className='field-help';help.textContent=rule.hintMode==='short'?rule.help.split(/[。！？]/)[0]:rule.help;wrapper.append(help)}
   if(rule.hint&&rule.hintMode!=='none'){const link=document.createElement('a');link.className='link';link.href='ai.html';link.dataset.fieldHelp=id;link.dataset.schemaId=spec.id;link.textContent='问问迈谱助手';wrapper.append(link)}
  });
  const parents=new Set(),primaryCounts=new Map();
  root.querySelectorAll('[data-field-id]').forEach(wrapper=>{
   const spec=fieldSpec(wrapper.dataset.schemaId||wrapper.dataset.fieldId),rule=fieldRule(spec.id||wrapper.dataset.fieldId);
   wrapper.style.order=Number(rule.order)||0;if(wrapper.tagName!=='TD')parents.add(wrapper.parentElement);
   if(!wrapper.querySelector('[data-field-status]')){
    const status=document.createElement('small');status.dataset.fieldStatus='';status.setAttribute('role','status');
    const update=()=>{const controls=[...wrapper.querySelectorAll('input,textarea,select')].filter(el=>!el.closest('[data-choice-for]')),completed=controls.some(el=>el.type==='checkbox'?el.checked:el.type==='file'?el.files.length:el.value.trim());const canonical=wrapper.querySelector('#'+CSS.escape(wrapper.dataset.fieldId)),tooLong=canonical?.value?.length>Number(rule.maxLength||4000),checked=completed&&(/^(math-check-|rehearsal-check-)/.test(spec.id)||['recordVerified','issueRechecked'].includes(spec.id));status.textContent=tooLong?'需要补充':checked?'已检查':completed?'已填写':rule.required?'需要补充':'未填写'};
    wrapper.append(status);wrapper.addEventListener('input',update);wrapper.addEventListener('change',update);update();
   }
   const container=wrapper.parentElement,primary=primaryCounts.get(container)||0;primaryCounts.set(container,primary+1);
   if(wrapper.tagName!=='TD'&&(rule.expanded===false||(primary>=5&&!wrapper.closest('.modal')))&&wrapper.parentElement?.dataset.fieldFold!==wrapper.dataset.fieldId){
    const details=document.createElement('details'),summary=document.createElement('summary');details.dataset.fieldFold=wrapper.dataset.fieldId;
    summary.textContent=rule.title||wrapper.querySelector('label')?.textContent||'更多填写项';wrapper.replaceWith(details);details.append(summary,wrapper);
   }
  });
  parents.forEach(parent=>{
   const nodes=[...parent.children].filter(el=>el.matches('[data-field-id],[data-field-fold]'));
   if(nodes.length<2)return;
   const sorted=nodes.slice().sort((a,b)=>Number((a.matches('[data-field-id]')?a:a.querySelector('[data-field-id]')).style.order)-Number((b.matches('[data-field-id]')?b:b.querySelector('[data-field-id]')).style.order));
   const markers=nodes.map(node=>{const marker=document.createComment('field');node.replaceWith(marker);return marker});
   markers.forEach((marker,i)=>marker.replaceWith(sorted[i]));
  });
 }
 function learningHistory(append=false){
  const s=load(),operations=[...(s.events||[]),...(s.fieldHistory||[]).map(row=>({ts:row.time,stage:row.stage,actor:row.actor,data:row.title+'：'+row.after.map(value=>typeof value==='string'||typeof value==='number'?value:value===true?'已勾选':value===false?'取消勾选':value?'更新了记录':'暂未填写').join('；')})),...(s.roleAssignments||[]).map(row=>({ts:row.time,stage:row.stage,actor:row.actor,data:row.members.map((m,i)=>m+'：'+row.roles[i]).join('；')}))].sort((a,b)=>String(b.ts||'').localeCompare(String(a.ts||'')));
  const html=`<section class="card"><div class="toolbar"><label>阶段<select id="historyStage"><option value="">全部</option>${STAGES.map(st=>`<option value="${st.n}">${st.n} ${esc(st.name)}</option>`).join('')}</select></label><label>由谁完成<select id="historyMember"><option value="">全部</option>${[...new Set(operations.map(row=>row.actor).filter(Boolean))].map(actor=>`<option>${esc(actor)}</option>`).join('')}</select></label></div><div id="historyRows"></div></section>${timeline(s)}`;if(append)document.querySelector('.main')?.insertAdjacentHTML('beforeend',html);else page('student','history','学习过程记录',html);
  const draw=()=>{$('historyRows').innerHTML=operations.filter(row=>(!value('historyStage')||String(row.stage)===value('historyStage'))&&(!value('historyMember')||row.actor===value('historyMember'))).map(row=>`<article class="task"><b>${esc(localTime(row.ts))} · 阶段${row.stage||''} · ${esc(row.actor||'小组')}</b><p>${esc(row.data||'保存记录')}</p></article>`).join('')||'<p>暂无记录</p>'};$('historyStage').onchange=draw;$('historyMember').onchange=draw;draw();
 }
 const studentTerms={"观点协商与证据型异议":"讨论不同想法","观点协商":"讨论不同想法","低威胁表达模板":"友好表达句式","复杂冲突转教师":"讨论无法解决时请老师帮助","协商者":"讨论组织者","第一轮想法":"每个人先想的内容","问题树":"问题拆分表","可验证的数学子问题":"能用计算、图或数据检查的小问题","共同问题空间":"小组共同记录","共同问题":"共同要解决的问题","已知信息":"题目给出的信息","现实约束":"必须满足的条件","当前模型":"目前的数学方案","证据/待核验":"已经检查的根据/还需要检查的内容","变量、符号和单位定义清楚":"字母、符号和单位都写清楚","数量关系有题目条件或证据支持":"算式能从题目条件中找到根据","过程可以复核":"别人能按我们的步骤重新检查","取值范围":"这个量可以取哪些值","实际范围":"这个量可以取哪些值","边界情况":"最小、最大或接近限制的情况","证据链检查":"检查这些根据能否支持结论","已核验":"已检查","待核验":"还需要检查","关联模型版本":"这条记录对应的方案版本","问题—模型—证据—结论链":"从问题到结论的完整过程","公开表达前检查":"上台前检查","公开表达检查":"上台前检查","成果对象":"成果给谁看、谁会使用","服务对象":"成果给谁看、谁会使用","局限与现实条件":"结论适用的条件和还没有考虑的情况","数学模型或关系":"数学方案","证据与验证":"用什么根据、怎样检查","过程事实回看":"回顾这次实际做过的事","最高提示层级":"这次用到的帮助程度","模型版本":"方案版本","AI提示":"迈谱给过的帮助","自由AI工作区":"问问迈谱助手","自由工作区":"问问迈谱助手","输入问题":"写下你现在想问的内容","本轮卡点类型（可修改）":"我们卡在哪一步","已检查共享工作区":"已查看共享工作区（小组已经填写的内容）","等待教师处理":"已发给老师，等待回应","成员信息已固化":"成员名单已确认","责任说明":"每个人要做什么","推荐适合的数学模型":"帮我想想可以用什么数学方法","第一轮数学责任":"每位成员先负责的数学任务","阶段规则":"每一步要完成什么","数量关系":"数量之间的关系","模型表征与检验":"用算式、图或表完成表示并检查","计算、测量或数据依据":"计算结果、测量结果或数据","这个方案在什么条件下成立":"什么情况下可以使用这个方案","形成模型修订与理由":"记录方案改了哪里、为什么","3D计算器":"立体图形工具","三维曲面、空间曲线和点":"立体图形、空间中的线和点","引用已有材料":"使用小组已经保存的记录","最终方案":"最后确定的方案","回答范围":"需要回答哪些问题","适用范围":"结论在哪些情况下适用","验证方法":"我们怎样检查的","结论依据":"结论根据什么得出的","现实因素":"实际中还要考虑的情况","历史分工":"以前的分工记录","跨阶段记录":"学习过程记录","操作记录":"每次保存和修改的记录","操作成员":"由谁完成","二级页面":"","三级页面":"","变量":"题目中会变化、需要研究的量","迁移":"把学到的方法用到新问题","局限":"还没有考虑到的情况","提示链":"这次问题收到的帮助记录","成员信息已锁定":"成员名单已确认","结论说明局限":"结论还没有考虑到哪些情况","过程反思":"回顾这次是怎样完成的","展示预演":"展示预演（上台前的排练）","约束":"必须遵守的条件","支架预设":"学习帮助设置","建模表征":"用算式、图或表表示","证据包":"计算和图表记录","复杂冲突":"讨论无法解决时请老师帮助"};
 Object.assign(studentTerms,{'变量、关系、约束快照':'当时保存的研究量、数量关系和必须满足的条件','数学建模':'用数学方法描述问题','模型表征':'用算式、图或表表示','证据链':'从问题到结论的根据','提示层级':'帮助程度','教师升级队列':'待老师处理的求助','共性卡点':'多个小组遇到的相同困难','班级学习脉搏':'全班进度概况','阶段分布':'各组目前做到哪一步','模型调用':'AI请求次数','会话有效期':'登录保持时间','安全Cookie':'登录安全设置','自由文本':'文字提问','自由生图':'AI图片生成','去标识化':'用小组编号代替姓名','方案版本':'方案的不同版本','支架':'学习帮助','核验':'检查'});
 function studentLabels(){
  const translate=text=>Object.entries(studentTerms).sort((a,b)=>b[0].length-a[0].length).reduce((text,[from,to])=>text.split(from).join(to),text).replace(/模型V(\d+)/g,'数学方案第$1版').replace(/^V(\d+)$/,'第$1版方案');
  document.querySelectorAll('.student-app h1,.student-app h2,.student-app h3,.student-app label,.student-app button,.student-app a,.student-app .eyebrow,.student-app dt,.student-app .mini-stat>span,.student-app .locked-rule>span,.student-app .tl-body>b').forEach(el=>{
   for(const child of el.childNodes)if(child.nodeType===3)child.textContent=translate(child.textContent);
  });
  const conceptNotes={推理:'根据题目给出的信息，一步步说明理由。',建模:'把实际问题整理成数量之间的关系，再用数学方法检查。'};
  for(const [term,explanation] of Object.entries(conceptNotes)){const target=[...document.querySelectorAll('.student-app h1,.student-app h2,.student-app h3')].find(el=>el.textContent.includes(term));if(target){const details=document.createElement('details');details.className='small';const summary=document.createElement('summary');summary.textContent=term+'是什么意思？';const text=document.createElement('p');text.textContent=explanation;details.append(summary,text);target.after(details)}}
  document.querySelectorAll('.student-app [placeholder]').forEach(el=>el.placeholder=translate(el.placeholder));
 }
 function decorate(){
  const s=load(),id=document.body.dataset.page;
  if(id!=='student-help')clearInterval(window.mapleQueueTimer);
  if(id!=='teacher-queue')clearInterval(window.mapleProjectionTimer);
  if(id.startsWith('student-')&&s.questionTree?.slice(1).some(Boolean)&&Number(id.match(/stage([3-5])/)?.[1])>=3){
   document.querySelector('.page-head')?.insertAdjacentHTML('afterend',`<details class="card mb16"><summary>回看我们拆分的小问题</summary>${s.questionTree.slice(1).map((question,i)=>question?`<p>${esc(question)} ${['stage3-model.html','stage3-graph.html','stage4-evidence.html'].includes(s.workspace.questionLinks?.[i])?`<a href="${s.workspace.questionLinks[i]}">继续处理</a>`:''}</p>`:'').join('')}</details>`);
  }
  if(id.startsWith('student-'))queueMicrotask(()=>{decorateFields();studentLabels();C.installDraftAutosave();if(C.preview()){document.querySelector('.main')?.insertAdjacentHTML('afterbegin','<p class="card">学生页面预览 · 可以试填查看状态</p>');document.querySelectorAll('a[href]').forEach(link=>{const url=new URL(link.href,location.href);if(url.origin===location.origin&&url.pathname.startsWith('/student/')){url.searchParams.set('preview','1');url.searchParams.set('g',s.groupId||1);link.href=url.href}});document.querySelectorAll('input[type="file"],[data-logout],[data-project-menu]').forEach(el=>el.disabled=true)}});
  document.querySelectorAll('.student-app [data-nav-key^="stage-"]>span:last-child').forEach(el=>{const n=Number(el.parentElement.dataset.navKey.split('-')[1]);if(n)el.innerHTML=`${n} ${esc(STAGES[n-1].name)}<small class="nav-action">${sub[n-1]}</small>`});
  if(id==='teacher-project'){teacherConfig();fieldConfig()}
  if(id==='student-governance')learningHistory(true);
  if(id.startsWith('student-')){
   const taskPages={'student-stage1-materials':'1.0','student-stage1-roles':'1.2','student-stage2-understanding':'2.2','student-stage2-question-tree':'2.3','student-stage3-model':'3.2','student-stage3-graph':'3.3','student-stage3-check':'3.3','student-stage3-compare':new URLSearchParams(location.search).get('stage')==='4'?'4.2':'3.3','student-stage4-evidence':'4.1','student-stage4-discussion':'4.2','student-stage5-product':'5.2','student-stage5-rehearsal':'5.1','student-stage5-reflection':'5.3'},taskKey=taskPages[id],next=s.teacherConfig?.workflow?.[taskKey]?.next;
   if(next){const link=document.createElement('a');link.className='btn primary';link.href=next;link.textContent='进入下一步';link.onclick=event=>{const [stage,task]=taskKey.split('.').map(Number);if(!C.stageTaskReady(load(),stage,task)){event.preventDefault();toast('请先保存并完成当前任务')}};document.querySelector('.page-head')?.append(link)}
  }
  if(id==='teacher-governance')document.querySelector('.main')?.insertAdjacentHTML('beforeend',`<section class="card mt16"><h2>各组迈谱助手额度</h2>${(s.groups||[]).map(group=>`<p>${group.id}组 · 总额度 ${s.freeAiQuota||30} · 已使用 ${group.freeAiUsage||0} · 剩余 ${Math.max(0,(s.freeAiQuota||30)-(group.freeAiUsage||0))}</p>`).join('')}</section>`);
  if(id==='student-dashboard'&&!$('currentTaskCard')){const t=s.teacherConfig?.stageTasks?.[s.currentStage]||{};document.querySelector('.dashboard-status')?.insertAdjacentHTML('afterend',`<div id="currentTaskCard" class="current-task"><b>这一步要做什么？</b><span>${esc(t.goal||sub[s.currentStage-1])}</span><span>先完成：${esc(t.action||stageTasks[s.currentStage]?.[0]||'')}</span><span>完成后留下：${esc(t.output||STAGES[s.currentStage-1].output)}</span></div>`)}
  if(id.startsWith('student-')&&!document.querySelector('.page-head a[href="ai.html"]'))document.querySelector('.page-head')?.insertAdjacentHTML('beforeend','<a class="btn" href="ai.html">问问迈谱助手</a>');
  if(id==='student-ai'){
   const free=s.freeAi||{},used=Number(free.usage||0),quota=Number(s.freeAiQuota||30);document.querySelector('.page-head')?.insertAdjacentHTML('afterend',`<div class="toolbar">总额度 ${quota} · 已使用 ${used} · 剩余 ${Math.max(0,quota-used)}</div>`);
   const composer=document.querySelector('.ai-composer');if(composer&&!$('aiBoundaries'))composer.insertAdjacentHTML('beforebegin',`<div id="aiBoundaries" class="toolbar">${['帮我读懂这个条件','检查我的式子有没有漏掉单位','提醒我下一步该想什么'].map(t=>`<button class="btn sm" type="button" data-safe-question="${t}">${t}</button>`).join('')}</div>`);
   const params=new URLSearchParams(location.search);if(params.has('prompt')){$('aiPrompt').value=[params.get('field')?'当前填写项：'+(s.fieldCatalog?.find(item=>item.id===params.get('field'))?.title||params.get('field')):'',params.get('prompt')].filter(Boolean).join('\n')}
   document.querySelectorAll('[data-safe-question]').forEach(b=>b.onclick=()=>{$('aiPrompt').value=b.dataset.safeQuestion;$('aiPrompt').focus()});
   if(!s.freeAiEnabled||!s.aiOpen){$('sendAi')?.setAttribute('disabled','');if($('aiPrompt'))$('aiPrompt').placeholder='请向老师求助'}
  }
  if(id==='student-help'){
   const drawStatus=async()=>{try{const result=await api('/api/state');const items=result.state?.queue||[];const panel=$('studentQueueStatus')||document.createElement('section');panel.id='studentQueueStatus';panel.className='card mt16';panel.innerHTML='<h2>发给老师的求助</h2>'+items.map(row=>`<p>${esc(({received:'已发送',viewed:'教师已查看',processing:'处理中',visiting:'处理中',broadcast:'已回应',handled:'已回应'})[row.handling]||'已发送')} · ${esc(row.feedback||'等待老师回应')}</p>`).join('');if(!panel.isConnected)document.querySelector('.help-conversation')?.append(panel)}catch(_){}};
   drawStatus();clearInterval(window.mapleQueueTimer);window.mapleQueueTimer=setInterval(()=>{if(!document.hidden)drawStatus()},5000);
   const labels={'L1':'给我一点方向','L2':'帮我们把问题分成几步','L3':'只检查我们卡住的这一处'};
   document.querySelectorAll('[data-help-level]').forEach(b=>{if(b.dataset.helpLevel==='4'){b.hidden=true;return}b.textContent=labels['L'+b.dataset.helpLevel]||b.textContent});
   const current=s.activeHelpThread||s.helpThreads?.at(-1);if(current&&!$('helpNextAction')){
    document.querySelector('.help-conversation')?.insertAdjacentHTML('beforeend',`<section class="task mt16">${field('helpNextAction','我们接下来准备做什么？',s.workspace.next)}${(s.queue||[]).filter(q=>q.threadId===current.id&&q.feedback).map(q=>`<article class="task"><b>老师的回应</b><p>${esc(q.feedback)}</p></article>`).join('')}<div class="toolbar">${button('helpContinue','继续尝试')}${button('helpUnresolved','还没解决','warn')}${button('helpResolved','问题已解决','green')}</div></section>`);
    for(const [key,outcome] of [['helpContinue','continue'],['helpUnresolved','unresolved'],['helpResolved','resolved']])if($(key))$(key).onclick=async()=>{if(await request('help-feedback',{threadId:current.id,next:value('helpNextAction'),outcome},load())){toast('已保存到服务器');location.reload()}};
   }
   const form=$('helpDone');if(form)form.previousElementSibling.textContent='到目前为止，我们试过什么？';
  }
  if(id==='student-stage5-product'&&!$('productPhotoGallery')){
   document.querySelector('.main')?.insertAdjacentHTML('beforeend',productPhotos(s,!s.stageSubmitted?.[5]));
   $('productPhotoGallery').onclick=async event=>{const button=event.target.closest('[data-remove-product-photo]');if(!button)return;const x=deepClone(load());readProductDraft(x);x.product.photos.splice(Number(button.dataset.removeProductPhoto),1);if(await save(x)){const gallery=$('productPhotoGallery');const template=document.createElement('template');template.innerHTML=productPhotos(load(),true);gallery.innerHTML=template.content.firstElementChild.innerHTML}};
  }
  if(id==='student-stage5-product'&&!s.stageSubmitted?.[5]&&!$('productPhoto')){
   document.querySelector('.product-layout')?.insertAdjacentHTML('afterend',`<section class="card mt16"><div class="field"><label>拍照记录成果</label><input type="file" accept="image/*" capture="environment" multiple id="productPhoto"></div>${button('saveProductPhoto','保存成果照片')}</section>`);
   $('saveProductPhoto').onclick=async()=>{const files=$('productPhoto').files;if(!files.length)return;try{const uploaded=await uploadEvidenceFiles([...files]);const x=deepClone(load());readProductDraft(x);x.product.photos=[...(x.product.photos||[]),...uploaded];event(x,'product_photo','保存成果照片');if(await save(x)){toast('成果照片已保存');const template=document.createElement('template');template.innerHTML=productPhotos(load(),true);$('productPhotoGallery').innerHTML=template.content.firstElementChild.innerHTML}}catch(error){toast(error.message)}};
  }
  if(id==='student-stage5-product'&&!$('productVersions')){
   document.querySelector('.main')?.insertAdjacentHTML('beforeend',`<div id="productVersions">${timeline(s)}<section class="card mt16"><h2>引用已有材料</h2><div class="field"><label for="productModelRef">最终方案</label><select id="productModelRef"><option value="">选择方案版本</option>${s.versions.map(v=>`<option value="${esc(v.v)}" ${s.product.modelVersion===v.v?'selected':''}>${esc(String(v.v).replace(/^V(\d+)$/,'第$1版方案'))}</option>`).join('')}</select></div>${s.evidence.map(e=>`<label class="check mt12"><input type="checkbox" data-product-evidence="${esc(e.id)}" ${(s.product.evidenceRefs||[]).includes(e.id)?'checked':''}>${esc(e.text||e.file?.name||'证据')}</label>`).join('')}${!s.evidence.length?'<p>还没有关联证据，可以先保存草稿。</p>':''}${button('saveProductRefs','保存材料引用')}</section></div>`);
   $('saveProductRefs').onclick=async()=>{const x=deepClone(load());x.product.modelVersion=value('productModelRef');x.product.evidenceRefs=[...document.querySelectorAll('[data-product-evidence]:checked')].map(el=>el.dataset.productEvidence);if(await save(x))toast('已保存到服务器')};
  }
  if(id==='student-stage3-graph'&&!$('representationCheck')){
   document.querySelector('.main')?.insertAdjacentHTML('afterbegin',`<section class="card mb16" id="representationCheck"><h2>画图、列表或列式，再检查</h2><div class="chips">${['列表试一试','画图看变化','写算式进行计算'].map((t,i)=>`<button type="button" class="btn" data-route-type="${i}">${t}</button>`).join('')}</div>${field('representationMeaning','你们从图、表或算式里发现了什么？',s.mathTools?.representation?.meaning)}${field('representationCheckText','怎样确认结果没算错？',s.mathTools?.representation?.check)}${button('saveRepresentation','保存检验记录')}</section>`);
   let selected=s.mathTools?.representation?.route||0;document.querySelectorAll('[data-route-type]').forEach(b=>{b.classList.toggle('primary',Number(b.dataset.routeType)===selected);b.onclick=()=>{selected=Number(b.dataset.routeType);document.querySelectorAll('[data-route-type]').forEach(el=>el.classList.toggle('primary',el===b));const tab=document.querySelector('[data-desmos-tool="'+(selected===2?'scientific':'graph')+'"]');tab?.click();(tab||document.querySelector('.desmos-host'))?.scrollIntoView({behavior:'smooth',block:'start'})}});
   $('saveRepresentation').onclick=async()=>{const x=deepClone(load());x.mathTools.representation={route:selected,meaning:value('representationMeaning'),check:value('representationCheckText'),modelVersion:s.versions.at(-1)?.v};event(x,'math_tool_save','保存表征检验记录');if(await save(x))toast('已保存到服务器')};
  }
  if(id==='teacher-broadcast'&&!$('broadcastTargets')){
   const targets=new URLSearchParams(location.search).get('groups')?.split(',').map(Number)||s.groups.map(g=>g.id);
   $('bcText')?.parentElement.insertAdjacentHTML('beforebegin',`<fieldset id="broadcastTargets"><legend>接收小组</legend>${s.groups.map(g=>`<label class="check"><input type="checkbox" data-target-group="${g.id}" ${targets.includes(g.id)?'checked':''}>${g.id}组</label>`).join('')}</fieldset>`);
  }
  if(id==='student-dashboard'&&Object.keys(s.evaluations||{}).length&&!$('publishedEvaluation')){const ev=s.evaluations[s.groupId];if(ev)document.querySelector('.main')?.insertAdjacentHTML('beforeend',`<section class="card mt16" id="publishedEvaluation"><h2>老师的评价</h2><p>${esc(ev.comment||'')}</p></section>`)}
  if(['student-stage2-understanding','student-stage3-compare','student-stage5-product'].includes(id)&&!$('roleReminder')&&s.roleAssignments?.at(-1)?.stage!==s.currentStage)document.querySelector('.main')?.insertAdjacentHTML('afterbegin',`<div class="toolbar" id="roleReminder"><a class="btn sm" href="group.html">确认本阶段分工</a><button class="btn sm" type="button" id="dismissRoles">收起</button></div>`);
  if($('dismissRoles'))$('dismissRoles').onclick=()=>$('roleReminder').remove();
 }
 return {decorate,decorateFields,pages:{'student-stage1-materials':materials,'student-stage2-understanding':understanding,'student-stage2-question-tree':questions,'student-stage4-discussion':discussion,'student-stage4-evidence':evidence,'student-stage5-reflection':reflection,'student-stage3-model':model,'student-stage3-compare':compare,'student-history':learningHistory,'student-group':roles,'teacher-evaluation':evaluation,'teacher-stages':stages,'teacher-group-situation':groups,'teacher-group-detail':readonly,'teacher-evaluation-evidence':readonly,'teacher-queue':queue,'teacher-report':report,'teacher-template':templates}};
};
