"""B&O status only. Explicit IP registration, identity pinning, no discovery broadcasts."""
import ipaddress
import re
import time
from adapters.beoshape import BeoShape

class Mozart(BeoShape):
    PATHS = {'/api/v1/beolink/self', '/api/v1/state'}
    def snapshot(self):
        result = dict(provider='mozart', state='unavailable', checkedAt=time.time(),
                      address=self.config['baseUrl'], volume=None, maximum=None, muted=None,
                      power=None, source=None, rawAudio=False, environmentMeasurement=False)
        try:
            identity = self.get('/api/v1/beolink/self')
            if not self.config.get('expectedJid') or identity.get('jid') != self.config['expectedJid']:
                return {**result, 'state':'identity_mismatch'}
            result.update(identityVerified=True, name=identity.get('friendlyName', 'B&O'))
            data = self.get('/api/v1/state')
            volume = data.get('volume', {})
            level = volume.get('level', {}).get('level')
            maximum = volume.get('maximum', {}).get('level')
            if type(level) is int and type(maximum) is int and 0 <= level <= maximum <= 100:
                result.update(volume=level, maximum=maximum)
            mute = volume.get('muted', {}).get('muted')
            if type(mute) is bool: result['muted'] = mute
            power = data.get('powerState', {}).get('value')
            if power in {'on','standby','networkStandby','shutdown','storage'}: result['power'] = power
            source = data.get('source', {})
            if isinstance(source, dict):
                name = source.get('name') or source.get('id')
                if isinstance(name, str): result['source'] = name[:80]
            result.update(state='live' if result['volume'] is not None else 'partial', checkedAt=time.time())
        except Exception:
            result.update(state='unavailable', volume=None, maximum=None, muted=None, power=None, source=None)
        return result

def adapter(config):
    if config.get('provider') in {'beoshape', 'beonet'}: return BeoShape(config)
    if config.get('provider') == 'mozart': return Mozart(config)
    return None

def register_address(address):
    """Probe only the operator-supplied private IPv4; pin the identity returned by it."""
    try: ip = ipaddress.IPv4Address(address)
    except (ValueError, TypeError): raise ValueError('请填写音响的内网 IPv4 地址')
    if not any(ip in ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')) or str(ip).split('.')[-1] in {'0','255'}:
        raise ValueError('仅支持明确的内网设备地址')
    legacy = BeoShape({'baseUrl':f'http://{ip}:8080'})
    try: identity = legacy.get('/BeoDevice').get('beoDevice', {})
    except Exception: identity = {}
    mac = identity.get('hardware', {}).get('mac', '')
    product = identity.get('productId', {}).get('productType')
    if isinstance(mac, str) and re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', mac) and isinstance(product, str) and product:
        return dict(provider='beonet', baseUrl=legacy.config['baseUrl'], expectedMac=mac.upper(),
                    expectedProductType=product, name=identity.get('productFriendlyName', {}).get('productFriendlyName') or product)
    mozart = Mozart({'baseUrl':f'http://{ip}'})
    try: identity = mozart.get('/api/v1/beolink/self')
    except Exception: identity = {}
    jid = identity.get('jid')
    if isinstance(jid, str) and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+@products\.bang-olufsen\.com', jid):
        return dict(provider='mozart', baseUrl=mozart.config['baseUrl'], expectedJid=jid, name=identity.get('friendlyName') or 'B&O')
    raise ValueError('未读到可核验的 B&O 身份；地址未保存。请检查 IP、设备开机状态及接口兼容性。')
