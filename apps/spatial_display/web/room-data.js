/* Pure display projection; never combines overlapping views into a room total. */
(function(root){
'use strict';
var finite=function(v){return typeof v==='number'&&isFinite(v);},fmt=function(v,d){return finite(v)?v.toFixed(d||0):'—';};
function visible(v){return String(v==null?'':v).replace(/\bD\d+\b/g,'').replace(/·\s*·/g,'·').trim();}
function view(packet,camera,region,elapsed){
 var live=!!packet&&packet.schema==='aos.spatial-screen.v1'&&packet.region===region&&packet.sensors?.camera===camera&&elapsed>=0&&elapsed<=7,s=live?packet:null,l=s?.sensors;
 function people(c){return c&&finite(c.people)&&finite(c.detectionAge)&&c.detectionAge+elapsed<=5?c.people:null;}
 var p=people(l),db=l&&finite(l.dbfs)&&finite(l.audioAge)&&l.audioAge+elapsed<=5?l.dbfs:null;
 var peers=s?.secondarySensors||[],door=peers[0],dp=people(door),sem=s?.semantic,semLive=sem?.state==='CURRENT'&&finite(sem.age)&&sem.age+elapsed<=120;
 var source=s?.acoustics?.sources?.find(function(x){return x.current&&x.parameters.M01&&x.parameters.M01.age+elapsed<=180;}),event=source?.parameters.M01?.value;
 var core=s?.core,cv=core?.state==='OBSERVED',field=s?.field,fv=field?.state==='OBSERVED'&&field.age+elapsed<=12;
 var services=sem?.services||[],serviceLive=services.length>0&&services.every(function(x){return x.online&&x.age+elapsed<=45;});
 var serviceNote=serviceLive?'语义服务在线 · 算法结果待授权':'语义服务等待连接';
 var coreText=cv?({HOLD:'保持 / HOLD',RUNNING:'运行中',STOPPED:'已停止',IDLE:'待机'}[core.status]||core.status||'状态未知'):'状态未知';
 var fieldText=fv?(field.audioState==='SAFE_MUTED'?'安全静音':field.phase==='STOPPED'?'渲染停止':field.nativeActive===true&&field.readbackVerified===true?'原生渲染运行':'等待渲染证据'):'声场状态未知';
 var cards=[
  {id:'perception',state:p!==null||db!==null?'running':'pending',badge:p!==null||db!==null?'实时':'等待',value:p===null?'室内人数待更新':'室内 '+p+' 人估计',detail:db===null?'室内音频待更新':'室内 '+fmt(db,1)+' dBFS',note:'摄像头声能 · 未校准声压级'},
  {id:'cognition',state:semLive?'running':event?'local':'pending',badge:semLive?'视觉语义':event?'研发参考':'等待',value:semLive?sem.label:'空间语义待更新',detail:event?'研发声学：'+event:'研发声学结果待更新',note:event?source.name+' · 非本室实测':serviceNote},
  {id:'orchestration',state:cv?'local':'pending',badge:cv?'已读取':'等待',value:coreText,detail:cv?'场景 '+fmt(core.sceneIndex)+' · '+(core.outputArmed===true?'输出已授权':core.outputArmed===false?'输出未授权':'授权未知'):'等待编排状态',note:'BaiCore / AOS演示 · 全局 · 只读'},
  {id:'connection',state:live?'running':'pending',badge:live?'只读连接':'等待',value:live?'数据持续接收':'数据等待连接',detail:serviceNote,note:serviceLive?services.map(function(x){return x.name+' '+x.version;}).join(' · '):'保留来源状态与更新时间'},
  {id:'rendering',state:fv&&field.nativeActive===true&&field.readbackVerified===true&&field.audioState!=='SAFE_MUTED'?'running':fv?'local':'pending',badge:fv?'已读取':'等待',value:fieldText,detail:fv?'效果 '+String(field.effect||'未知'):'等待声场状态',note:'BaiField网关 · 全局 · 非本室测量'},
  {id:'scene',state:p!==null||dp!==null?'local':'pending',badge:'分区观测',value:dp===null?'门口人数待更新':'门口 '+dp+' 人估计',detail:'室内与门口视野不相加',note:'原研发模式为房间配置，非人数推断'}
 ];
 var eventNames={'Mechanisms':'机械声','Background noise':'背景噪声','Speech':'语音','Music':'音乐'};
 cards.forEach(function(c){for(var k of ['badge','value','detail','note']){c[k]=visible(c[k]);Object.keys(eventNames).forEach(function(word){c[k]=c[k].split(word).join(eventNames[word]);});}});
 return {live:live,people:p,doorPeople:dp,dbfs:db,frame:l?.frame,cards:cards,serviceNote:serviceNote,stamp:s?.generatedAt};
}
var api={view:view,visible:visible};if(typeof module==='object'&&module.exports)module.exports=api;else root.RoomLiveData=api;
})(typeof globalThis==='undefined'?this:globalThis);
