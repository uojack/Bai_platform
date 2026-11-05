"""Bind an existing six-slot AOS room page without changing its visual/content baseline."""
import json,re
MARK='<!-- AOS-ROOM-LIVE-V1 -->'
END='<!-- /AOS-ROOM-LIVE-V1 -->'
GUARD=b'\n        if (window.AOS_ROOM_LIVE_OWNERSHIP) return;'
ANCHOR=b'function requestBaiHubState() {'
LEGACY=b'var config = window.DOOR_LIVE_CONFIG;'
SLOTS={b'perception',b'cognition',b'orchestration',b'connection',b'rendering',b'scene'}
def build_room(raw,endpoint,asset_base,camera,region,*,name=None,primary_label=None):
 slots=re.findall(rb'<article\b[^>]*\bdata-layer="([a-z]+)"',raw)
 if MARK.encode()in raw or raw.count(b'</head>')!=1 or len(slots)!=6 or set(slots)!=SLOTS:raise ValueError('Original room structure changed')
 if raw.count(ANCHOR)+raw.count(LEGACY)>1 or (b'DOOR_LIVE_CONFIG' in raw and LEGACY not in raw):raise ValueError('Ambiguous data writer')
 cfg=json.dumps({'endpoint':endpoint,'camera':camera,'region':region,'name':name or region.upper(),'primaryLabel':primary_label or'本室'},ensure_ascii=True).replace('<','\\u003c')
 block=(MARK+'\n<link rel="stylesheet" href="'+asset_base+'/room.css?v=0.2.1"><script>window.AOS_ROOM_LIVE_OWNERSHIP=true;window.AOS_ROOM_LIVE_CONFIG='+cfg+';</script><script src="'+asset_base+'/room-data.js?v=0.2.1" defer></script><script src="'+asset_base+'/room.js?v=0.2.1" defer></script>\n'+END+'\n').encode()
 result=raw.replace(ANCHOR,ANCHOR+GUARD).replace(LEGACY,GUARD+b'\n  '+LEGACY).replace(b'</head>',block+b'</head>')
 assert result.replace(block,b'').replace(ANCHOR+GUARD,ANCHOR).replace(GUARD+b'\n  '+LEGACY,LEGACY)==raw
 return result
