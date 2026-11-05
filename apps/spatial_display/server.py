#!/usr/bin/env python3
"""Read-only supplement; originals remain private and byte-preserved by the builder."""
import ipaddress,json,mimetypes,os,sys,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit
ROOT=Path(os.environ.get("BAIPLAY_ROOT",Path(__file__).resolve().parents[2]));sys.path.insert(0,str(Path(__file__).parent))
from feed import Feed

def main():
 cfg=json.loads((ROOT/'private/spatial-display.json').read_text());pages=Path(cfg['pages']);web=Path(__file__).parent/'web'
 feed=Feed(cfg['feedBase'],cfg['coreBase'],cfg.get('semanticHost'),cfg.get('semanticServices'),cfg.get('regions'));threading.Thread(target=feed.run,daemon=True).start()
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*a):pass
  def do_GET(self):
   host=urlsplit('//'+self.headers.get('Host','')).hostname
   if not ipaddress.ip_address(self.client_address[0]).is_private or host not in('127.0.0.1','localhost',cfg['address']):return self.reply(403,b'Local network only','text/plain')
   path=urlsplit(self.path).path
   region=path.removeprefix('/api/')
   if path.startswith('/api/')and region in feed.regions:
    with (ROOT/'runtime/logs/spatial-access.jsonl').open('a')as log:log.write(json.dumps({'at':__import__('time').time(),'ip':self.client_address[0],'path':path})+'\n')
    return self.reply(200,json.dumps(feed.snapshot(region),ensure_ascii=False,allow_nan=False).encode(),'application/json')
   files={'/elevator/left.html':pages/'left.html','/elevator/right.html':pages/'right.html','/elevator/live.js':web/'live.js','/elevator/live.css':web/'live.css'}
   files.update({f'/rooms/{key}.html':Path(file)for key,file in cfg.get('roomPages',{}).items()if key in feed.regions})
   files.update({'/rooms/room-data.js':web/'room-data.js','/rooms/room.js':web/'room.js','/rooms/room.css':web/'room.css'})
   if path in files and files[path].is_file():
    p=files[path];return self.reply(200,p.read_bytes(),mimetypes.guess_type(p.name)[0]or'application/octet-stream')
   if path.startswith('/elevator/assets/audio/'):
    name=Path(path).name
    if name in cfg.get('audioFiles',[]):
     p=pages/'assets/audio'/name
     if p.is_file():return self.reply(200,p.read_bytes(),'audio/wav')
   return self.reply(404,b'Not found','text/plain')
  def reply(self,status,body,mime):
   self.send_response(status);self.send_header('Content-Type',mime+'; charset=utf-8'if mime.startswith('text/')or mime=='application/json'else mime);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
   if urlsplit(self.path).path.startswith('/api/'):self.send_header('Access-Control-Allow-Origin','*')
   self.end_headers();self.wfile.write(body)
  def do_POST(self):self.reply(405,b'Read only','text/plain')
 server=ThreadingHTTPServer(('0.0.0.0',cfg.get('port',18787)),Handler)
 try:server.serve_forever()
 finally:feed.stop.set();server.server_close()
if __name__=='__main__':main()
