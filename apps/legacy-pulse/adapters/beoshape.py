"""Read-only adapter verified against the headquarters Beosound Shape Core."""
import json
import time
import urllib.request
from urllib.parse import urlparse

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class BeoShape:
    PATHS={'/BeoDevice','/BeoZone/Zone/Sound/Volume/Speaker','/BeoZone/Zone/ActiveSources','/BeoDevice/powerManagement'}
    def __init__(self,config):self.config=config
    def get(self,path):
        if path not in self.PATHS:raise ValueError('read endpoint not allowed')
        base=getattr(self,'active_base',self.config['baseUrl']).rstrip('/');u=urlparse(base)
        if u.scheme!='http' or u.username or u.password or u.path:raise ValueError('invalid device base URL')
        request=urllib.request.Request(base+path,method='GET',headers={'Accept':'application/json','Cache-Control':'no-cache'})
        with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=2) as response:
            raw=response.read(262145)
            if len(raw)>262144:raise ValueError('response too large')
            data=json.loads(raw)
            if not isinstance(data,dict):raise ValueError('object required')
            return data
    def snapshot(self):
        result={'provider':self.config.get('provider','beoshape'),'state':'unavailable','checkedAt':time.time(),'address':self.config.get('baseUrl'),'volume':None,'maximum':None,'muted':None,'power':None,'source':None,'rawAudio':False,'environmentMeasurement':False}
        try:
            identity=None
            for base in [self.config['baseUrl'],*self.config.get('fallbackUrls',[])]:
                self.active_base=base
                try:
                    identity=self.get('/BeoDevice').get('beoDevice',{});break
                except Exception:continue
            if identity is None:raise OSError('identity unavailable')
            result.update(address=self.active_base,preferredAddress=self.config['baseUrl'])
            mac=identity.get('hardware',{}).get('mac','').upper()
            expected=self.config.get('expectedMac','').upper()
            if not expected or mac!=expected or identity.get('productId',{}).get('productType')!=self.config.get('expectedProductType','BEOSOUND_SHAPE'):
                result.update(state='identity_mismatch',error='设备身份不符');return result
            result.update(identityVerified=True,name=identity.get('productFriendlyName',{}).get('productFriendlyName','BeoShape'),mac=mac)
            volume=self.get('/BeoZone/Zone/Sound/Volume/Speaker').get('speaker',{})
            level=volume.get('level');maximum=volume.get('range',{}).get('maximum');minimum=volume.get('range',{}).get('minimum')
            if all(type(v) is int for v in [level,maximum,minimum]) and 0<=minimum<=level<=maximum<=100:
                result.update(volume=level,maximum=maximum)
            if type(volume.get('muted')) is bool:result['muted']=volume['muted']
            errors=[]
            try:
                power_data=self.get('/BeoDevice/powerManagement')
                power=power_data.get('profile',power_data).get('powerManagement',{}).get('standby',{}).get('powerState')
                if power in {'standby','on'}:result['power']=power
                else:errors.append('power')
            except Exception:errors.append('power')
            try:
                sources=self.get('/BeoZone/Zone/ActiveSources').get('activeSources',{})
                source=sources.get('primary')
                if isinstance(source,str):result['source']=source.split(':',1)[0][:80]
            except Exception:errors.append('source')
            result.update(state='live' if result['volume'] is not None and not errors else 'partial',checkedAt=time.time(),errors=errors)
        except Exception:result.update(state='unavailable',volume=None,maximum=None,muted=None,power=None,source=None,error='设备只读接口暂不可用')
        return result
