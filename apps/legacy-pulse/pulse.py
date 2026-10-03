"""Read-only Pulse evidence. No synthetic readings, device control, or raw AV retention."""
import copy
from adapters.beo_audio import adapter, register_address
from concurrent.futures import ThreadPoolExecutor
import re
import json
import math
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

def number(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
def epoch(v):
    if number(v):return v/1000 if v>1e11 else v
    if isinstance(v,str):
        try:return datetime.fromisoformat(v.replace('Z','+00:00')).timestamp()
        except ValueError:return None
    return None

def camera_evidence(payload,code,now,previous=None):
    empty={'state':'unmapped' if not code else 'unavailable','cameraCode':code,'people':None,'audio':None,'videoState':'unknown','capturedAt':None}
    monitor=payload.get('multi_camera_monitor',{})
    if not isinstance(monitor,dict): return empty
    cameras=monitor.get('cameras',[])
    if not isinstance(cameras,list): return empty
    matches=[c for c in cameras if isinstance(c,dict) and (c.get('camera_code') or ('D'+str(c.get('camera_number'))))==code]
    if not code:return empty
    if len(matches)!=1 or monitor.get('enabled') is not True:return empty
    c=matches[0]
    # Source timestamps are mandatory: receiving the same cached JSON again is not fresh evidence.
    captured=epoch(c.get('captured_at') or c.get('timestamp') or payload.get('captured_at') or payload.get('timestamp'))
    rev=c.get('frame_id',payload.get('frame_id',monitor.get('data_revision')))
    timely=captured is not None and -2<=now-captured<=10
    advancing=rev is not None and previous is not None and previous.get('revision')!=rev and (captured or 0)>previous.get('capturedAt',0)
    live=timely and advancing
    result={**empty,'state':'live' if live else 'stale','cameraName':c.get('location_name',''),'capturedAt':captured,'validUntil':captured+10 if captured else 0,'revision':rev,'videoState':'live' if live and c.get('video_state')=='live' else 'unverified'}
    age=c.get('detection_age_seconds')
    if live and c.get('detection_stale') is False and number(age) and 0<=age<=10 and isinstance(c.get('person_count'),int) and not isinstance(c['person_count'],bool) and c['person_count']>=0:result['people']=c['person_count'];result['peopleValidUntil']=now+10-age
    age=c.get('audio_age_seconds')
    if live and c.get('audio_stale') is False and number(age) and 0<=age<=10:
        if c.get('sound_calibrated') is True and c.get('sound_unit')=='dB SPL' and number(c.get('sound_pressure_level_db_spl')):
            result['audio']={'value':c['sound_pressure_level_db_spl'],'unit':'dB SPL','calibrated':True}
        elif c.get('sound_unit')=='dBFS' and number(c.get('sound_level_db')):
            result['audio']={'value':c['sound_level_db'],'unit':'dBFS','calibrated':False}
        elif number(c.get('dbfs')):result['audio']={'value':c['dbfs'],'unit':'dBFS','calibrated':False}
    if result.get('audio') is not None:result['audioValidUntil']=now+10-age
    return result

class Pulse:
    def __init__(self, config_path, screens=None):
        self.path = Path(config_path)
        self.config = json.loads(self.path.read_text())
        self.screens = screens or (lambda: {})
        self.lock = threading.RLock()
        self.data = {}; self.history = []; self.previous = {}; self.discovered = {}

    def start(self): threading.Thread(target=self._run, daemon=True, name='pulse-readonly').start()

    def _fetch(self, url):
        started=time.monotonic(); result={'url':url,'checkedAt':time.time(),'ok':False,'latencyMs':None}
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'Accept':'application/json'}),timeout=3) as response:
                raw=response.read(2_000_001)
                if len(raw)>2_000_000: raise ValueError('response too large')
                payload=json.loads(raw)
                if not isinstance(payload,dict): raise ValueError('object required')
                result.update(ok=True,httpStatus=response.status,latencyMs=round((time.monotonic()-started)*1000,1)); return result,payload
        except urllib.error.HTTPError as e: result.update(httpStatus=e.code,error='上游接口不可用')
        except Exception: result['error']='连接失败或数据格式无效'
        result['elapsedMs']=round((time.monotonic()-started)*1000,1)
        return result,None

    def _persist(self, config):
        # Single process, locked revision check; replace prevents a half-written configuration.
        import os, tempfile
        with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False, encoding='utf-8') as f:
            temp=f.name
            try:
                json.dump(config,f,ensure_ascii=False,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
            except Exception:
                os.unlink(temp); raise
        try: os.replace(temp,self.path)
        finally:
            if os.path.exists(temp): os.unlink(temp)
        self.config=config

    def _revision(self, payload):
        if type(payload.get('expectedRevision')) is not int or payload['expectedRevision'] != self.config.get('revision',0):
            raise ValueError('感知配置已变化，请刷新后再保存')

    def sources(self):
        with self.lock:
            c=copy.deepcopy(self.config); discovered=copy.deepcopy(self.discovered); vision_ok=self.data.get('vision',{}).get('ok',False)
        screens=self.screens()
        catalog={item['code']:item for item in c.get('cameraCatalog',[])}
        catalog.update(discovered)
        for binding in c.get('screens',{}).values():
            for code in binding.get('cameraCodes',[]): catalog.setdefault(code,{'code':code,'name':'手动登记 · 等待视觉接口核验'})
        return {'revision':c.get('revision',0),'maintenance':bool(c.get('maintenance')) and not vision_ok, 'sourceLabel':c.get('sourceLabel',''),
                'screens':[{'id':k,'name':d.get('name',k),'region':c.get('screens',{}).get(k,{}).get('region',d.get('zone_id','')),
                            'cameraCodes':c.get('screens',{}).get(k,{}).get('cameraCodes',[]),
                            'audioIds':c.get('screens',{}).get(k,{}).get('audioIds',[])} for k,d in screens.items()],
                'cameras':list(catalog.values()),
                'audioSources':[{'id':a['id'],'name':a['name'],'region':a.get('region','位置待确认'),
                                 'address':a.get('baseUrl',''),'configured':adapter(a) is not None,
                                 'identity':a.get('expectedMac') or a.get('expectedJid') or '',
                                 'provider':a.get('provider','pending')} for a in c.get('audioSources',[])]}

    def bind(self, payload):
        device=payload.get('deviceId')
        if device not in self.screens(): raise ValueError('请选择已登记的显示屏')
        codes=payload.get('cameraCodes'); audio_ids=payload.get('audioIds')
        if not isinstance(codes,list) or len(codes)>32 or any(not isinstance(x,str) or not re.fullmatch(r'[\w.:-]{1,64}',x) for x in codes) or len(set(codes))!=len(codes):
            raise ValueError('摄像头通道编号无效或重复（最多 32 路）')
        if not isinstance(audio_ids,list) or len(audio_ids)>8 or any(not isinstance(x,str) for x in audio_ids) or len(set(audio_ids))!=len(audio_ids):
            raise ValueError('请选择不重复的音响（最多 8 台）')
        region=payload.get('region','')
        if not isinstance(region,str) or not 1<=len(region.strip())<=80: raise ValueError('请填写屏幕所在区域')
        with self.lock:
            self._revision(payload)
            if not set(audio_ids)<=set(a['id'] for a in self.config.get('audioSources',[])): raise ValueError('音响尚未登记')
            c=copy.deepcopy(self.config)
            c.setdefault('screens',{})[device]={'region':region.strip(),'cameraCodes':codes,'audioIds':audio_ids,'confirmedAt':time.time()}
            c['revision']=c.get('revision',0)+1; self._persist(c)
        return self.sources()

    def connect_audio(self, payload):
        audio_id=payload.get('audioId')
        with self.lock:
            self._revision(payload)
            if audio_id not in {a['id'] for a in self.config.get('audioSources',[])}: raise ValueError('音响尚未登记')
        detected=register_address(payload.get('address'))
        region=payload.get('region','位置待确认')
        if not isinstance(region,str) or not 1<=len(region.strip())<=80: raise ValueError('请填写音响所在区域')
        with self.lock:
            self._revision(payload)
            identity=detected.get('expectedMac') or detected.get('expectedJid')
            if any(a['id']!=audio_id and (a.get('expectedMac') or a.get('expectedJid'))==identity for a in self.config.get('audioSources',[])):
                raise ValueError('这台音响已登记，请选用已有设备，避免重复关联')
            c=copy.deepcopy(self.config)
            old=next(a for a in c['audioSources'] if a['id']==audio_id)
            if identity == (old.get('expectedMac') or old.get('expectedJid')) and old.get('fallbackUrls'):
                detected['fallbackUrls']=old['fallbackUrls']
            old.clear(); old.update(id=audio_id,region=region.strip(),**detected)
            c['revision']=c.get('revision',0)+1; self._persist(c)
            self.data.get('audioSources',{}).pop(audio_id,None)
        return self.sources()

    def poll(self):
        with self.lock: config=copy.deepcopy(self.config)
        # Each configured speaker is read once, even when several displays share it.
        def speaker_read(a):
            reader=adapter(a)
            base={'id':a['id'],'name':a['name'],'state':'pending','volume':None,'address':a.get('baseUrl'), 'checkedAt':None}
            return {**base,**(reader.snapshot() if reader else {})}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures=[(a,pool.submit(speaker_read,a)) for a in config.get('audioSources',[])]
            network,health=self._fetch(config['managementUrl'])
            if health is not None and (health.get('ok') is not True or not isinstance(health.get('observerBase'),str)): network.update(ok=False,error='管理服务身份未验证')
            audio={a['id']:f.result() for a,f in futures}
        vision,payload=self._fetch(config['visionUrl'])
        now=time.time(); payload=payload or {}; monitor=payload.get('multi_camera_monitor',{})
        cameras=monitor.get('cameras',[]) if isinstance(monitor,dict) else []
        if not isinstance(cameras,list): cameras=[]
        discovered={}
        for camera in cameras:
            if not isinstance(camera,dict): continue
            code=camera.get('camera_code') or ('D'+str(camera['camera_number']) if camera.get('camera_number') is not None else None)
            if isinstance(code,str) and re.fullmatch(r'[\w.:-]{1,64}',code):
                discovered[code]={'code':code,'name':str(camera.get('location_name') or code)[:120],'lastSeenAt':now}
        codes=set(discovered)
        for binding in config.get('screens',{}).values(): codes.update(binding.get('cameraCodes',[]))
        evidence={}
        for code in codes:
            item=camera_evidence(payload,code,now,self.previous.get(code))
            if not vision.get('ok'): item['state']='maintenance' if config.get('maintenance') else 'unavailable'
            if item.get('revision') is not None: self.previous[code]={'revision':item['revision'],'capturedAt':item.get('capturedAt') or 0}
            evidence[code]=item
        with self.lock:
            # Do not publish readings fetched using an address that changed during the poll.
            current={a['id']:a for a in self.config.get('audioSources',[])}
            audio={a['id']:audio[a['id']] for a in config.get('audioSources',[]) if current.get(a['id'])==a}
            self.discovered.update(discovered)
            self.history.append({'at':network['checkedAt'],'latencyMs':network['latencyMs'] if network['ok'] else None}); self.history=self.history[-60:]
            self.data={'checkedAt':time.time(),'vision':vision,'network':network,'cameras':evidence,'audioSources':audio,
                       'sourceLabel':config['sourceLabel'],'sampleInterval':config.get('pollSeconds',10),'rawMediaConnected':False}

    def snapshot(self, device=None):
        with self.lock: data=copy.deepcopy(self.data); history=copy.deepcopy(self.history); config=copy.deepcopy(self.config)
        if device and device not in self.screens(): raise ValueError('该设备不属于已登记显示屏')
        binding=config.get('screens',{}).get(device,{}) if device else {}
        now=time.time(); fresh=bool(data) and now-data['checkedAt']<20
        cameras=[]
        for code in binding.get('cameraCodes',[]):
            p=data.get('cameras',{}).get(code,{'cameraCode':code,'state':'maintenance' if config.get('maintenance') else 'unavailable','people':None,'audio':None})
            if not fresh or (p.get('state')=='live' and now>p.get('validUntil',0)): p.update(state='stale',people=None,audio=None,videoState='unverified')
            if now>p.get('peopleValidUntil',0): p['people']=None
            if now>p.get('audioValidUntil',0): p['audio']=None
            cameras.append(p)
        audio=[]
        for audio_id in binding.get('audioIds',[]):
            configured=next((a for a in config.get('audioSources',[]) if a['id']==audio_id),{})
            s=data.get('audioSources',{}).get(audio_id,{'id':audio_id,'name':configured.get('name',audio_id),'state':'pending' if not configured.get('baseUrl') else 'unavailable','volume':None,'address':configured.get('baseUrl')})
            if s.get('state')!='pending' and (not fresh or now-(s.get('checkedAt') or 0)>20): s.update(state='stale',volume=None,maximum=None,muted=None,power=None,source=None)
            audio.append(s)
        if not fresh:
            for key in ['network','vision']: data.setdefault(key,{}).update(ok=False,latencyMs=None)
        empty={'state':'maintenance' if config.get('maintenance') and not data.get('vision',{}).get('ok') else 'unmapped','people':None,'audio':None}
        return {**data,'deviceId':device,'region':binding.get('region','未配置区域' if device else '节目预览 · 未选择屏幕'),
                'bindingRevision':config.get('revision',0),'mappingConfirmed':bool(binding.get('cameraCodes')),
                'maintenance':bool(config.get('maintenance')) and not data.get('vision',{}).get('ok'),
                'cameras':cameras,'audioSources':audio,'perception':cameras[0] if len(cameras)==1 else empty,
                'regionalAudio':audio[0] if len(audio)==1 else None,
                'fresh':fresh,'serverTime':now,'history':history}

    def _run(self):
        while True:
            try: self.poll()
            except Exception: pass # Snapshot expires; never substitute fake evidence.
            time.sleep(max(5,self.config.get('pollSeconds',10)))
