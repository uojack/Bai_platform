/* Additive, read-only layer. No playback, audio, scene or device commands. */
(function(){
'use strict';
var config=window.AOS_SCREEN_SUPPLEMENT||{},host=document.querySelector('.screen');if(!host)return;
var panel=document.createElement('section');panel.className='aos-supplement';panel.setAttribute('aria-label','AOS 电梯厅实时数据补充');
panel.innerHTML='<div class="aos-s-head"><span>AOS LIVE · 电梯厅</span><small id="aos-s-time">等待实时数据</small></div><div class="aos-s-grid">'+['perception','cognition','core','field'].map(function(id,i){return '<article data-aos="'+id+'"><label>'+['感知 · PERCEPTION','认知 · COGNITION','调度 · BAICORE','渲染 · BAIFIELD'][i]+'</label><strong>等待接入</strong><span></span><small></small></article>';}).join('')+'</div><svg class="aos-s-trace" viewBox="0 0 1000 24" preserveAspectRatio="none" aria-label="电梯厅实际声能趋势"><path/><circle r="1.6"/></svg>';
host.appendChild(panel);
var snapshot=null,receipt=0,samples=[],frame=null,lastStamp=0,pending=false;
var f=function(v,d){return typeof v==='number'&&isFinite(v)?v.toFixed(d||0):'—';};
var events={'background noise':'背景噪声','mechanisms':'机械声','speech':'语音','music':'音乐','silence':'静音'};
function eventName(v){return String(v||'').split(/\s*\/\s*/).map(function(s){return events[s.toLowerCase()]||s;}).join(' / ');}
function displayName(value){return String(value||'').replace(/\bD\d+\b/g,'').replace(/·\s*·/g,'·').trim();}
function set(id,value,line,note,live){var e=panel.querySelector('[data-aos="'+id+'"]');e.classList.toggle('current',!!live);e.querySelector('strong').textContent=displayName(value);e.querySelector('span').textContent=displayName(line);e.querySelector('small').textContent=displayName(note);}
function trend(){var now=performance.now(),path='',prev=null;var recent=samples.filter(function(s){return now-s.at<=180000;}),values=recent.filter(function(s){return s.value!==null;}).map(function(s){return s.value;}),low=values.length?Math.min.apply(null,values)-2:-80,high=values.length?Math.max.apply(null,values)+2:0;recent.forEach(function(s){if(s.value===null){prev=null;return;}var x=1000-(now-s.at)/180,y=22-Math.max(0,Math.min(1,(s.value-low)/(high-low)))*20;path+=(prev!==null&&s.at-prev<6000?'L':'M')+x.toFixed(1)+','+y.toFixed(1);prev=s.at;});panel.querySelector('path').setAttribute('d',path);var dot=panel.querySelector('circle'),last=recent[recent.length-1];if(last&&last.value!==null&&now-last.at<6000){dot.setAttribute('cx',1000-(now-last.at)/180);dot.setAttribute('cy',22-Math.max(0,Math.min(1,(last.value-low)/(high-low)))*20);dot.style.display='';}else dot.style.display='none';}
function render(){
 var elapsed=(performance.now()-receipt)/1000,live=!!snapshot&&elapsed<=7,s=live?snapshot:null,l=s&&s.sensors;
 var pa=l&&typeof l.people==='number'&&l.detectionAge+elapsed<=5?l.people:null,db=l&&typeof l.dbfs==='number'&&l.audioAge+elapsed<=5?l.dbfs:null;
 set('perception',pa===null?'人数等待更新':pa+' 人 · 视觉估计',db===null?'本路音频等待更新':f(db,1)+' dBFS · 摄像头音频','电梯厅 · 声能趋势非 PCM 波形',pa!==null||db!==null);
 var sem=s&&s.semantic,semLive=sem&&sem.state==='CURRENT'&&sem.age+elapsed<=120;
 var sources=s&&s.acoustics.sources||[],source=sources.find(function(x){return x.current&&x.parameters.M01&&x.parameters.M01.age+elapsed<=180;}),r=source&&source.parameters.M01;
 set('cognition',semLive?'视觉语义：'+sem.label:'语义结果等待更新',r?'研发声学：'+eventName(r.value):'研发声学结果等待更新',r?source.name+' · 非本厅实测':'语义服务器等待数据连接',semLive||!!r);
 var core=s&&s.core,cv=core&&core.state==='OBSERVED';var statuses={HOLD:'保持 / HOLD',RUNNING:'运行中',STOPPED:'已停止',IDLE:'待机'};
 set('core',cv?(statuses[core.status]||core.status||'状态未知'):'调度状态未知',cv?'场景 '+f(core.sceneIndex)+' · '+(core.outputArmed===true?'输出已授权':core.outputArmed===false?'输出未授权':'授权未知'):'等待编排状态回执','AOS 演示编排 · 全局 · 只读',cv);
 var field=s&&s.field,fv=field&&field.state==='OBSERVED'&&field.age+elapsed<=12;
 var phase=fv?(field.audioState==='SAFE_MUTED'?'安全静音':field.phase==='STOPPED'?'渲染停止':field.nativeActive===true&&field.readbackVerified===true?'原生渲染运行':'等待渲染证据'):'声场状态未知';
 set('field',phase,fv?'效果 '+String(field.effect||'未知'):'等待声场状态回执',fv?'网关转报 · 全局 · xrun '+f(field.xruns):'不代表电梯厅扬声器实测',fv);
 panel.querySelector('#aos-s-time').textContent=live?'源采样 '+new Date(s.generatedAt*1000).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+' · 2 秒更新':'数据暂不可用 · 保留原节目';trend();
}
async function refresh(){if(pending||document.hidden)return;pending=true;var c=new AbortController(),timer=setTimeout(function(){c.abort();},4000);
 try{var r=await fetch(config.endpoint,{cache:'no-store',signal:c.signal});if(!r.ok)throw Error('HTTP');var x=await r.json();if(x.schema!=='aos.spatial-screen.v1'||x.sensors.camera!=='D8')throw Error('SOURCE');
  // A repeated response cannot renew the validity of frozen source data.
  if(x.generatedAt>lastStamp){lastStamp=x.generatedAt;snapshot=x;receipt=performance.now();if(frame!==x.sensors.frame){samples.push({at:receipt,value:x.sensors.dbfs});samples=samples.filter(function(s){return receipt-s.at<=180000;});frame=x.sensors.frame;}}
 }catch(e){snapshot=null;samples.push({at:performance.now(),value:null});samples=samples.slice(-100);}finally{clearTimeout(timer);pending=false;render();}}
render();refresh();setInterval(refresh,2000);setInterval(render,1000);
})();
