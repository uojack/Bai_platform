"""Bind an existing six-slot AOS room page without changing its visual/content baseline."""
import json,hashlib
from pathlib import Path
MARK='<!-- AOS-ROOM-LIVE-V1 -->'
END='<!-- /AOS-ROOM-LIVE-V1 -->'
GUARD=b'\n        if (window.AOS_ROOM_LIVE_OWNERSHIP) return;'
ANCHOR=b'function requestBaiHubState() {'
def build_room(raw,endpoint,asset_base,camera,region):
 if MARK.encode()in raw or raw.count(b'</head>')!=1 or raw.count(ANCHOR)!=1 or b'data-layer="perception"'not in raw:raise ValueError('Original room structure changed')
 cfg=json.dumps({'endpoint':endpoint,'camera':camera,'region':region},ensure_ascii=True).replace('<','\\u003c')
 block=(MARK+'\n<link rel="stylesheet" href="'+asset_base+'/room.css?v=0.2.0"><script>window.AOS_ROOM_LIVE_OWNERSHIP=true;window.AOS_ROOM_LIVE_CONFIG='+cfg+';</script><script src="'+asset_base+'/room-data.js?v=0.2.0" defer></script><script src="'+asset_base+'/room.js?v=0.2.0" defer></script>\n'+END+'\n').encode()
 result=raw.replace(ANCHOR,ANCHOR+GUARD).replace(b'</head>',block+b'</head>')
 assert result.replace(block,b'').replace(ANCHOR+GUARD,ANCHOR)==raw
 return result
