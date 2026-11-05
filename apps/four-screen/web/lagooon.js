/* LAGOOON display: D1 field evidence, with separately attributed server results. */
(function(root){
'use strict';
const model=typeof module==='object'&&module.exports?require('./panorama-data.js'):root.PanoramaData;
const finite=v=>typeof v==='number'&&Number.isFinite(v);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(v,d=0,unit='')=>finite(v)?v.toFixed(d)+unit:'—';
const labels={P01:'响应张量',P02:'衰减剖面',P03:'直达混响',P04:'系统传递',S01:'声源场',S02:'扩散度',S03:'包围感',S04:'空间分离',T01:'事件密度',T02:'节律结构',T03:'持续性',T04:'转移不确定性',M01:'声音类别',M02:'语义显著代理',M03:'关系落地',M04:'异常与歧义代理',H01:'聆听努力代理',H02:'注意对齐代理',H03:'愉悦活跃代理',H04:'隐私自主',A01:'响应可供',A02:'响应置信',A03:'代价可逆',A04:'结果增量'};
const names={detection:'目标检测',face_detection:'人脸检测',depth:'相对深度',pedestrian_attributes:'行人属性',head_pose:'头部姿态',roi_alarm:'区域告警',speech_recognition:'语音识别',audio_perception:'声音理解'};
function snapshot(vision,acoustics,camera='D1',now=Date.now()){
 const local=model.local(vision,camera,now),server=model.vision(vision,now);
 const cameraRow=vision?.multi_camera_monitor?.cameras?.find(c=>c.camera_code===camera);
 local.audioLocal=local.audioLocal&&cameraRow?.audio_fallback_active===false;
 // A fallback audio stream must never drive the local room's meter or animation.
 local.dbfs=local.audioLocal?local.dbfs:null;
 local.energy=local.dbfs===null?null:local.energy;
 const age=(now-Date.parse(acoustics?.dataCollectedAt||''))/1000;
 const acousticLive=acoustics?.schema==='aos.demo.acoustics.v1'&&acoustics?.transport?.status==='OK'&&finite(age)&&age>=-5&&age<=12;
 const sources=(acoustics?.sources||[]).map(source=>{
  const windowAge=(now-Date.parse(source.window?.end||''))/1000;
  const period=finite(source.durationSeconds)&&source.durationSeconds>0?source.durationSeconds:30;
  const connected=acousticLive&&source.connection==='CONNECTED';
  const eligible=connected&&source.spaceId==='future_scene'&&source.status==='READY'&&!!source.observationId&&finite(windowAge)&&windowAge>=-5&&windowAge<=180&&['FRESH','DELAYED'].includes(source.resultState);
  const current=eligible&&windowAge<=2*period;
  const parameters=Object.keys(labels).map(id=>{
   const r=model.acoustic(acoustics,source.id,id,now);
   return {...r,...(r.valid&&!eligible?{valid:false,text:'来源结果未就绪 / 过期'}:{}),id,label:labels[id]};
  });
  const state=!connected?'离线 / 等待':!source.observationId?'等待首个结果':!eligible?'结果过期 / 未核对':!current?'窗口较旧':windowAge>period||source.resultState==='DELAYED'?'处理延后':'结果更新';
  return {source,parameters,connected,current,state,windowAge:finite(windowAge)?Math.max(0,windowAge):null,valid:parameters.filter(p=>p.valid).length};
 });
 const semantic=vision?.multimodal_perception?.results?.find(s=>s.camera_code===camera);
 const domain=semantic?.domains?.spatial_attribute;
 const semanticAge=(now/1000)-domain?.updated_at,recordAge=(now/1000)-semantic?.timestamp;
 const semanticLive=local.packetFresh&&semantic?.stale===false&&finite(semanticAge)&&semanticAge>=-5&&semanticAge<=120&&finite(recordAge)&&recordAge>=-5&&recordAge<=120&&typeof domain?.label==='string'&&domain.label.trim().length>0;
 return {local,server,acousticLive,sources,semantic,semanticLive,semanticAge:finite(semanticAge)?Math.max(0,semanticAge):null};
}
function stat(label,value,note){return '<div class="l-stat"><label>'+esc(label)+'</label><strong>'+esc(value)+'</strong><small>'+esc(note||'')+'</small></div>';}
function heading(kicker,title,lead){return '<div class="eyebrow">'+kicker+'</div><h1>'+title+'</h1><p class="lead">'+lead+'</p>';}
function rail(title,body){return '<aside class="rail l-rail"><div class="eyebrow">LIVE EVIDENCE</div><div class="chapter">'+title+'</div>'+body+'</aside>';}
function sourceName(row){return row?.source.name||'研发来源等待接入';}
function result(row,id){return row?.parameters.find(p=>p.id===id)||{valid:false,text:'等待来源',age:null};}
function resultRow(row,id){const r=result(row,id);return '<div class="l-result '+(r.valid?'fresh':'')+'"><label>'+esc(labels[id])+'</label><strong>'+esc(r.text)+'</strong><small>'+esc(r.age===null?'没有有效采样窗口':fmt(r.age,0)+' 秒前')+'</small></div>';}
function sound(local){return '<div class="l-sound"><div><label>'+esc(local.camera)+' · 摄像头音频</label><strong>'+fmt(local.dbfs,1)+'<small> dBFS</small></strong></div><div class="meter-track"><i style="width:'+(local.energy===null?0:100*local.energy).toFixed(1)+'%"></i></div><p>'+esc(local.audioLocal?'本区域输入 · 数字满刻度参考，非声压级':'本区域音频未就绪 · 不使用其他区域代替')+'</p></div>';}
function sourceCards(s){return '<div class="l-sources">'+s.sources.map(row=>'<article class="l-source '+(row.current?'fresh':'')+'"><label>'+esc(sourceName(row))+'</label><strong>'+esc(row.state)+'</strong><p>'+esc(result(row,'M01').text)+'</p><small>事件密度 '+esc(result(row,'T01').text)+'</small><small>'+esc(row.windowAge===null?'尚无结果窗口':fmt(row.windowAge,0)+' 秒前 · '+row.valid+' / 24 项有效')+'</small></article>').join('')+'</div>';}
function algorithmRows(vision,fresh){return '<div class="l-algorithms">'+Object.entries(names).map(([id,name])=>{
 const a=vision?.algorithms?.[id],top=vision?.[id];
 const state=!fresh?'等待新数据':!a?'未提供状态':a.enabled===false?'未开启':a.enabled!==true?'状态未知':top&&typeof top.ready==='boolean'&&top.ready!==a.ready?'状态不一致':a.ready===true?'就绪':'等待输入';
 return '<div><span>'+name+'</span><b class="'+(state==='就绪'?'live':'')+'">'+state+'</b></div>';
 }).join('')+'</div>';}
function matrix(row){return '<div class="l-matrix">'+['P','S','T','M','H','A'].map((g,i)=>'<div><label>'+['物理','空间','时间','意义','人因','响应'][i]+'</label>'+[1,2,3,4].map(n=>{
 const id=g+'0'+n,r=result(row,id),missing=r.text==='尚无算法结果';
 return '<div class="l-cell '+(r.valid?'fresh':'')+'"><b>'+id+' '+labels[id]+'</b><span>'+esc(r.valid?r.text:missing?'尚未提供':r.text)+'</span></div>';
 }).join('')+'</div>').join('')+'</div>';}
function render(ctx,ui){
 const {vision,acoustics,page,camera='D1'}=ctx,s=snapshot(vision,acoustics,camera),l=s.local,v=s.server;
 const primary=s.sources.find(row=>row.source.id==='microphone-1'),current=s.sources.filter(row=>row.current).length;
 let left='',right='';
 if(page===0){
  left=heading('LAGOOON · HERE & NOW','百灵岛，此刻','以本区域摄像头的视觉与音频，观察空间正在发生的变化。')+'<div class="l-vitals"><div class="l-occupancy"><label>'+esc(camera)+' · 视觉估计</label><strong>'+fmt(l.people)+'<small> 人</small></strong><span>'+esc(l.people===null?'等待独立人数结果':fmt(l.detectionAge,1)+' 秒前 · 非门禁记录')+'</span></div>'+sound(l)+'</div><div class="l-trend"><label>声音起伏 · 最近三分钟</label>'+ui.trace('sound',false)+'</div><div class="l-inline">'+stat('单路检测',fmt(l.yoloFps,2,' FPS'),camera+' 实际采样')+stat('音频年龄',l.dbfs===null?'—':fmt(l.audioAge,1,' 秒'),'来自摄像头麦克风')+stat('空间理解',s.semanticLive?'新结果可用':'等待新结果','独立判断模型结果时效')+'</div><p class="note">背景曲线随本区域有效声能变化。摄像头音频与电视自带麦克风是不同的输入来源。</p>';
  right=rail('两台算法服务器', '<div class="l-kpis">'+stat('视觉整体吞吐',fmt(v.fps,1,' FPS'),'整体服务 · 非 D1 帧率')+stat('单次推理',fmt(v.inferMs,1,' ms'),'视觉服务瞬时遥测')+stat('算法就绪',v.ready===null?'—':v.ready+' / '+v.enabled,'就绪 / 已启用')+stat('声学有效结果源',s.acousticLive?current+' / '+s.sources.length:'—','按结果窗口检查')+'</div>'+ui.gpu()+'<div class="l-research"><label>声学服务器 · 研发参考</label><strong>'+esc(result(primary,'M01').text)+'</strong><small>'+esc(sourceName(primary))+' · 非百灵岛实测</small></div>');
 }
 if(page===2){
  left=heading('AUDIO · OBSERVATION TO MEANING','从声音，到理解','百灵岛声能实时变化；研发声学结果保留原始空间与采样窗口。')+sound(l)+sourceCards(s)+'<div class="l-section"><b>S/CA–24 · 六类算法证据</b><span>'+esc(sourceName(primary))+' · 逐项状态</span></div>'+matrix(primary)+'<p class="note">人因项是模型代理，非现场听众评价。物理声学参数需要有效测量；历史沿用与过期结果不作当前值。</p>';
  right=rail('研发算法 · 当前结果','<p class="l-source-note">'+esc(sourceName(primary))+'<br>以下均非百灵岛现场测量</p><div class="l-result-grid">'+['M01','T01','T02','T03','T04','M02','M04','H03'].map(id=>resultRow(primary,id)).join('')+'</div><div class="l-inline two">'+stat('处理耗时',primary?.current?fmt(primary.source.latencySeconds,1,' 秒'):'—','声学结果产生耗时')+stat('排队等待',primary?.current?fmt(primary.source.queueSeconds,1,' 秒'):'—','输入到处理的等待')+'</div>');
 }
 if(page===3){
  left=heading('VISION · COMPUTE IN MOTION','让计算过程可见','整体推理、单路采样与算法就绪状态，各自保留真实含义。')+'<div class="l-compute-hero"><strong>'+fmt(v.fps,1)+'</strong><span>FPS<br>视觉服务整体吞吐</span></div><div class="l-inline">'+stat('单次推理',fmt(v.inferMs,1,' ms'),'服务器瞬时值')+stat('采集滞后',fmt(v.lagMs,0,' ms'),'视觉采集链路')+stat('本区域检测',fmt(l.yoloFps,2,' FPS'),camera+' 单路采样')+'</div><div class="l-trend"><label>推理吞吐 · 接收后记录</label>'+ui.trace('fps',false)+'</div>'+ui.gpu()+ui.computeBreakdown()+'<p class="note">GPU 条形表示显存占用，不能推算 GPU 利用率。未开启的算法不显示为运行中。</p>';
  right=rail('八项视觉与音频能力',algorithmRows(vision,v.fresh)+'<div class="l-inline two">'+stat('实时检测通道',v.liveCameras===null?'—':v.liveCameras+' / '+v.cameras,'有效采样的通道')+stat('算法就绪',v.ready===null?'—':v.ready+' / '+v.enabled,'就绪 / 已启用')+'</div><p class="note">服务器语音识别和声音理解的就绪状态，不代表结果来自 D1；没有来源证据时不关联到百灵岛。</p>');
 }
 if(page===4){
  const rows=vision?.multi_camera_monitor?.cameras||[];
  left=heading('SENSING · SOURCES & PLACES','每路数据，都有位置','百灵岛绑定 '+esc(camera)+'；各区域独立观察，不把通道人数直接相加。')+'<div class="l-camera-grid">'+rows.map(c=>{
   const a=model.local(vision,c.camera_code),ownAudio=a.audioLocal&&a.dbfs!==null;
   return '<article class="'+(c.camera_code===camera?'selected':'')+'"><b>'+esc(c.camera_code+' · '+c.location_name)+'</b><strong>'+esc(a.people===null?'人数未知':a.people+' 人估计')+'</strong><span>'+esc(ownAudio?fmt(a.dbfs,1,' dBFS'):a.audioCode&&a.audioCode!==c.camera_code?'音频参考 '+a.audioCode:'本路音频未知')+'</span><small>'+esc(a.yoloFps===null?'检测等待':fmt(a.yoloFps,2)+' FPS')+'</small></article>';
  }).join('')+'</div><p class="note">来源回退单独标记；某一路引用另一通道，不能当作两个独立传感器。电视内置传感器尚未接入。</p>';
  right=rail('百灵岛 · 输入链路',ui.metric('视觉输入',camera+' · 百灵岛',l.peopleLocal?'人数来源一致':'人数来源待核对')+ui.metric('声音输入',l.audioLocal?camera+' 摄像头音频':'本区域输入等待','数字声能 · 非电视麦克风')+ui.metric('声学服务器',s.acousticLive?'已连接':'等待 / 过期',current+' 路具备有效结果窗口')+ui.metric('电视内置麦克风','尚未接入','不以摄像头音频冒充')+ui.metric('温湿度 / 空气质量','尚未接入','没有对应传感器数据'));
 }
 if(page===5){
  left=heading('SPACE · EVIDENCE BEFORE INFERENCE','看见变化，保留边界','把可观测数据与模型解释放在一起，同时显示它们的时间。')+'<div class="l-semantic"><label>'+esc(camera)+' · 视觉模型的空间解释</label><strong>'+esc(s.semanticLive?s.semantic.domains.spatial_attribute.label:s.semantic?'理解结果已过期':'等待空间理解')+'</strong><small>'+esc(s.semanticAge===null?'尚无有效时间':fmt(s.semanticAge,0)+' 秒前')+' · 模型推断，不是现场事实</small></div><div class="l-trend"><label>人数变化 · 最近三分钟</label>'+ui.trace('people',false)+'</div><div class="l-trend"><label>声能变化 · 最近三分钟</label>'+ui.trace('sound',false)+'</div><div class="l-inline">'+stat('本区域人数',fmt(l.people,0,' 人'),'独立视觉估计')+stat('本区域声能',fmt(l.dbfs,1,' dBFS'),'未校准为声压级')+stat('语义状态',s.semanticLive?'当前可用':'等待更新','结果时效 ≤ 120 秒')+'</div>';
  right=rail('观测与解释分开','<div class="l-evidence"><b>01 · 现场观测</b><p>'+esc(camera)+' 摄像头提供人数估计和音频声能。采样超过 5 秒则不再显示为当前值。</p><b>02 · 模型解释</b><p>空间语义单独核对时间。人数与音量不能直接证明情绪、满意度或注意力。</p><b>03 · 声学研发</b><p>事件类别、节律、持续性及人因代理来自研发区域，暂不代表百灵岛。</p><b>04 · 等待接入</b><p>电视自身麦克风、温度、湿度和空气质量保持“尚未接入”。</p></div><div class="l-inline two">'+stat('视觉数据年龄',l.packetFresh?fmt(l.frameAge,1,' 秒'):'已过期','按源帧时间计算')+stat('声学结果源',s.acousticLive?current+' / '+s.sources.length:'—','按各自窗口计算')+'</div>');
 }
 return '<div class="l-main">'+left+'</div>'+right;
}
const api={snapshot,render};
if(typeof module==='object'&&module.exports)module.exports=api;else root.LagooonDisplay=api;
})(typeof globalThis==='undefined'?this:globalThis);
