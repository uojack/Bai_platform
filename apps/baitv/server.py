#!/usr/bin/env python3
"""BaiTV local network operator application; no independent TV writer."""
from __future__ import annotations
import concurrent.futures
import ipaddress
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from adapters.huawei_dlna import HuaweiDLNAAdapter
from apps.baitv.bridge import Bridge,PROGRAMMES,DEFAULTS,NAMES

class App:
 def __init__(self,root=ROOT):
  self.root=Path(root);self.site=json.loads((self.root/'private/site.json').read_text());self.displays=json.loads((self.root/'private/displays.json').read_text())
  self.bridge=Bridge(self.root);self.adapter=HuaweiDLNAAdapter(subnet=self.site['tvSubnet'],timeout=2.5)
  self.lock=threading.Lock();self.snapshot={'at':0,'devices':[]};self.stopping=threading.Event();self.refresh_event=threading.Event()
 def media_reads(self):
  p=self.root/'runtime/logs/media-access.jsonl';out={};now=time.time()
  try:
   with p.open('rb')as f:f.seek(max(0,p.stat().st_size-1024*1024));lines=f.read().decode(errors='replace').splitlines()[1:]
   for line in lines:
    try:
     x=json.loads(line);addr=x.get('client')or x.get('ip')or x.get('remote');path=x.get('path','');at=x.get('at',x.get('timestamp',0))
     if isinstance(at,str):
      from datetime import datetime
      at=datetime.fromisoformat(at.replace('Z','+00:00')).timestamp()
     if now-float(at)>120 or not path.endswith('.ts')or x.get('status')not in (200,206):continue
     out.setdefault(addr,[]).append((at,path))
    except(ValueError,TypeError):continue
  except OSError:pass
  return out
 def refresh(self):
  prefs=self.bridge.preferences();reads=self.media_reads()
  beat=self.bridge.heartbeat();addresses=beat.get('addresses',{})if beat and time.time()-beat['at']<90 else{}
  def one(d):
   candidate=addresses.get(d['role'])
   if candidate and ipaddress.ip_address(candidate)in self.adapter.subnet:d={**d,'address':candidate}
   role=d['role'];p=prefs.get(role);prog=p['programme']if p else DEFAULTS[role]
   row={'role':role,'name':NAMES[role],'address':d['address'],'programme':prog,'autoRecover':bool(p and p['auto']),'online':False,'verified':False,'transport':'UNKNOWN','error':None,'source':self.bridge.source(prog)}
   try:
    ident=self.adapter.describe(d['address']);row['online']=True;row['verified']=ident.get('UDN')==d['udn']
    if not row['verified']:raise ValueError('设备身份变化，禁止推送')
    row['transport']=self.adapter.transport_info(d['address']).get('CurrentTransportState','UNKNOWN')
    try:
     pos=self.adapter.position_info(d['address']);row['uri']=pos.get('TrackURI');row['elapsed']=pos.get('RelTime')
    except Exception:row['positionUnavailable']=True
    filename=PROGRAMMES[prog]['filename'];base=f"http://{self.site['localAddress']}:{self.site['mediaPort']}/media/"
    row['uriMatched']=row.get('uri')==base+filename
    hits=[(at,path)for at,path in reads.get(d['address'],[])if '/'+filename.rsplit('/',1)[0]+'/' in path]
    row['segments120s']=len(set(path for _,path in hits));row['lastMediaAge']=round(time.time()-max(at for at,_ in hits),1)if hits else None
    # Device protocol and source transfer are distinct from physical pixels.
    row['receiving']=row['uriMatched']and row['lastMediaAge']is not None and row['lastMediaAge']<20 and row['segments120s']>=3
   except Exception as e:row['error']=str(e)[:120]
   return row
  with concurrent.futures.ThreadPoolExecutor(max_workers=4)as pool:rows=list(pool.map(one,self.displays))
  with self.lock:self.snapshot={'at':time.time(),'devices':rows}
 def monitor(self):
  while not self.stopping.is_set():
   try:self.refresh()
   except Exception:pass
   self.refresh_event.wait(10);self.refresh_event.clear()
 def status(self):
  with self.lock:value=dict(self.snapshot)
  if time.time()-value['at']>30:
   value['devices']=[{**d,'receiving':False,'error':'读取结果已过期，等待重新连接','transport':'UNKNOWN'}for d in value['devices']]
  beat=self.bridge.heartbeat();value.update(version='1.0.1',programmes=PROGRAMMES,controller={'online':bool(beat and time.time()-beat['at']<90),'heartbeat':beat},jobs=self.bridge.recent(),phoneUrl='http://baitv.local',lanUrl='http://'+self.site['localAddress'],physicalScreenVerified=False)
  return value

class Handler(BaseHTTPRequestHandler):
 server_version='BaiTV/1.0.1'
 def setup(self):
  super().setup();self.connection.settimeout(12)
 def log_message(self,*args):pass
 @property
 def app(self):return self.server.app
 def local(self):return ipaddress.ip_address(self.client_address[0]).is_loopback
 def headers_safe(self):
  self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY')
  self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
 def respond(self,status,value,mime='application/json; charset=utf-8'):
  raw=value if isinstance(value,bytes)else json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
  self.send_response(status);self.headers_safe();self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)))
  self.end_headers();self.wfile.write(raw)
 def permitted(self):
  try:
   addr=ipaddress.ip_address(self.client_address[0]);host=urlsplit('//'+self.headers.get('Host','')).hostname
   if not(addr.is_private or addr.is_loopback):return False
   return host in {'baitv','baitv.local','localhost','127.0.0.1',self.app.site['localAddress']}
  except(ValueError,TypeError):return False
 def do_GET(self):
  if not self.permitted():return self.respond(403,{'error':'仅供本地网络访问'})
  path=urlsplit(self.path).path
  if path=='/api/session':return self.respond(200,{'authorised':True,'local':self.local(),'accessMode':'lan_direct'})
  if path.startswith('/api/')or path.startswith('/preview/'):
   if path=='/api/status':return self.respond(200,self.app.status())
   if path.startswith('/api/jobs/'):
    job=self.app.bridge.job(path.split('/')[-1]);return self.respond(200,job)if job else self.respond(404,{'error':'没有这条操作记录'})
   if path.startswith('/preview/'):
    name=path.rsplit('/',1)[-1]
    if name not in {p+'.jpg'for p in PROGRAMMES}:return self.respond(404,{'error':'无此节目'})
    p=self.app.root/'runtime/four-screen-evidence'/name
    if p.exists():return self.respond(200,p.read_bytes(),'image/jpeg')
   return self.respond(404,{'error':'未找到'})
  files={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/manifest.webmanifest':'manifest.webmanifest','/icon.svg':'icon.svg'}
  if path not in files:return self.respond(404,{'error':'未找到'})
  p=Path(__file__).parent/'web'/files[path];mime={'html':'text/html','js':'text/javascript','css':'text/css','webmanifest':'application/manifest+json','svg':'image/svg+xml'}[p.suffix[1:]]
  return self.respond(200,p.read_bytes(),mime+'; charset=utf-8')
 def do_POST(self):
  if not self.permitted():return self.respond(403,{'error':'访问地址不正确'})
  host=self.headers.get('Host','');origin=self.headers.get('Origin')
  if origin!='http://'+host:return self.respond(403,{'error':'请从BaiTV页面发起操作'})
  if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.respond(415,{'error':'需要JSON请求'})
  try:
   n=int(self.headers.get('Content-Length','0'))
   if not 0<n<=8192:raise ValueError('请求大小不正确')
   body=json.loads(self.rfile.read(n))
   if self.path=='/api/refresh':self.app.refresh_event.set();return self.respond(202,{'ok':True})
   if self.path=='/api/repair':
    beat=self.app.bridge.heartbeat()
    if not beat or time.time()-beat['at']>90:return self.respond(503,{'error':'电视监护尚未就绪，请稍候刷新'})
    return self.respond(202,self.app.bridge.submit(body))
   return self.respond(404,{'error':'未找到'})
  except(ValueError,TypeError,KeyError)as e:return self.respond(400,{'error':str(e)[:180]})
  except sqlite3.Error:return self.respond(503,{'error':'本机正在保存操作，请按原编号查询后再试'})

class Server(ThreadingHTTPServer):
 daemon_threads=True
 def __init__(self,address,app):self.app=app;super().__init__(address,Handler)

def main():
 app=App();t=threading.Thread(target=app.monitor,daemon=True);t.start()
 server=Server(('0.0.0.0',int(os.environ.get('BAITV_PORT','80'))),app)
 try:server.serve_forever()
 finally:app.stopping.set();app.refresh_event.set();server.server_close()
if __name__=='__main__':main()
