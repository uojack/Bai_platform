import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
import uuid
from types import SimpleNamespace
from apps.baitv.bridge import Bridge,PROGRAMMES
from apps.baitv.server import App,Handler,Server

class Adapter:
 def __init__(self):self.pushes=[];self.udn='known'
 def describe(self,address):return {'UDN':self.udn}
 def push(self,address,url,title,mime_type=None):self.pushes.append((address,url));return {'transport':{'CurrentTransportState':'TRANSITIONING'},'position':{'TrackURI':url}}
 def position_info(self,address):return {'TrackURI':self.pushes[-1][1]if self.pushes else ''}

class BaiTVTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'private').mkdir()
  (self.root/'private/site.json').write_text(json.dumps({'tvSubnet':'192.0.2.0/24','localAddress':'192.0.2.5','mediaPort':4341}))
  (self.root/'private/displays.json').write_text('[]');self.b=Bridge(self.root,probe=lambda _:True);self.a=Adapter();self.d=SimpleNamespace(role='left',address='192.0.2.10',udn='known',filename=PROGRAMMES['panorama1']['filename'],title='test',retry_count=0,recovery_due=None)
  self.d.url='http://127.0.0.1:4341/media/'+self.d.filename
 def tearDown(self):self.tmp.cleanup()
 def body(self,prog='panorama1'):return {'id':str(uuid.uuid4()),'targets':[{'role':'left','programme':prog}],'autoRecover':True}
 def source(self):
  p=self.root/'web/media'/PROGRAMMES['panorama1']['filename'];p.parent.mkdir(parents=True);p.write_text('#EXTM3U\n#EXTINF:2,\nseg_1.ts\n');(p.parent/'seg_1.ts').write_bytes(b'segment')
 def test_replay_and_conflict(self):
  r=self.body();self.assertEqual(self.b.submit(r)['id'],self.b.submit(r)['id']);r['autoRecover']=False
  with self.assertRaises(ValueError):self.b.submit(r)
 def test_fixed_targets_and_programmes(self):
  for field,value in [('role','unknown'),('programme','http://evil/')]:
   r=self.body();r['targets'][0][field]=value
   with self.assertRaises(ValueError):self.b.submit(r)
  r=self.body();r['targets']*=2
  with self.assertRaises(ValueError):self.b.submit(r)
 def test_source_failure_does_not_write(self):
  r=self.b.submit(self.body());self.b.process(self.a,[self.d]);self.assertEqual(self.b.job(r['id'])['state'],'failed');self.assertFalse(self.a.pushes)
 def test_udn_mismatch_does_not_write(self):
  self.source();self.a.udn='other';r=self.b.submit(self.body());self.b.process(self.a,[self.d]);self.assertFalse(self.a.pushes);self.assertEqual(self.b.job(r['id'])['state'],'failed')
 def test_success_consumed_once_and_preferences_persist(self):
  self.source();r=self.b.submit(self.body());self.b.process(self.a,[self.d]);self.b.process(self.a,[self.d]);self.assertEqual(len(self.a.pushes),1);self.assertEqual(self.b.job(r['id'])['state'],'done');self.assertTrue(Bridge(self.root).preferences()['left']['auto'])
 def test_interrupted_command_not_replayed(self):
  r=self.b.submit(self.body())
  with self.b.db()as d:d.execute("UPDATE jobs SET state='running'")
  self.b.start();self.assertEqual(self.b.job(r['id'])['state'],'unknown');self.b._controller_lock.close()
 def test_stale_source_refused(self):
  self.source();p=self.root/'web/media'/PROGRAMMES['panorama1']['filename'];os.utime(p,(time.time()-50,time.time()-50));self.assertFalse(self.b.source('panorama1')['ready'])
 def test_media_service_failure_does_not_push(self):
  self.source()
  def failed(_):raise ValueError('media offline')
  self.b.probe=failed;r=self.b.submit(self.body());self.b.process(self.a,[self.d]);self.assertFalse(self.a.pushes);self.assertEqual(self.b.job(r['id'])['state'],'failed')
 def test_source_switch_and_pause_respected(self):
  with self.b.db()as d:d.execute('INSERT INTO desired VALUES(?,?,?,?)',('left','panorama1',1,time.time()))
  self.assertFalse(self.b.recovery(self.a,self.d,{'CurrentTransportState':'STOPPED'},{'TrackURI':'http://other'},50))
  self.assertFalse(self.b.recovery(self.a,self.d,{'CurrentTransportState':'PAUSED_PLAYBACK'},{'TrackURI':self.d.url},50));self.assertFalse(self.a.pushes)
 def test_recovery_grace_and_identity(self):
  self.source()
  with self.b.db()as d:d.execute('INSERT INTO desired VALUES(?,?,?,?)',('left','panorama1',1,time.time()))
  self.b.recovery(self.a,self.d,{'CurrentTransportState':'STOPPED'},{'TrackURI':self.d.url},50);self.assertFalse(self.a.pushes)
  self.a.udn='wrong'
  self.assertTrue(self.b.recovery(self.a,self.d,{'CurrentTransportState':'STOPPED'},{'TrackURI':self.d.url},71))
  self.assertFalse(self.a.pushes)
 def test_automatic_recovery_is_bounded_and_resumes_after_cooldown(self):
  self.source()
  with self.b.db()as d:d.execute('INSERT INTO desired VALUES(?,?,?,?)',('left','panorama1',1,time.time()))
  for now in [50,71,132,193,254]:self.b.recovery(self.a,self.d,{'CurrentTransportState':'STOPPED'},{'TrackURI':self.d.url},now)
  self.assertEqual(len(self.a.pushes),3)
  self.b.recovery(self.a,self.d,{'CurrentTransportState':'STOPPED'},{'TrackURI':self.d.url},500);self.assertEqual(len(self.a.pushes),4)
 def test_heartbeat_carries_controller_resolved_addresses(self):
  self.b.beat([self.d]);self.assertEqual(self.b.heartbeat()['addresses'],{'left':'192.0.2.10'})
 def test_http_direct_access_and_same_origin_control(self):
  app=App(self.root)
  class RemoteHandler(Handler):
   def local(self):return False
  server=Server(('127.0.0.1',0),app);server.RequestHandlerClass=RemoteHandler;t=threading.Thread(target=server.serve_forever,daemon=True);t.start();url='http://127.0.0.1:'+str(server.server_port)
  try:
   with urllib.request.urlopen(url+'/')as r:
    html=r.read().decode();self.assertNotIn('pairForm',html);self.assertNotIn('连接码',html)
   with urllib.request.urlopen(url+'/api/session')as r:
    session=json.load(r);self.assertTrue(session['authorised']);self.assertFalse(session['local']);self.assertEqual(session['accessMode'],'lan_direct');self.assertNotIn('pairingPin',session);self.assertIsNone(r.headers.get('Set-Cookie'))
   with urllib.request.urlopen(url+'/api/status')as r:self.assertEqual(r.status,200)
   app.bridge.beat();body=self.body()
   def request(origin=None,host=None):
    headers={'Content-Type':'application/json'}
    if origin:headers['Origin']=origin
    if host:headers['Host']=host
    return urllib.request.Request(url+'/api/repair',data=json.dumps(body).encode(),headers=headers,method='POST')
   for req in [request(),request('http://other.invalid'),request('http://other.invalid','other.invalid')]:
    with self.assertRaises(urllib.error.HTTPError)as e:urllib.request.urlopen(req)
    self.assertEqual(e.exception.code,403)
   with urllib.request.urlopen(request(url))as r:
    self.assertEqual(r.status,202);self.assertEqual(json.load(r)['state'],'queued')
  finally:server.shutdown();server.server_close();t.join()
 def test_public_network_address_is_refused(self):
  app=App(self.root);fake=SimpleNamespace(client_address=('8.8.8.8',1),headers={'Host':'baitv'},app=app)
  self.assertFalse(Handler.permitted(fake))

class ProjectionTests(unittest.TestCase):
 def test_visual_v15_allowlist(self):
  path=Path(__file__).resolve().parents[1]/'apps/four-screen/tools/feed_server.py';spec=importlib.util.spec_from_file_location('feed_projection_test',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  raw={'schema_version':'1.5','event_type':'vision.result','frame':{'id':1,'timestamp':time.time()},'persons':[{'face':'secret'}],'metrics':{'fps':20,'secret':'drop'},'multi_camera_monitor':{'enabled':True,'cameras':[{'camera_code':'D4','person_count':0,'image':'drop'}]}}
  p=m.projection(raw);self.assertEqual(p['schema_version'],'1.5');self.assertNotIn('persons',p);self.assertNotIn('secret',p['metrics']);self.assertNotIn('image',p['multi_camera_monitor']['cameras'][0])
  raw['schema_version']='2.0'
  with self.assertRaises(ValueError):m.projection(raw)
if __name__=='__main__':unittest.main()
