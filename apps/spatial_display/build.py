"""Inject one removable supplement; preserve every original source byte."""
import argparse,hashlib,json
from pathlib import Path
MARK='<!-- AOS-SPATIAL-SUPPLEMENT-V1 -->'
END='<!-- /AOS-SPATIAL-SUPPLEMENT-V1 -->'
def build(raw,endpoint,asset_base):
 if MARK.encode()in raw or raw.count(b'</body>')!=1:raise ValueError('Ambiguous original; refusing to replace existing content')
 config=json.dumps({'endpoint':endpoint},ensure_ascii=True).replace('<','\\u003c')
 block=(MARK+'\n<link rel="stylesheet" href="'+asset_base+'/live.css?v=0.1.0"><script>window.AOS_SCREEN_SUPPLEMENT='+config+';</script><script src="'+asset_base+'/live.js?v=0.1.0" defer></script>\n'+END+'\n').encode()
 result=raw.replace(b'</body>',block+b'</body>')
 assert result.replace(block,b'')==raw
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('output',type=Path);p.add_argument('--base',required=True);a=p.parse_args()
 raw=a.source.read_bytes();result=build(raw,a.base+'/api/elevator',a.base+'/elevator');a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(result)
 print(json.dumps({'originalSha256':hashlib.sha256(raw).hexdigest(),'resultSha256':hashlib.sha256(result).hexdigest(),'originalBytesPreserved':True}))
