/* Pure display projection; never combines overlapping views into a room total. */
(function(root){
'use strict';
var finite=function(v){return typeof v==='number'&&isFinite(v);},fmt=function(v,d){return finite(v)?v.toFixed(d||0):'—';};
function visible(v){return String(v==null?'':v).replace(/\bD\d+\b/g,'').replace(/·\s*·/g,'·').replace(/^\s*·\s*/,'').trim();}
function view(packet,camera,region,elapsed){
 var live=!!packet&&packet.schema==='aos.spatial-screen.v1'&&packet.region===region&&packet.sensors?.camera===camera&&elapsed>=0&&elapsed<=7,s=live?packet:null,l=s?.sensors;
 function people(c){return c&&finite(c.people)&&finite(c.detectionAge)&&c.detectionAge+elapsed<=5?c.people:null;}
 var bound=typeof camera==='string';
 var p=bound?people(l):null,db=bound&&l&&finite(l.dbfs)&&finite(l.audioAge)&&l.audioAge+elapsed<=5?l.dbfs:null;
 var scope=s?.sourceScope||'room',label=l?.displayLocation||(region==='lab02'?'室内':'本室');
 var peers=s?.secondarySensors||[],door=peers[0],dp=people(door),peerText=peers.map(function(x){var n=people(x);return (x.displayLocation||'补充视角')+(n===null?' 待更新':' '+n+' 人估计');}),sem=s?.semantic,semLive=sem?.state==='CURRENT'&&finite(sem.age)&&sem.age+elapsed<=120;
 var source=s?.acoustics?.sources?.find(function(x){return x.current&&x.parameters.M01&&x.parameters.M01.age+elapsed<=180;}),event=source?.parameters.M01?.value;
 var core=s?.core,cv=core?.state==='OBSERVED',field=s?.field,fv=field?.state==='OBSERVED'&&field.age+elapsed<=12;
 var services=sem?.services||[],serviceLive=services.length>0&&services.every(function(x){return x.online&&x.age+elapsed<=45;});
 var serviceNote=serviceLive?'语义服务在线 · '+(services.every(function(x){return x.resultAccess==='AUTH_REQUIRED';})?'算法结果待授权':'结果未接入'):'语义服务等待连接';
 var coreText=cv?({HOLD:'保持 / HOLD',RUNNING:'运行中',STOPPED:'已停止',IDLE:'待机'}[core.status]||core.status||'状态未知'):'状态未知';
 var fieldText=fv?(field.audioState==='SAFE_MUTED'?'安全静音':field.phase==='STOPPED'?'渲染停止':field.nativeActive===true&&field.readbackVerified===true?'原生渲染运行':'等待渲染证据'):'声场状态未知';
 var cards=[
  {id:'perception',state:p!==null||db!==null?'running':'pending',badge:p!==null||db!==null?'实时':'等待',value:!bound?'本室点位待确认':p===null?label+'人数待更新':label+' '+p+' 人估计',detail:!bound?'本室感知暂未绑定':db===null?label+'音频待更新':label+' '+fmt(db,1)+' dBFS',note:!bound?'仅展示已接入的系统状态':scope==='nearby'?'邻近视野 · 非室内测量':'摄像头声能 · 未校准声压级'},
  {id:'cognition',state:semLive?'running':event?'local':'pending',badge:semLive?'视觉语义':event?'研发参考':'等待',value:semLive?(scope==='nearby'?'邻近语义：':'视觉语义：')+sem.label:!bound?'本室语义待接入':'空间语义待更新',detail:event?'研发声学：'+event:'研发声学结果待更新',note:event?source.name+' · '+(source.scope?.includes('本区研发视角')?'本区参考':'非本室实测'):serviceNote},
  {id:'orchestration',state:cv?'local':'pending',badge:cv?'已读取':'等待',value:coreText,detail:cv?'场景 '+fmt(core.sceneIndex)+' · '+(core.outputArmed===true?'输出已授权':core.outputArmed===false?'输出未授权':'授权未知'):'等待编排状态',note:'BaiCore / AOS演示 · 全局 · 只读'},
  {id:'connection',state:live?'running':'pending',badge:live?'只读连接':'等待',value:live?'数据持续接收':'数据等待连接',detail:serviceNote,note:serviceLive?services.map(function(x){return x.name+' '+x.version;}).join(' · '):'保留来源状态与更新时间'},
  {id:'rendering',state:fv&&field.nativeActive===true&&field.readbackVerified===true&&field.audioState!=='SAFE_MUTED'?'running':fv?'local':'pending',badge:fv?'已读取':'等待',value:fieldText,detail:fv?'效果 '+String(field.effect||'未知'):'等待声场状态',note:'BaiField网关 · 全局 · 非本室测量'},
  {id:'scene',state:'local',badge:bound?'分区观测':'房间配置',value:peerText[0]||(!bound?'等待本室点位':scope==='nearby'?'门外参考':'本区独立观测'),detail:peerText.length>1?peerText.slice(1).join(' · '):peerText.length?'各视野不相加':scope==='nearby'?'不推断房间使用情况':'保留原房间模式',note:peerText.length>1?'各视野独立，不相加':'房间模式不由人数推断'}
 ];
 var eventNames={'Mechanisms':'机械声','Background noise':'背景噪声','Speech':'语音','Music':'音乐'};
 cards.forEach(function(c){for(var k of ['badge','value','detail','note']){c[k]=visible(c[k]);Object.keys(eventNames).forEach(function(word){c[k]=c[k].split(word).join(eventNames[word]);});}});
 return {live:live,people:p,doorPeople:dp,dbfs:db,frame:l?.frame,cards:cards,serviceNote:serviceNote,stamp:s?.generatedAt,location:label,sourceScope:scope,bound:bound};
}
var api={view:view,visible:visible};if(typeof module==='object'&&module.exports)module.exports=api;else root.RoomLiveData=api;
})(typeof globalThis==='undefined'?this:globalThis);
