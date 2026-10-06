/* Read-only HTML rendering; device control remains with the existing supervisor. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const {spawn}=require('node:child_process');
const fs=require('node:fs/promises');
const path=require('node:path');
const {once}=require('node:events');
const root=path.resolve(__dirname,'..');
const output=process.env.PULSE_MEDIA_ROOT;
const evidence=process.env.PULSE_EVIDENCE_ROOT||path.join(root,'evidence');
if(!output)throw Error('PULSE_MEDIA_ROOT required');
let browser,children=[],stopped=false,lastProgress=Date.now();
// A stuck browser.close() must not leave a live process with frozen playlists.
function shutdown(code,error){
 if(stopped)return;
 stopped=true;
 if(error)console.error(error);
 const deadline=setTimeout(()=>{for(const p of children)p.kill('SIGKILL');process.exit(code);},3000);
 for(const p of children){p.stdin.destroy();p.kill('SIGTERM');}
 Promise.resolve().then(()=>browser?.close()).catch(()=>{}).finally(()=>{clearTimeout(deadline);process.exit(code);});
}
const watchdog=setInterval(()=>{if(!stopped&&Date.now()-lastProgress>60000)shutdown(1,new Error('RENDER_PROGRESS_TIMEOUT'));},1000);
watchdog.unref();
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>shutdown(0));
async function atomic(file,value){const temp=file+'.tmp';await fs.writeFile(temp,value);await fs.rename(temp,file);}
async function main(){
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_EXECUTABLE || '/opt/google/chrome/chrome',args:['--disable-dev-shm-usage','--disable-background-timer-throttling','--disable-renderer-backgrounding']});
 lastProgress=Date.now();
 const context=await browser.newContext({viewport:{width:1920,height:1080},timezoneId:'Asia/Shanghai'});
 await fs.mkdir(evidence,{recursive:true});
 const roles=['pulse','panorama1','panorama2','panorama3'];
 const streams=[];
 for(const role of roles){
  const out=path.join(output,role);await fs.mkdir(out,{recursive:true});
  const page=await context.newPage();page.on('pageerror',e=>shutdown(1,new Error(role+': '+e.message)));
  await page.goto('http://127.0.0.1:'+(process.env.PULSE_FEED_PORT||18784)+'/index.html?mode='+role+'&delivery=stream');
  lastProgress=Date.now();
  await page.waitForFunction(()=>!!window.Pulse?.status().frame,null,{timeout:15000}).catch(()=>{});
  // Four independent data waits can exceed one watchdog interval in total.
  // Completed startup stages are progress even when sensors are unavailable.
  lastProgress=Date.now();
  const args=['-hide_banner','-loglevel','warning','-y','-f','image2pipe','-framerate','4','-use_wallclock_as_timestamps','1','-probesize','32','-analyzeduration','0','-vcodec','mjpeg','-i','pipe:0','-an','-vf','fps=12','-c:v','libx264','-preset','ultrafast','-tune','zerolatency','-threads','2','-pix_fmt','yuv420p','-b:v','1800k','-maxrate','2400k','-bufsize','4800k','-g','24','-keyint_min','24','-sc_threshold','0','-f','hls','-hls_time','2','-hls_list_size','6','-hls_delete_threshold','6','-hls_start_number_source','epoch','-hls_flags','delete_segments+temp_file+omit_endlist+program_date_time+discont_start','-hls_segment_filename',path.join(out,'seg_%d.ts'),path.join(out,'live.m3u8')];
  const encoder=spawn(process.env.FFMPEG_EXECUTABLE || 'ffmpeg',args,{stdio:['pipe','ignore','inherit']});children.push(encoder);
  encoder.stdin.on('error',e=>shutdown(1,new Error(role+' encoder pipe: '+e.message)));
  encoder.on('exit',(code)=>{if(!stopped)shutdown(1,new Error(role+' encoder exited '+code));});
  streams.push({role,page,encoder,out});
 }
 let count=0;
 while(!stopped){
  const start=Date.now();
  await Promise.all(streams.map(async s=>{
   const jpg=await s.page.screenshot({type:'jpeg',quality:87,timeout:8000});
   if(!s.encoder.stdin.write(jpg))await once(s.encoder.stdin,'drain');
   if(count%40===0){
    await atomic(path.join(evidence,s.role+'.jpg'),jpg);
    const status=await s.page.evaluate(()=>({title:document.title,status:Pulse.status(),overflow:document.querySelector('#content').scrollHeight-document.querySelector('#content').clientHeight}));
    await atomic(path.join(evidence,s.role+'.json'),JSON.stringify({checkedAt:new Date().toISOString(),...status}));
   }
  }));
  lastProgress=Date.now();
  if(count%40===0)console.log(JSON.stringify({at:new Date().toISOString(),frame:count,renderMs:Date.now()-start}));
  count++;await new Promise(r=>setTimeout(r,Math.max(0,250-(Date.now()-start))));
 }
}
main().catch(e=>shutdown(1,e));
