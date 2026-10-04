const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const {EventEmitter}=require('node:events');
const source=fs.readFileSync(path.join(__dirname,'../apps/four-screen/tools/stream.cjs'),'utf8');
async function exercise({screenshotHangs=false}){
 const killed=[],messages=[];let tick,clock=0,closed=false;
 const page={on(){},goto:async()=>{},waitForFunction:async()=>{},screenshot:()=>screenshotHangs?new Promise(()=>{}):Promise.reject(new Error('screenshot timed out'))};
 const browser={newContext:async()=>({newPage:async()=>page}),close:()=>{closed=true;return new Promise(()=>{});}};
 return new Promise((resolve,reject)=>{
  const limit=setTimeout(()=>reject(new Error('Frozen render process did not exit')),1000);
  const sandbox={__dirname:path.join(__dirname,'../apps/four-screen/tools'),Buffer,Date:{now:()=>clock},
   process:{env:{PLAYWRIGHT_MODULE:'mock-browser',PULSE_MEDIA_ROOT:'/synthetic-output'},on(){},exit(code){clearTimeout(limit);resolve({code,killed,messages,closed});}},
   console:{log(){},error(e){messages.push(String(e));}},
   setInterval(callback){tick=callback;return {unref(){}};},
   setTimeout(callback,delay){return setTimeout(callback,delay===3000?20:delay);},clearTimeout,
   require(name){
    if(name==='mock-browser')return {chromium:{launch:async()=>browser}};
    if(name==='node:fs/promises')return {mkdir:async()=>{},writeFile:async()=>{},rename:async()=>{}};
    if(name==='node:child_process')return {spawn(){const child=new EventEmitter();child.stdin=new EventEmitter();child.stdin.destroy=()=>{};child.stdin.write=()=>true;child.kill=signal=>killed.push(signal);return child;}};
    return require(name);
   }};
  vm.runInNewContext(source,sandbox,{filename:'stream.cjs'});
  if(screenshotHangs)setTimeout(()=>{clock=61000;tick();},10);
 });
}
test('screenshot failure exits even when browser shutdown hangs',async()=>{
 const r=await exercise({});assert.equal(r.code,1);assert.equal(r.closed,true);
 assert.equal(r.killed.filter(x=>x==='SIGTERM').length,4);
 assert.equal(r.killed.filter(x=>x==='SIGKILL').length,4);
 assert.ok(r.messages.some(x=>x.includes('screenshot timed out')));
});
test('no render progress triggers bounded failure and supervisor restart',async()=>{
 const r=await exercise({screenshotHangs:true});assert.equal(r.code,1);
 assert.ok(r.messages.some(x=>x.includes('RENDER_PROGRESS_TIMEOUT')));
});
