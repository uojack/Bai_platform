"""Bounded read-only AOS projection for supplementary signage data."""
from datetime import datetime
import json,math,time,urllib.request,threading,concurrent.futures

def number(x):return isinstance(x,(int,float))and not isinstance(x,bool)and math.isfinite(x)
def age(at,now):
 try:
  if isinstance(at,str):at=datetime.fromisoformat(at.replace('Z','+00:00')).timestamp()
  return now-at if number(at)else None
 except(ValueError,TypeError):return None
def fresh(a,limit):return number(a)and -5<=a<=limit

def semantic_services(rows,now):
 result=[]
 for row in rows or[]:
  h=row.get('health')or{};kind=row.get('kind');sample_age=age(row.get('checkedAt'),now)
  identity=(kind=='dictionary'and h.get('service')=='acoustic_dictionary'and h.get('instance_role')=='delivery')or(kind=='intent'and h.get('scope')=='INTENT_REASONING'and h.get('deployment_channel')=='DELIVERY')
  version=h.get('version');online=identity and fresh(sample_age,45)and isinstance(version,str)and len(version)<=32
  result.append({'name':row.get('name'),'online':bool(online),'version':version if online else None,'age':sample_age,'resultAccess':row.get('resultAccess','UNKNOWN')if online else'UNKNOWN','resultDataAvailable':False})
 return result

def projection(vision=None,acoustics=None,runtime=None,now=None,camera='D8',semantic_host=None,services=None,location='电梯厅'):
 now=time.time()if now is None else now
 va=age((vision or{}).get('frame',{}).get('timestamp'),now);vf=fresh(va,5)
 monitor=(vision or{}).get('multi_camera_monitor')or{}
 row=next((r for r in monitor.get('cameras',[])if r.get('camera_code')==camera),{})
 def local(kind):
  a=row.get(kind+'_age_seconds');combined=max(0,va)+a if number(va)and number(a)and a>=0 else None
  return vf and monitor.get('enabled')is True and row.get(kind+'_stale')is False and row.get('video_state'if kind=='detection'else'audio_state')=='live'and fresh(combined,5),combined
 dv,da=local('detection');av,aa=local('audio')
 p=row.get('person_count');sound=row.get('dbfs')
 ownp=row.get('person_count_source_camera_code')==camera
 owna=row.get('audio_source_camera_number')==int(camera[1:])and row.get('audio_fallback_active')is False
 sensor={'camera':camera,'location':row.get('location_name',location),'people':p if dv and ownp and number(p)and p>=0 and int(p)==p else None,'dbfs':sound if av and owna and number(sound)and sound<=0 and row.get('sound_unit')=='dBFS'else None,'detectionAge':da,'audioAge':aa,'frame':(vision or{}).get('frame',{}).get('id'),'timestamp':(vision or{}).get('frame',{}).get('timestamp'),'localAudio':owna,'fps':row.get('yolo_fps')if dv else None}
 sem=next((r for r in (vision or{}).get('multimodal_perception',{}).get('results',[])if r.get('camera_code')==camera),{})
 domain=sem.get('domains',{}).get('spatial_attribute')or{}
 semantic={'state':'WAITING','label':None,'source':'视觉服务器 · '+camera,'age':age(domain.get('updated_at'),now),'independentSemanticServer':'ENDPOINT_REQUIRED'if semantic_host else'NOT_CONNECTED','semanticServerHost':semantic_host}
 semantic['services']=semantic_services(services,now)
 if services:semantic['independentSemanticServer']='AUTH_REQUIRED'if all(s['online']and s['resultAccess']=='AUTH_REQUIRED'for s in semantic['services'])else'SERVICE_UNAVAILABLE'
 if vf and sem.get('stale')is False and fresh(age(sem.get('timestamp'),now),120)and fresh(semantic['age'],120)and isinstance(domain.get('label'),str):semantic.update(state='CURRENT',label=domain['label'][:160])
 sources=[];af=fresh(age((acoustics or{}).get('dataCollectedAt'),now),12)and (acoustics or{}).get('transport',{}).get('status')=='OK'
 for s in (acoustics or{}).get('sources',[])[:8]:
  out={'id':s.get('id'),'name':s.get('name'),'spaceId':s.get('spaceId'),'scope':'研发参考 · 非'+location+'实测','parameters':{},'current':False}
  if af and s.get('connection')=='CONNECTED'and s.get('spaceId')=='future_scene'and s.get('status')=='READY'and s.get('observationId')and s.get('resultState')in('FRESH','DELAYED'):
   for p in s.get('parameters',[]):
    if p.get('id')not in('M01','T01','T02','T03','T04','M02','M04','H03'):continue
    ages=[age(w.get('end'),now)for w in p.get('windows',[])]
    if ages and all(fresh(a,180)for a in ages)and p.get('present')and p.get('available')and not p.get('carriedForward')and isinstance(p.get('value'),str):out['parameters'][p['id']]={'value':p['value'][:180],'age':max(ages)}
  out['current']=bool(out['parameters']);sources.append(out)
 core={'state':'UNKNOWN','scope':'AOS 演示编排 · 全局状态'};field={'state':'UNKNOWN','scope':'BaiField 网关转报 · 全局状态'}
 if runtime and runtime.get('ok')is True:
  r=runtime.get('runtime')or{};audio=(runtime.get('hardware')or{}).get('audio')or{}
  core.update({k:r.get(k)for k in('status','mode','sceneIndex','revision','updatedAt','controlPending','outputArmed','audioMuted')});core['state']='OBSERVED'
  fa=age(audio.get('updatedAt'),now);sr=audio.get('sceneRendering')or{}
  if fresh(fa,12):field.update(state='OBSERVED',audioState=audio.get('state'),phase=sr.get('phase'),active=sr.get('active'),nativeActive=sr.get('nativeActive'),readbackVerified=sr.get('readbackVerified'),effect=sr.get('effect'),inputPeakDbfs=audio.get('inputPeakDbfs')if number(audio.get('inputPeakDbfs'))else None,callbackCount=audio.get('callbackCount'),xruns=audio.get('xruns'),age=fa)
 return {'schema':'aos.spatial-screen.v1','generatedAt':now,'sensors':sensor,'semantic':semantic,'acoustics':{'online':bool(af),'sources':sources},'core':core,'field':field,'displayOnly':True}

def region_projection(values,region_id,config,semantic_host=None,services=None,now=None):
 now=time.time()if now is None else now
 result=projection(**values,now=now,camera=config['camera'],location=config['name'],semantic_host=semantic_host,services=services)
 result.update(region=region_id,regionName=config['name'],secondarySensors=[])
 result['sensors']['displayLocation']=config.get('primaryLabel',config['name'])
 for point in config.get('secondary',[]):
  row=projection(vision=values.get('vision'),now=now,camera=point['camera'],location=point['name'])['sensors']
  row['displayLocation']=point['name'];result['secondarySensors'].append(row)
 return result

class Feed:
 def __init__(self,base,core,semantic_host=None,services=None,regions=None):
  self.semantic_host=semantic_host;self.services=services or[];self.health=[];self.next_health=0
  self.regions=regions or{'elevator':{'camera':'D8','name':'电梯厅'}}
  self.urls={'vision':base+'/api/vision','acoustics':base+'/api/acoustics','runtime':core+'/api/show/runtime'}
  self.lock=threading.Lock();self.values={key:region_projection({},key,cfg,semantic_host)for key,cfg in self.regions.items()};self.stop=threading.Event()
 def get(self,url):
  try:
   with urllib.request.urlopen(urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'EOS-SpatialDisplay/0.1'}),timeout=2.5)as r:raw=r.read(2*1024*1024+1)
   if len(raw)>2*1024*1024:return None
   return json.loads(raw)
  except(Exception):return None
 def run(self):
  with concurrent.futures.ThreadPoolExecutor(max_workers=3)as pool:
   while not self.stop.is_set():
    futures={k:pool.submit(self.get,u)for k,u in self.urls.items()};values={k:f.result()for k,f in futures.items()}
    if time.monotonic()>=self.next_health:
     health_futures=[(s,pool.submit(self.get,s['healthUrl']))for s in self.services]
     self.health=[{'kind':s['kind'],'name':s['name'],'health':f.result(),'checkedAt':time.time(),'resultAccess':s.get('resultAccess','UNKNOWN')}for s,f in health_futures]
     self.next_health=time.monotonic()+30
    snapshots={key:region_projection(values,key,cfg,self.semantic_host,self.health)for key,cfg in self.regions.items()}
    with self.lock:self.values=snapshots
    self.stop.wait(2)
 def snapshot(self,region='elevator'):
  with self.lock:return dict(self.values[region])
