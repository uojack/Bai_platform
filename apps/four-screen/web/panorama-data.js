/* Read-only presentation rules shared by the three panorama screens. */
(function(root){
'use strict';
const finite=v=>typeof v==='number'&&Number.isFinite(v),clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const eventNames={'background noise':'背景噪声','speech':'语音','mechanisms':'机械声','whistle':'哨声','music':'音乐','silence':'静音','footsteps':'脚步声','clapping':'拍手声'};
function eventText(v){return String(v||'').split(/\s*\/\s*/).map(t=>eventNames[t.toLowerCase()]||t).join(' / ')}
function local(snapshot,camera='D4',now=Date.now()){
 const frame=snapshot?.frame,age=finite(frame?.timestamp)?now/1000-frame.timestamp:Infinity;
 const packetFresh=snapshot?.event_type==='vision.result'&&age>=-5&&age<=5;
 const c=snapshot?.multi_camera_monitor?.cameras?.find(c=>c.camera_code===camera);
 const available=packetFresh&&snapshot?.multi_camera_monitor?.enabled===true&&!!c;
 const detectionAge=finite(c?.detection_age_seconds)?Math.max(0,age)+c.detection_age_seconds:null;
 const audioAge=finite(c?.audio_age_seconds)?Math.max(0,age)+c.audio_age_seconds:null;
 const detectionFresh=available&&c.video_state==='live'&&c.detection_stale===false&&detectionAge!==null&&detectionAge>=0&&detectionAge<=5;
 const audioFresh=available&&c.audio_state==='live'&&c.audio_stale===false&&audioAge!==null&&audioAge>=0&&audioAge<=5;
 const peopleLocal=c?.person_count_source_camera_code===camera;
 const audioCode=Number.isInteger(c?.audio_source_camera_number)?'D'+c.audio_source_camera_number:null;
 const audioLocal=audioCode===camera&&c?.audio_fallback_active!==true;
 const people=detectionFresh&&peopleLocal&&finite(c.person_count)?c.person_count:null;
 const dbfs=audioFresh&&finite(c.dbfs)?c.dbfs:null;
 return {camera,frame:frame?.id??null,frameAge:finite(age)?Math.max(0,age):null,packetFresh,people,dbfs,detectionAge,audioAge,peopleLocal,audioLocal,audioCode,audioLocation:c?.audio_source_location_name||'来源待核对',yoloFps:detectionFresh&&finite(c?.yolo_fps)?c.yolo_fps:null,energy:dbfs===null?null:clamp((dbfs+80)/80,0,1)};
}
function acoustic(snapshot,sourceId,id,now=Date.now()){
 const source=snapshot?.sources?.find(s=>s.id===sourceId),p=source?.parameters?.find(p=>p.id===id);
 const fail=(reason,age=null)=>({valid:false,text:reason,age,source:source?.name||sourceId,sourceId,parameter:id});
 if(source?.spaceId!=='future_scene')return fail('来源空间未核对');
 if(!p?.present)return fail('尚无算法结果');
 const collected=Date.parse(snapshot?.dataCollectedAt||''),transportAge=(now-collected)/1000;
 if(snapshot?.schema!=='aos.demo.acoustics.v1'||snapshot.transport?.status!=='OK'||!finite(transportAge)||transportAge< -5||transportAge>12||source.connection!=='CONNECTED')return fail('来源离线 / 过期');
 const ends=(p.windows||[]).map(w=>Date.parse(w.end));
 const age=ends.length?(now-Math.min(...ends))/1000:null;
 if(age===null||!finite(age)||age< -5||age>180)return fail('采样已过期',finite(age)?age:null);
 if(p.carriedForward)return fail('历史沿用 · 不作当前值',age);
 if(!p.available||typeof p.value!=='string'||!p.value.trim())return fail('证据不足 / 等待结果',age);
 return {valid:true,text:(id==='M01'?eventText(p.value):p.value).slice(0,180),age:Math.max(0,age),source:source.name,sourceId,parameter:id,status:p.rawStatus,limitation:p.limitation};
}
function vision(snapshot,now=Date.now()){
 const fresh=local(snapshot,'D4',now).packetFresh,m=snapshot?.metrics||{},rows=snapshot?.multi_camera_monitor?.cameras||[];
 const value=k=>fresh&&finite(m[k])?m[k]:null;
 const algorithms=Object.values(snapshot?.algorithms||{});
 return {fresh,fps:value('fps'),inferMs:value('infer_ms'),lagMs:value('camera_lag_ms'),liveCameras:fresh?rows.filter(c=>local({...snapshot,multi_camera_monitor:{...snapshot.multi_camera_monitor,cameras:[c]}},c.camera_code,now).yoloFps!==null).length:null,cameras:rows.length,ready:fresh?algorithms.filter(a=>a.enabled===true&&a.ready===true).length:null,enabled:fresh?algorithms.filter(a=>a.enabled===true).length:null};
}
const api={local,acoustic,vision,eventText};
if(typeof module==='object'&&module.exports)module.exports=api;else root.PanoramaData=api;
})(typeof globalThis==='undefined'?this:globalThis);
