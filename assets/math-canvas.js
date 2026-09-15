(function(){
const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
const finite=value=>Number.isFinite(Number(value))?Number(value):0;
const formatTick=value=>{
 const normalized=Math.abs(value)<1e-10?0:value;
 const abs=Math.abs(normalized);
 if(abs>=10000||(abs>0&&abs<.001))return normalized.toExponential(1);
 return Number(normalized.toFixed(abs<1?2:abs<10?1:0)).toString();
};
const niceStep=raw=>{
 const power=10**Math.floor(Math.log10(Math.max(raw,1e-9)));
 const ratio=raw/power;
 const factor=ratio<=1?1:ratio<=2?2:ratio<=5?5:10;
 return factor*power;
};
const parseData=value=>String(value||'').split(/[,，\s]+/).map(Number).filter(Number.isFinite);

function mount(canvas,getModel,onResult){
 if(window.__mapleMathCanvas)window.__mapleMathCanvas.destroy();
 const ctx=canvas.getContext('2d');
 const viewport={scale:46,offsetX:0,offsetY:0};
 let cssWidth=0,cssHeight=0,drag=null,destroyed=false,showGrid=true;
 const wrap=canvas.closest('.math-canvas-wrap')||canvas.parentElement;

 const pointToScreen=(x,y)=>({x:cssWidth/2+viewport.offsetX+x*viewport.scale,y:cssHeight/2+viewport.offsetY-y*viewport.scale});
 const screenToPoint=(x,y)=>({x:(x-cssWidth/2-viewport.offsetX)/viewport.scale,y:(cssHeight/2+viewport.offsetY-y)/viewport.scale});
 const readModel=()=>{const model=getModel();return {...model,values:model.values||{}}};

 function resize(){
  const rect=canvas.getBoundingClientRect();
  const nextWidth=Math.max(320,Math.round(rect.width));
  const nextHeight=Math.max(280,Math.round(rect.height));
  if(nextWidth===cssWidth&&nextHeight===cssHeight)return draw();
  cssWidth=nextWidth;cssHeight=nextHeight;
  const dpr=clamp(window.devicePixelRatio||1,1,2);
  canvas.width=Math.round(cssWidth*dpr);canvas.height=Math.round(cssHeight*dpr);
  ctx.setTransform(dpr,0,0,dpr,0,0);
  draw();
 }

 function drawGrid(){
  const origin=pointToScreen(0,0),major=niceStep(76/viewport.scale),minor=major/5;
  const min=screenToPoint(0,cssHeight),max=screenToPoint(cssWidth,0);
  ctx.fillStyle='#fff';ctx.fillRect(0,0,cssWidth,cssHeight);
  const grid=(step,color,width)=>{
   ctx.beginPath();ctx.strokeStyle=color;ctx.lineWidth=width;
   for(let x=Math.ceil(min.x/step)*step;x<=max.x+step/2;x+=step){const px=pointToScreen(x,0).x;ctx.moveTo(px,0);ctx.lineTo(px,cssHeight)}
   for(let y=Math.ceil(min.y/step)*step;y<=max.y+step/2;y+=step){const py=pointToScreen(0,y).y;ctx.moveTo(0,py);ctx.lineTo(cssWidth,py)}
   ctx.stroke();
  };
  grid(minor,'#edf1f5',1);grid(major,'#d2d9e1',1);
  ctx.strokeStyle='#55616e';ctx.lineWidth=1.35;ctx.beginPath();
  if(origin.y>=0&&origin.y<=cssHeight){ctx.moveTo(0,origin.y);ctx.lineTo(cssWidth,origin.y);ctx.lineTo(cssWidth-7,origin.y-4);ctx.moveTo(cssWidth,origin.y);ctx.lineTo(cssWidth-7,origin.y+4)}
  if(origin.x>=0&&origin.x<=cssWidth){ctx.moveTo(origin.x,cssHeight);ctx.lineTo(origin.x,0);ctx.lineTo(origin.x-4,7);ctx.moveTo(origin.x,0);ctx.lineTo(origin.x+4,7)}
  ctx.stroke();
  ctx.fillStyle='#526171';ctx.font='12px "Microsoft YaHei",sans-serif';
  ctx.textAlign='center';ctx.textBaseline='top';
  const labelY=clamp(origin.y+5,4,cssHeight-18);
  for(let x=Math.ceil(min.x/major)*major;x<=max.x+major/2;x+=major){if(Math.abs(x)<major/10)continue;const px=pointToScreen(x,0).x;if(px>22&&px<cssWidth-18)ctx.fillText(formatTick(x),px,labelY)}
  ctx.textAlign='right';ctx.textBaseline='middle';
  const labelX=clamp(origin.x-6,32,cssWidth-5);
  for(let y=Math.ceil(min.y/major)*major;y<=max.y+major/2;y+=major){if(Math.abs(y)<major/10)continue;const py=pointToScreen(0,y).y;if(py>12&&py<cssHeight-12)ctx.fillText(formatTick(y),labelX,py)}
  if(origin.x>=8&&origin.x<=cssWidth-8&&origin.y>=8&&origin.y<=cssHeight-8){ctx.textAlign='right';ctx.textBaseline='top';ctx.fillText('0',origin.x-6,origin.y+5)}
 }

 function drawFunction(kind,values){
  const k=finite(values.k),a=finite(values.a),b=finite(values.b),c=finite(values.c);
  const evaluate=x=>kind==='quadratic'?a*x*x+b*x+c:kind==='inverse'?k/x:kind==='proportional'?k*x:k*x+b;
  ctx.strokeStyle='#1879a4';ctx.lineWidth=2.4;ctx.beginPath();let drawing=false,lastY=0;
  for(let px=0;px<=cssWidth;px+=1){
   const x=screenToPoint(px,0).x,y=evaluate(x),py=pointToScreen(0,y).y;
   const valid=Number.isFinite(y)&&Math.abs(py)<cssHeight*4;
   if(!valid||kind==='inverse'&&(Math.abs(x)<2/viewport.scale||drawing&&Math.abs(py-lastY)>cssHeight*.8)){drawing=false;continue}
   if(!drawing){ctx.moveTo(px,py);drawing=true}else ctx.lineTo(px,py);lastY=py;
  }
  ctx.stroke();
  return kind==='quadratic'?`二次函数 y=${a}x²+${b}x+${c}`:kind==='inverse'?`反比例函数 y=${k}/x（x 不能为 0）`:kind==='proportional'?`正比例函数 y=${k}x`:`一次函数 y=${k}x+${b}`;
 }

 function drawStatistics(values){
  const nums=parseData(values.data);if(!nums.length)return '';
  const baseline=pointToScreen(0,0).y,max=Math.max(...nums.map(Math.abs),1),barWidth=Math.min(52,Math.max(10,cssWidth/(nums.length*1.8))),gap=Math.max(5,barWidth*.35),total=nums.length*barWidth+(nums.length-1)*gap,start=cssWidth/2-total/2;
  ctx.font='12px "Microsoft YaHei",sans-serif';ctx.textAlign='center';ctx.textBaseline='bottom';
  nums.forEach((value,index)=>{const height=value/max*Math.min(cssHeight*.34,130),x=start+index*(barWidth+gap),top=baseline-height;ctx.fillStyle='rgba(24,121,164,.72)';ctx.fillRect(x,Math.min(top,baseline),barWidth,Math.abs(height));ctx.fillStyle='#35536b';ctx.fillText(formatTick(value),x+barWidth/2,Math.min(top,baseline)-4)});
  return `已录入 ${nums.length} 个数据，可查看统计图并继续保存。`;
 }

 function drawGeometry(kind,values){
  const p=(x,y)=>pointToScreen(x,y);ctx.strokeStyle='#1879a4';ctx.fillStyle='rgba(24,121,164,.12)';ctx.lineWidth=2.4;ctx.beginPath();
  let result='';
  if(kind==='circle'){const r=Math.max(0,finite(values.radius)),o=p(0,0);ctx.arc(o.x,o.y,r*viewport.scale,0,Math.PI*2);result=`圆面积 ${(Math.PI*r*r).toFixed(2)}`}
  else{const w=Math.max(0,finite(values.length||values.base||values.a)),h=Math.max(0,finite(values.width||values.height||values.b)),a=p(-w/2,0),b=p(w/2,0),c=p(-w/2,h),d=p(w/2,h);ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);if(kind==='rectangle'){ctx.lineTo(d.x,d.y);ctx.lineTo(c.x,c.y);ctx.closePath()}else{ctx.lineTo(c.x,c.y);ctx.closePath()}result=kind==='rectangle'?`长方形面积 ${w*h}`:kind==='triangle'?`三角形面积 ${w*h/2}`:`直角边 ${w}、${h} 的直角三角形`}
  ctx.fill();ctx.stroke();return result;
 }

 function draw(){
  if(destroyed||!cssWidth)return;
  ctx.save();ctx.clearRect(0,0,cssWidth,cssHeight);ctx.beginPath();ctx.rect(0,0,cssWidth,cssHeight);ctx.clip();if(showGrid)drawGrid();
  const {domain,kind,values}=readModel();
  const result=domain==='function'?drawFunction(kind,values):domain==='statistics'?drawStatistics(values):drawGeometry(kind,values);
  ctx.restore();if(result)onResult(result);
 }

 function zoomAt(factor,x=cssWidth/2,y=cssHeight/2){
  const before=screenToPoint(x,y),next=clamp(viewport.scale*factor,8,640);viewport.scale=next;
  viewport.offsetX=x-cssWidth/2-before.x*next;viewport.offsetY=y-cssHeight/2+before.y*next;draw();
 }
 function reset(){viewport.scale=46;viewport.offsetX=0;viewport.offsetY=0;draw()}
 const wheel=event=>{event.preventDefault();const rect=canvas.getBoundingClientRect();zoomAt(Math.exp(-event.deltaY*.0015),event.clientX-rect.left,event.clientY-rect.top)};
 const pointerDown=event=>{canvas.setPointerCapture(event.pointerId);drag={x:event.clientX,y:event.clientY,offsetX:viewport.offsetX,offsetY:viewport.offsetY};canvas.classList.add('dragging')};
 const pointerMove=event=>{if(!drag)return;viewport.offsetX=drag.offsetX+event.clientX-drag.x;viewport.offsetY=drag.offsetY+event.clientY-drag.y;draw()};
 const pointerUp=event=>{drag=null;canvas.classList.remove('dragging');if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId)};
 canvas.addEventListener('wheel',wheel,{passive:false});canvas.addEventListener('pointerdown',pointerDown);canvas.addEventListener('pointermove',pointerMove);canvas.addEventListener('pointerup',pointerUp);canvas.addEventListener('pointercancel',pointerUp);
 wrap.querySelector('[data-math-zoom="in"]')?.addEventListener('click',()=>zoomAt(1.25));wrap.querySelector('[data-math-zoom="out"]')?.addEventListener('click',()=>zoomAt(.8));wrap.querySelector('[data-math-zoom="reset"]')?.addEventListener('click',reset);wrap.querySelector('[data-math-grid]')?.addEventListener('click',event=>{showGrid=!showGrid;event.currentTarget.textContent=showGrid?'隐藏网格':'显示网格';draw()});
 const observer=new ResizeObserver(resize);observer.observe(canvas);requestAnimationFrame(resize);
 const controller={draw,reset,destroy(){destroyed=true;observer.disconnect();canvas.removeEventListener('wheel',wheel);canvas.removeEventListener('pointerdown',pointerDown);canvas.removeEventListener('pointermove',pointerMove);canvas.removeEventListener('pointerup',pointerUp);canvas.removeEventListener('pointercancel',pointerUp)}};
 window.__mapleMathCanvas=controller;return controller;
}
window.MPTMathCanvas={mount};
})();
