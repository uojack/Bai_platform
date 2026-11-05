const {test}=require('node:test');
const assert=require('node:assert/strict');
const display=require('../apps/four-screen/web/lagooon.js');
const now=Date.parse('2026-11-05T12:00:00Z');
function vision(){return {event_type:'vision.result',frame:{id:20,timestamp:now/1000-1},metrics:{fps:30,infer_ms:33},algorithms:{detection:{enabled:true,ready:true}},multi_camera_monitor:{enabled:true,cameras:[{camera_code:'D1',location_name:'百灵岛',person_count:0,person_count_source_camera_code:'D1',dbfs:-46,video_state:'live',audio_state:'live',detection_stale:false,audio_stale:false,detection_age_seconds:.2,audio_age_seconds:.1,audio_source_camera_number:1,audio_fallback_active:false,yolo_fps:1.2}]}};}
function acoustics(){return {schema:'aos.demo.acoustics.v1',dataCollectedAt:new Date(now-1000).toISOString(),transport:{status:'OK'},sources:[{id:'microphone-1',name:'研发 D14',spaceId:'future_scene',connection:'CONNECTED',status:'READY',observationId:'test',resultState:'FRESH',durationSeconds:30,window:{end:new Date(now-10000).toISOString()},parameters:[{id:'T01',present:true,available:true,value:'0 次/分钟',windows:[{end:new Date(now-10000).toISOString()}]}]}]};}
test('D1 zero occupancy and own microphone are retained independently of server throughput',()=>{
 const s=display.snapshot(vision(),acoustics(),'D1',now);
 assert.equal(s.local.people,0);assert.equal(s.local.dbfs,-46);assert.equal(s.server.fps,30);assert.equal(s.local.yoloFps,1.2);assert.equal(s.sources[0].valid,1);assert.equal(s.sources[0].current,true);
});
test('borrowed or unverified audio cannot drive the D1 display',()=>{
 for(const change of [{audio_source_camera_number:15,audio_fallback_active:true},{audio_fallback_active:undefined}]){
  const v=vision();Object.assign(v.multi_camera_monitor.cameras[0],change);
  const s=display.snapshot(v,null,'D1',now);assert.equal(s.local.dbfs,null);assert.equal(s.local.energy,null);assert.equal(s.local.people,0);
 }
});
test('missing D1 does not automatically substitute another camera',()=>{
 const v=vision();v.multi_camera_monitor.cameras[0].camera_code='D4';
 assert.equal(display.snapshot(v,null,'D1',now).local.people,null);
});
test('network receipt does not renew stale field or acoustic measurements',()=>{
 const v=vision(),a=acoustics();v.frame.timestamp-=30;a.sources[0].window.end=new Date(now-181000).toISOString();
 const s=display.snapshot(v,a,'D1',now);assert.equal(s.local.people,null);assert.equal(s.local.dbfs,null);assert.equal(s.sources[0].current,false);assert.equal(s.sources[0].valid,0);
});
test('connected array without an observation is waiting, not an active result source',()=>{
 const a=acoustics();a.sources[0].observationId=null;
 const s=display.snapshot(null,a,'D1',now);assert.equal(s.sources[0].connected,true);assert.equal(s.sources[0].current,false);assert.equal(s.sources[0].valid,0);assert.equal(s.sources[0].state,'等待首个结果');
});
test('upstream stale state, carried values, and connection errors reject current results',()=>{
 for(const kind of ['stale','carried','offline','wrong-space']){
  const a=acoustics();if(kind==='stale')a.sources[0].resultState='STALE';if(kind==='carried')a.sources[0].parameters[0].carriedForward=true;if(kind==='offline')a.transport.status='ERROR';if(kind==='wrong-space')a.sources[0].spaceId='lagooon';
  assert.equal(display.snapshot(null,a,'D1',now).sources[0].valid,0);
 }
});
test('delayed source preserves its age while old semantic inference is rejected',()=>{
 const v=vision(),a=acoustics();a.sources[0].resultState='DELAYED';a.sources[0].window.end=new Date(now-45000).toISOString();
 v.multimodal_perception={results:[{camera_code:'D1',stale:false,timestamp:now/1000,domains:{spatial_attribute:{label:'旧解释',updated_at:now/1000-121}}}]};
 const s=display.snapshot(v,a,'D1',now);assert.equal(s.sources[0].state,'处理延后');assert.equal(s.sources[0].windowAge,45);assert.equal(s.semanticLive,false);
});
test('rendered values are escaped and local and research sources remain explicit',()=>{
 const a=acoustics();a.sources[0].name='<img src=x onerror=alert(1)>';
 const ui={trace:()=>'',gpu:()=>'',computeBreakdown:()=>'',metric:()=>''};
 const html=display.render({vision:vision(),acoustics:a,page:2},ui);
 assert.equal(html.includes('<img'),false);assert.ok(html.includes('&lt;img'));assert.ok(html.includes('非百灵岛现场测量'));assert.ok(html.includes('D1 · 摄像头音频'));assert.ok(html.includes('H03'));
});
