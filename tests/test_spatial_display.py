import unittest,hashlib
from apps.spatial_display.feed import projection
from apps.spatial_display.build import build,MARK,END
class SpatialDisplayTests(unittest.TestCase):
 def visual(self):return {'frame':{'id':1,'timestamp':999},'multi_camera_monitor':{'enabled':True,'cameras':[{'camera_code':'D8','location_name':'电梯厅','person_count':0,'person_count_source_camera_code':'D8','dbfs':-50,'sound_unit':'dBFS','audio_source_camera_number':8,'audio_fallback_active':False,'detection_age_seconds':.2,'audio_age_seconds':.1,'video_state':'live','audio_state':'live','detection_stale':False,'audio_stale':False}]}}
 def test_zero_occupancy_and_own_audio(self):
  d=projection(vision=self.visual(),now=1000);self.assertEqual(d['sensors']['people'],0);self.assertEqual(d['sensors']['dbfs'],-50)
 def test_borrowed_audio_and_people_are_unknown(self):
  v=self.visual();v['multi_camera_monitor']['cameras'][0].update(audio_source_camera_number=15,audio_fallback_active=True,person_count_source_camera_code='D6');s=projection(vision=v,now=1000)['sensors'];self.assertIsNone(s['people']);self.assertIsNone(s['dbfs'])
 def test_old_source_is_not_renewed_by_projection_receipt(self):
  v=self.visual();v['frame']['timestamp']=990;s=projection(vision=v,now=1000)['sensors'];self.assertIsNone(s['people']);self.assertIsNone(s['dbfs'])
 def test_disabled_monitor_and_invalid_local_age(self):
  v=self.visual();v['multi_camera_monitor']['enabled']=False;self.assertIsNone(projection(vision=v,now=1000)['sensors']['people'])
  v=self.visual();v['multi_camera_monitor']['cameras'][0]['detection_age_seconds']=-1;self.assertIsNone(projection(vision=v,now=1000)['sensors']['people'])
 def test_muted_stopped_is_never_rendering_success(self):
  r={'ok':True,'runtime':{'status':'HOLD','outputArmed':False},'hardware':{'audio':{'updatedAt':999,'state':'SAFE_MUTED','sceneRendering':{'phase':'STOPPED','active':False,'nativeActive':False,'readbackVerified':True}}}}
  d=projection(runtime=r,now=1000);self.assertEqual(d['core']['status'],'HOLD');self.assertFalse(d['core']['outputArmed']);self.assertEqual(d['field']['phase'],'STOPPED');self.assertFalse(d['field']['nativeActive'])
  r['hardware']['audio']['updatedAt']=900;self.assertEqual(projection(runtime=r,now=1000)['field']['state'],'UNKNOWN')
 def test_old_or_unbound_semantic_is_not_current(self):
  v=self.visual();v['multimodal_perception']={'results':[{'camera_code':'D8','timestamp':999,'stale':False,'domains':{'spatial_attribute':{'updated_at':800,'label':'走廊'}}}]};d=projection(vision=v,now=1000);self.assertEqual(d['semantic']['state'],'WAITING');self.assertEqual(d['semantic']['independentSemanticServer'],'NOT_CONNECTED')
 def test_acoustic_zero_survives_but_carried_and_wrong_space_are_rejected(self):
  a={'dataCollectedAt':'1970-01-01T00:16:39Z','transport':{'status':'OK'},'sources':[{'id':'microphone-1','name':'研发麦克风','spaceId':'future_scene','connection':'CONNECTED','status':'READY','observationId':'test','resultState':'FRESH','parameters':[{'id':'T01','present':True,'available':True,'value':'0 次/分钟','windows':[{'end':'1970-01-01T00:16:30Z'}]}]}]}
  self.assertEqual(projection(acoustics=a,now=1000)['acoustics']['sources'][0]['parameters']['T01']['value'],'0 次/分钟')
  a['sources'][0]['parameters'][0]['carriedForward']=True;self.assertFalse(projection(acoustics=a,now=1000)['acoustics']['sources'][0]['current'])
  a['sources'][0]['parameters'][0]['carriedForward']=False;a['sources'][0]['spaceId']='elevator';self.assertFalse(projection(acoustics=a,now=1000)['acoustics']['sources'][0]['current'])
 def test_injection_is_reversible_and_keeps_all_original_bytes(self):
  raw=b'<html><body><h1>Existing content</h1><script>old()</script></body></html>';out=build(raw,'http://localhost/api/elevator','http://localhost/elevator');start=out.index(MARK.encode());end=out.index(END.encode())+len(END)+1;self.assertEqual(out[:start]+out[end:],raw)
  with self.assertRaises(ValueError):build(out,'http://localhost/api','http://localhost')
 def test_semantic_host_registration_is_not_algorithm_result_evidence(self):
  d=projection(now=1000,semantic_host='192.0.2.50');self.assertEqual(d['semantic']['semanticServerHost'],'192.0.2.50');self.assertEqual(d['semantic']['independentSemanticServer'],'ENDPOINT_REQUIRED');self.assertEqual(d['semantic']['state'],'WAITING');self.assertIsNone(d['semantic']['label'])
 def test_unknown_has_no_false_sensor_or_render_success(self):
  d=projection(now=1000);self.assertIsNone(d['sensors']['people']);self.assertEqual(d['field']['state'],'UNKNOWN');self.assertEqual(d['core']['state'],'UNKNOWN')
