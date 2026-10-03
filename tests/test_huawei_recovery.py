import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools import huawei_panorama_autostart as app

class EndRun(Exception): pass
class RecoveryTests(unittest.TestCase):
    def exercise(self, mode, ticks=14):
        display=app.Display('192.0.2.3','02:00:00:00:00:05','test-udn','left','film.mp4','title')
        state={'tick':0,'pushes':0,'playing':mode!='retry'}
        class Adapter:
            def describe(self, address):return {'UDN':'test-udn'}
            def transport_info(self, address):
                stopped=mode in {'manual','ended'} and state['tick']>=1 and state['pushes']==0
                return {'CurrentTransportState':'STOPPED' if stopped else ('PLAYING' if state['playing'] else 'NO_MEDIA_PRESENT')}
            def position_info(self,address):
                return {'TrackURI':display.url if state['playing'] else '', 'TrackDuration':'00:15:00','RelTime':'00:15:00' if mode=='ended' else '00:02:00'}
            def push(self,*args):
                state['pushes']+=1
                if mode=='retry' and state['pushes']==1:raise TimeoutError('boot not ready')
                state['playing']=True
                return {'transport':{'CurrentTransportState':'PLAYING'}}
        def sleep(_):
            state['tick']+=1
            if state['tick']>=ticks:raise EndRun()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'tools').mkdir();(root/'runtime').mkdir()
            with patch.object(app,'DISPLAYS',[display]),patch.object(app,'__file__',str(root/'tools/supervisor.py')),patch.object(app,'HuaweiDLNAAdapter',return_value=Adapter()),patch.object(app,'refresh_address'),patch.object(app.time,'monotonic',side_effect=lambda:1000+8*state['tick']),patch.object(app.time,'sleep',side_effect=sleep):
                with self.assertRaises(EndRun):app.run()
        return state
    def test_service_restart_adopts_existing_playback(self):self.assertEqual(self.exercise('playing')['pushes'],0)
    def test_training_manual_stop_is_not_overridden(self):self.assertEqual(self.exercise('manual')['pushes'],0)
    def test_finished_media_is_replayed(self):self.assertEqual(self.exercise('ended')['pushes'],1)
    def test_failed_boot_push_retries_after_backoff(self):self.assertEqual(self.exercise('retry')['pushes'],2)
