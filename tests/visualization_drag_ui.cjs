const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require('playwright'),root=path.resolve(__dirname,'..');

(async()=>{
 const server=http.createServer((req,res)=>{const file=path.join(root,decodeURIComponent(new URL(req.url,'http://localhost').pathname));try{res.setHeader('Content-Type',file.endsWith('.html')?'text/html':'text/plain');res.end(fs.readFileSync(file))}catch(_){res.writeHead(404);res.end()}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
 try{
  browser=await chromium.launch({headless:true,...(fs.existsSync(chromium.executablePath())?{}:{channel:'msedge'})});
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/vendor/math-viz-kit/%E5%88%9D%E4%B8%AD/%E5%90%88%E5%B9%B6%E5%90%8C%E7%B1%BB%E9%A1%B9%E5%BD%92%E7%B1%BB%E5%99%A8.html`);
  let term=page.locator('.terms-pool .term-card').first(),data=JSON.parse(decodeURIComponent(await term.getAttribute('data-term'))),bucket=page.locator(`[data-bucket-type="${data.type}"]`);
  const transfer=await page.evaluateHandle(()=>new DataTransfer());await term.dispatchEvent('dragstart',{dataTransfer:transfer});await bucket.dispatchEvent('dragover',{dataTransfer:transfer});await bucket.dispatchEvent('drop',{dataTransfer:transfer});await bucket.locator('.bucket-terms').getByText(data.text,{exact:true}).waitFor();
  await page.getByText('重新归类',{exact:true}).click();term=page.locator('.terms-pool .term-card').first();data=JSON.parse(decodeURIComponent(await term.getAttribute('data-term')));await term.evaluate((card,type)=>{const target=document.querySelector(`[data-bucket-type="${CSS.escape(type)}"]`),start=card.getBoundingClientRect(),end=target.getBoundingClientRect(),event=(name,x,y)=>new PointerEvent(name,{bubbles:true,cancelable:true,pointerId:7,pointerType:'touch',clientX:x,clientY:y});card.setPointerCapture=()=>{};card.dispatchEvent(event('pointerdown',start.x+start.width/2,start.y+start.height/2));document.body.dispatchEvent(event('pointermove',end.x+end.width/2,end.y+end.height/2));document.body.dispatchEvent(event('pointerup',end.x+end.width/2,end.y+end.height/2))},data.type);await page.locator(`[data-bucket-type="${data.type}"] .bucket-terms`).getByText(data.text,{exact:true}).waitFor();
  assert.deepEqual(errors,[]);console.log('PASS: combining-like-terms supports mouse drag and touch drag');
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve))}
})().catch(error=>{console.error(error);process.exitCode=1});
