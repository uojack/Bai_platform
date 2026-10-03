// Display-only projection. No hardware commands, raw media, or invented scores.
export const ENDPOINT=process.env.BAIPLAYER_ACOUSTICS_URL||'http://127.0.0.1:8765/api/v1/acoustics/all';
export const SCHEMA='aos.demo.acoustics.v1';
export const IDS=['P','S','T','M','H','A'].flatMap(g=>[1,2,3,4].map(i=>`${g}0${i}`));
export const NAMES=['响应张量','衰减剖面','直达—混响结构','系统传递','声源场','扩散度','包围感','空间可分离度','事件密度','节律结构','持续性','状态转移不确定性','事件身份','语义显著性','关系绑定','异常与歧义','聆听负担','注意对齐','愉悦—活跃度','隐私与控制','响应可供性','响应置信度','代价与可逆性','结果变化'];
export const LIMITS={P01:'运行中盲估计响应切片；非完整空间响应张量',P02:'基于估计响应的衰减；非现场标准测量',P03:'估计响应的能量结构；保留拟合质量',P04:'频谱着色代理；非校准后的系统传递矩阵',S01:'阵列相对水平方位；无距离、仰角或三维位置',S02:'CDR 扩散能量比例；依赖信号与模型条件',S03:'客观包围感代理；未经现场主观标定',S04:'方向可分离性代理；不证明物理声源身份',T01:'检出事件实例密度；不是人数',T02:'滚动窗口节律结构；无节律与缺失不同',T03:'滚动窗口活动持续性；查看覆盖率',T04:'经验状态转移不确定性；不是因果传递熵或在线预测',M01:'声学事件类别；不是物理声源身份',M02:'当前为音画语义一致性代理；非任务重要性',M03:'上游尚未实现人物—声音—物体关系绑定',M04:'异常与歧义代理；需要人工复核',H01:'客观聆听负担代理；不是听者主观评分',H02:'上游尚未实现注意对齐',H03:'音频模型预测；不是受访者声景评价',H04:'隐私与空间可控性代理；不代表用户授权',A01:'规划变量；本接口不提供执行授权',A02:'规划变量；本接口不提供策略置信度',A03:'规划变量；本接口不提供动作代价',A04:'规划变量；尚无执行前后因果评价'};
const num=v=>typeof v==='number'&&Number.isFinite(v);
const list=v=>Array.isArray(v)?v:[];
const str=v=>typeof v==='string'?v.slice(0,400):'';
const fmt=v=>num(v)?String(Number(v.toFixed(3))):null;
const date=v=>typeof v==='string'&&/^\d{4}-\d\d-\d\dT/.test(v)&&Number.isFinite(Date.parse(v))?v:null;
function windowOf(w){if(!w||typeof w!=='object')return null;const start=date(w.captured_start_utc)||date(w.start),end=date(w.captured_end_utc)||date(w.end);return start&&end&&Date.parse(end)>=Date.parse(start)?{start,end}:null}
function upstreamWindows(d,o,id){
 const own=windowOf(d.window);if(own)return [own];
 const wins=[];
 for(const w of [d.upstream?.window,...Object.values(d.upstream||{}).map(v=>v?.window)]){const t=windowOf(w);if(t)wins.push(t)}
 if(wins.length)return wins;
 const models=[d.model_id,...(id==='H03'?['sens']:[]),...(id==='S02'?['cdr']:[])].filter(Boolean);
 for(const m of models){const t=windowOf(o.acoustic?.[m]?.window);if(t)wins.push(t)}
 // Spatial snapshots are aggregated explicitly over observation.time.
 if(!wins.length&&['S01','S03','S04'].includes(id)&&o.audio?.channels>1){const t=windowOf(o.time);if(t)wins.push(t)}
 return wins;
}
function summary(id,d){
 const f=(v,u='')=>num(v)?`${fmt(v)}${u}`:null;
 if(id==='P01')return d.slice_id?`响应切片 · ${f(d.response?.effective_duration_s,' s')||'时长未知'}`:null;
 if(id==='P02')return [f(d.wideband?.edt_s,' s EDT'),f(d.wideband?.t20_s,' s T20'),f(d.wideband?.t30_s,' s T30')].filter(Boolean).join(' · ')||null;
 if(id==='P03')return [f(d.wideband?.drr_db,' dB DRR'),f(d.wideband?.c50_db,' dB C50')].filter(Boolean).join(' · ')||null;
 if(id==='S01')return list(d.sources).map(s=>f(s.azimuth_degree??s.azimuth_deg,'°')).filter(Boolean).slice(0,3).join(' / ')||null;
 if(id==='S04')return f(d.mean??d.latest_value,' · 分离性代理');
 if(id==='M01')return list(d.classes).slice(0,3).map(c=>str(c.label||c.name||c.canonical_label||c.class_id)).filter(Boolean).join(' / ')||null;
 if(id==='T02'&&d.status==='NO_RHYTHMIC_STRUCTURE')return '未检出稳定节律';
 if(id==='T04')return f(d.global_analysis?.conditional_entropy_bits??d.value_bits,' bit');
 if(id==='H03')return [f(d.value?.pleasantness,' 愉悦'),f(d.value?.eventfulness,' 活跃')].filter(Boolean).join(' · ')||null;
 if(id==='M04')return [f(d.value?.unexpectedness,' 异常'),f(d.value?.ambiguity,' 歧义')].filter(Boolean).join(' · ')||null;
 if(num(d.value)){const unit=d.value_unit||d.unit;const label={score_0_100:'/100',proxy_score_0_100:'/100 代理',engineering_difficulty_0_100:'/100 负担代理',ratio_0_1:' 比例',events_per_minute:' 次/分钟'}[unit]||str(unit);return `${fmt(d.value)}${label?' '+label:''}`;}
 return null;
}
function projectParameter(id,d,o,s,generated){
 const present=!!d;d=d&&typeof d==='object'?d:{};
 const rawStatus=str(d.status)||'NOT_PROVIDED';
 const blocked=/UNAVAILABLE|INSUFFICIENT|CANCEL|FAIL|ERROR|NOT_CONFIGURED|ABSTAIN|LOW_SIGNAL|NOT_PROVIDED|WAITING/.test(rawStatus)||d.quality_gate_passed===false;
 const value=summary(id,d),windows=upstreamWindows(d,o,id),ends=windows.map(w=>Date.parse(w.end));
 const oldest=ends.length?Math.min(...ends):null;
 const age=oldest===null?null:(Date.parse(generated)-oldest)/1000;
 const model=o.acoustic?.[d.model_id];
 const carried=model?.freshness?.carried_forward===true;
 return {id,name:NAMES[IDS.indexOf(id)],sourceId:s.source_id,present,rawStatus,available:!blocked&&value!==null,
  value:blocked?null:value,model:str(d.model_id)||null,method:str(d.method)||null,
  unit:str(d.value_unit||d.unit)||null,windows,ageSeconds:num(age)&&age>=0?age:null,carriedForward:carried,
  confidence:num(d.confidence_0_1)?d.confidence_0_1:num(d.quality?.confidence)?d.quality.confidence:null,
  reason:str(d.reason)||list(d.quality?.reasons).map(str).slice(0,4).join(' · ')||null,
  limitation:LIMITS[id],path:id==='S04'?'observation.spatial.spatial_separability':`observation.derived.${id}`};
}
export function projectSnapshot(raw,now=Date.now()){
 if(raw?.schema_version!=='aos.acoustics.multi_source.v1'||!Array.isArray(raw.sources)||!Array.isArray(raw.spaces)||!date(raw.generated_at))throw Error('INVALID_UPSTREAM_SCHEMA');
 const unique=new Set();
 const sources=raw.sources.map(s=>{
  if(!s.source_id||unique.has(s.source_id))throw Error('INVALID_SOURCE_ID');unique.add(s.source_id);
  const o=s.observation||{},parameters=IDS.map(id=>{
   const rawValue=id==='S04'?(o.derived?.S04??o.spatial?.spatial_separability):o.derived?.[id];
   const variants=(Array.isArray(rawValue)&&rawValue.length?rawValue:[Array.isArray(rawValue)?undefined:rawValue]).map(d=>projectParameter(id,d,o,s,raw.generated_at));
   // Do not average model outputs. Prefer an available variant, retaining all variant summaries.
   const p=variants.find(v=>v.available)||variants[0];return {...p,variants:variants.length>1?variants:undefined};
  });
  return {id:str(s.source_id),name:str(s.source_name)||str(s.source_id),kind:str(s.source_kind),role:str(s.source_role),spaceId:str(s.location_id),spatialRef:str(s.spatial_context_ref),connection:str(s.connection_state)||'UNKNOWN',status:str(s.status)||'WAITING',resultState:str(s.result_state)||'NO_RESULT',ageSeconds:num(s.data_age_seconds)?s.data_age_seconds:null,latencySeconds:num(s.processing_latency_seconds)?s.processing_latency_seconds:null,queueSeconds:num(s.queue_wait_seconds)?s.queue_wait_seconds:null,droppedBatches:num(s.dropped_batches)?s.dropped_batches:null,observationId:str(o.observation_id)||null,sessionId:str(o.session_id)||null,observationStatus:str(o.status),window:windowOf(o.time),durationSeconds:num(o.time?.duration_s)&&o.time.duration_s>0?o.time.duration_s:30,channels:num(o.audio?.channels)?o.audio.channels:null,parameters};
 });
 const spaces=raw.spaces.map(s=>({id:str(s.space_id),name:str(s.space_name),members:list(s.member_source_ids).map(str),reference:str(s.spatial_reference_source_id),observationId:str(s.spatial_observation_id),coordinateFrame:str(s.coordinate_frame),window:windowOf(s.spatial_time),associations:list(s.associations).map(a=>({sourceId:str(a.source_id),status:str(a.status),overlap:num(a.overlap_ratio)?a.overlap_ratio:null,minimum:num(a.minimum_overlap_ratio)?a.minimum_overlap_ratio:0.8}))}));
 return {schema:SCHEMA,collectedAt:new Date(now).toISOString(),dataCollectedAt:new Date(now).toISOString(),upstreamGeneratedAt:raw.generated_at,transport:{status:'OK',lastSuccessAt:new Date(now).toISOString()},sourceCount:sources.length,sources,spaces,endpoint:ENDPOINT,displayOnly:true};
}
export function failSnapshot(previous,error,now=Date.now()){
 return {...(previous||{schema:SCHEMA,sources:[],spaces:[],sourceCount:0,endpoint:ENDPOINT,displayOnly:true}),collectedAt:new Date(now).toISOString(),transport:{status:'ERROR',error:str(error)||'UPSTREAM_UNAVAILABLE',lastSuccessAt:previous?.transport?.lastSuccessAt||null}};
}
export function transportAlive(snapshot,now=Date.now()){
 const age=(now-Date.parse(snapshot?.collectedAt))/1000;
 return snapshot?.schema===SCHEMA&&snapshot.transport?.status==='OK'&&num(age)&&age>=-5&&age<=12;
}
export function freshness(source,snapshot,now=Date.now(),parameter=null){
 if(!transportAlive(snapshot,now))return 'OFFLINE';
 if(source.connection!=='CONNECTED')return 'DISCONNECTED';
 if(source.status!=='READY'||!source.observationId)return 'NO_RESULT';
 if(parameter&&!parameter.available)return 'UNAVAILABLE';
 const initial=parameter?parameter.ageSeconds:source.ageSeconds;
 if(!num(initial))return 'UNKNOWN';
 const age=initial+Math.max(0,(now-Date.parse(snapshot.dataCollectedAt||snapshot.collectedAt))/1000),period=source.durationSeconds;
 if(source.resultState==='STALE'||age>2*period)return 'STALE';
 if(source.resultState==='NO_RESULT')return 'NO_RESULT';
 if(source.resultState==='DELAYED'||age>period)return 'DELAYED';
 return source.resultState==='FRESH'?'FRESH':'UNKNOWN';
}
export function association(source,space,snapshot,now=Date.now()){
 const ref=snapshot?.sources?.find(s=>s.id===space?.reference),a=space?.associations?.find(a=>a.sourceId===source.id);
 const metadata=source.spaceId===space?.id&&source.spatialRef===space?.reference&&space?.members?.includes(source.id)&&ref?.spaceId===space.id&&space?.members?.includes(ref?.id);
 const sw=source.window,rw=space?.window;
 const span=sw?Date.parse(sw.end)-Date.parse(sw.start):0;
 const overlap=sw&&rw&&span>0?Math.max(0,Math.min(Date.parse(sw.end),Date.parse(rw.end))-Math.max(Date.parse(sw.start),Date.parse(rw.start)))/span:null;
 const aligned=metadata&&ref?.observationId===space?.observationId&&a?.status==='ALIGNED'&&num(a.overlap)&&a.overlap>=a.minimum&&num(overlap)&&overlap>=a.minimum;
 const fresh=['FRESH','DELAYED'];
 return {status:!metadata?'UNBOUND':!aligned?'MISALIGNED':fresh.includes(freshness(source,snapshot,now))&&fresh.includes(freshness(ref,snapshot,now))?'ALIGNED':'STALE',overlap,contextOnly:true};
}
