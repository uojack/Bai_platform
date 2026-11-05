"""Durable manual commands consumed only by the BaiPlay supervisor.

The web process never sends television commands. Known UDNs, fixed programme
choices, a fresh media source, and a single controller remain mandatory.
"""
from __future__ import annotations
import concurrent.futures
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
from adapters.network import direct_opener
import urllib.request
import sqlite3
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
PROGRAMMES={
 'pulse':{'name':'百灵岛 · 六页轮播','filename':'pulse-v14/pulse/live.m3u8','preview':'pulse'},
 'panorama1':{'name':'全景左 · 空间状态','filename':'pulse-v14/panorama1/live.m3u8','preview':'panorama1'},
 'panorama2':{'name':'全景中 · 感知与理解','filename':'pulse-v14/panorama2/live.m3u8','preview':'panorama2'},
 'panorama3':{'name':'全景右 · 计算与响应','filename':'pulse-v14/panorama3/live.m3u8','preview':'panorama3'},
}
DEFAULTS={'lagooon':'pulse','left':'panorama1','center':'panorama2','right':'panorama3'}
NAMES={'lagooon':'百灵岛','left':'全景厅 · 左','center':'全景厅 · 中','right':'全景厅 · 右'}

class Bridge:
 def __init__(self,root=ROOT,probe=None):
  self.root=Path(root);self.path=self.root/'runtime/baitv.sqlite'
  self.probe=probe or self.probe_media
  self.path.parent.mkdir(parents=True,exist_ok=True)
  with self.db()as d:
   d.executescript('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, request TEXT NOT NULL, state TEXT NOT NULL, result TEXT, created REAL NOT NULL, updated REAL NOT NULL); CREATE TABLE IF NOT EXISTS desired(role TEXT PRIMARY KEY, programme TEXT NOT NULL, auto INTEGER NOT NULL, updated REAL NOT NULL); CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);')
  os.chmod(self.path,0o600)
 @contextmanager
 def db(self):
  d=sqlite3.connect(self.path,timeout=5);d.row_factory=sqlite3.Row;d.execute('PRAGMA busy_timeout=5000')
  try:
   with d:yield d
  finally:d.close()
 def validate(self,body):
  if not isinstance(body,dict)or set(body)!={'id','targets','autoRecover'}:raise ValueError('请求字段不正确')
  if not isinstance(body['id'],str)or str(uuid.UUID(body['id']))!=body['id']:raise ValueError('操作编号不正确')
  if type(body['autoRecover'])is not bool:raise ValueError('守护设置不正确')
  targets=body['targets']
  if not isinstance(targets,list)or not 1<=len(targets)<=4:raise ValueError('请选择1至4台电视')
  seen=set()
  for t in targets:
   if not isinstance(t,dict)or set(t)!={'role','programme'}or t['role']not in DEFAULTS or t['programme']not in PROGRAMMES or t['role']in seen:raise ValueError('电视或节目选择不正确')
   seen.add(t['role'])
  return body
 def submit(self,body):
  self.validate(body);raw=json.dumps(body,sort_keys=True,separators=(',',':'));fp=hashlib.sha256(raw.encode()).hexdigest();now=time.time()
  with self.db()as d:
   d.execute('BEGIN IMMEDIATE');old=d.execute('SELECT * FROM jobs WHERE id=?',(body['id'],)).fetchone()
   if old:
    if old['fingerprint']!=fp:raise ValueError('该操作编号已用于其他选择，请重新核对')
    return self.unpack(old)
   if d.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]>=2:raise ValueError('已有恢复操作进行中，请先查看结果')
   d.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)',(body['id'],fp,raw,'queued',None,now,now))
   d.execute("DELETE FROM jobs WHERE id IN(SELECT id FROM jobs WHERE state NOT IN ('queued','running') ORDER BY created DESC LIMIT -1 OFFSET 200)")
  return self.job(body['id'])
 @staticmethod
 def unpack(row):
  return {'id':row['id'],'state':row['state'],'request':json.loads(row['request']),'result':json.loads(row['result'])if row['result']else None,'createdAt':row['created'],'updatedAt':row['updated']}
 def job(self,key):
  with self.db()as d:r=d.execute('SELECT * FROM jobs WHERE id=?',(key,)).fetchone()
  return self.unpack(r)if r else None
 def recent(self):
  with self.db()as d:return [self.unpack(r)for r in d.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 8')]
 def preferences(self):
  with self.db()as d:return {r['role']:dict(r)for r in d.execute('SELECT * FROM desired')}
 def heartbeat(self):
  with self.db()as d:
   r=d.execute("SELECT value FROM meta WHERE key='heartbeat'").fetchone()
  return json.loads(r[0])if r else None
 def beat(self):
  with self.db()as d:d.execute("INSERT OR REPLACE INTO meta VALUES('heartbeat',?)",(json.dumps({'at':time.time(),'pid':os.getpid()}),))
 def start(self):
  self._controller_lock=(self.path.parent/'baitv-controller.lock').open('a')
  fcntl.flock(self._controller_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  with self.db()as d:d.execute("UPDATE jobs SET state='unknown',result=?,updated=? WHERE state='running'",(json.dumps({'message':'监护重启，原操作结果未确认；请先刷新电视状态，再明确发起恢复。'},ensure_ascii=False),time.time()))
  self.beat()
 def close(self):
  handle=getattr(self,'_controller_lock',None)
  if handle and not handle.closed:handle.close()
 def __del__(self):
  self.close()
 def source(self,programme):
  p=self.root/'web/media'/PROGRAMMES[programme]['filename']
  try:
   age=time.time()-p.stat().st_mtime
   lines=p.read_text().splitlines();segments=[s for s in lines if s and not s.startswith('#')]
   # Restrict all local access to the fixed live programme directory.
   good=0<=age<=20 and bool(segments)and all('/'not in s and '..'not in s for s in segments)and(p.parent/segments[-1]).is_file()
   return {'ready':good,'ageSeconds':round(age,1),'segment':segments[-1]if segments else None}
  except OSError:return {'ready':False,'ageSeconds':None,'segment':None}
 def apply(self,displays):
  preferences=self.preferences()
  for d in displays:
   p=preferences.get(d.role)
   if p and p['programme']in PROGRAMMES:
    prog=PROGRAMMES[p['programme']];d.filename=prog['filename'];d.title='BaiTV · '+prog['name']
 def probe_media(self,programme):
  base=os.environ.get('BAIPLAYER_MEDIA_BASE','http://127.0.0.1:4341/media')
  url=base+'/'+PROGRAMMES[programme]['filename']
  with direct_opener().open(urllib.request.Request(url,method='HEAD'),timeout=3)as response:
   if response.status!=200:raise ValueError('直播媒体服务未就绪，保留原画面')
  return True
 def process(self,adapter,displays):
  self.beat()
  with self.db()as db:
   db.execute('BEGIN IMMEDIATE');r=db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
   if not r:return
   db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?",(time.time(),r['id']))
  request=json.loads(r['request']);self.validate(request);byrole={d.role:d for d in displays}
  def one(t):
   role=t['role'];programme=t['programme'];d=byrole.get(role)
   try:
    if not d:raise ValueError('电视未在本机核验台账中')
    source=self.source(programme)
    if not source['ready']:raise ValueError('本机直播源未更新，保留电视原画面；请检查直播服务')
    identity=adapter.describe(d.address)
    if identity.get('UDN')!=d.udn:raise ValueError('设备身份不匹配，未发送')
    self.probe(programme)
    prog=PROGRAMMES[programme];url=f"{os.environ.get('BAIPLAYER_MEDIA_BASE','http://127.0.0.1:4341/media')}/{prog['filename']}"
    with self.db()as db:db.execute('INSERT OR REPLACE INTO desired VALUES(?,?,?,?)',(role,programme,int(request['autoRecover']),time.time()))
    d.filename=prog['filename'];d.title='BaiTV · '+prog['name']
    response=adapter.push(d.address,url,d.title,mime_type='video/m3u8')
    state=response.get('transport',{}).get('CurrentTransportState','UNKNOWN');d.managed=state in ('PLAYING','TRANSITIONING','PAUSED_PLAYBACK');d.deploy_attempted=True;d.deploy_due=None;d.recovery_due=None;d.retry_count=0;d.eof_confirmed=False
    try:position=adapter.position_info(d.address)
    except Exception:position={}
    uri=position.get('TrackURI')or response.get('position',{}).get('TrackURI')
    return {'role':role,'programme':programme,'state':'sent','transport':state,'uriMatched':uri==url,'message':'已发送，等待持续取流核对；实屏画面需现场确认'}
   except Exception as exc:return {'role':role,'programme':programme,'state':'failed','message':str(exc)[:200]}
  with concurrent.futures.ThreadPoolExecutor(max_workers=4)as pool:results=list(pool.map(one,request['targets']))
  state='done'if all(x['state']=='sent'for x in results)else'partial'if any(x['state']=='sent'for x in results)else'failed'
  with self.db()as db:db.execute('UPDATE jobs SET state=?,result=?,updated=? WHERE id=?',(state,json.dumps({'targets':results},ensure_ascii=False),time.time(),r['id']))
  self.beat()
 def recovery(self,adapter,display,transport,position,now):
  """Recover only the selected managed source. Never take over another input."""
  p=self.preferences().get(display.role)
  if not p or not p['auto']:return False
  uri=position.get('TrackURI','');state=transport.get('CurrentTransportState','')
  if uri!=display.url or state in ('PAUSED_PLAYBACK','PLAYING','TRANSITIONING'):
   if uri==display.url and state=='PLAYING':display.retry_count=0
   display.recovery_due=None;return False
  if state not in ('STOPPED','NO_MEDIA_PRESENT')or display.retry_count>=3:return False
  if display.recovery_due is None:display.recovery_due=now+20;return True
  if now<display.recovery_due:return True
  if not self.source(p['programme'])['ready']:display.recovery_due=now+30;return True
  # Verify the UDN again immediately before every automatic write.
  if adapter.describe(display.address).get('UDN')!=display.udn:raise ValueError('UDN changed; recovery refused')
  self.probe(p['programme'])
  display.retry_count+=1
  adapter.push(display.address,display.url,display.title,mime_type='video/m3u8')
  display.recovery_due=now+60
  return True
