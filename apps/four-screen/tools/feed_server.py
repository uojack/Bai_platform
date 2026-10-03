#!/usr/bin/env python3
"""Local, read-only preview. Camera images, attributes and transcripts are excluded."""
import json
import os
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE / 'web' if (BASE / 'web').exists() else BASE / 'AOSDisplay/app/src/main/assets/pulse'
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from adapters.network import direct_opener
OPENER = direct_opener()
LOCK = threading.Lock()
CACHE = {}
ACOUSTICS = Path(os.environ.get('BAIPLAYER_ACOUSTICS_FILE', str(BASE.parents[1]/'runtime/acoustics/live.json')))
URLS = {'vision': os.environ.get('BAIPLAYER_VISION_URL','http://127.0.0.1:8080/api/observer/latest'),
        'network': os.environ.get('BAIPLAYER_NETWORK_URL','http://127.0.0.1:18080/v1/health')}

def pick(obj, keys):
    return {key: obj[key] for key in keys if isinstance(obj, dict) and key in obj}

def projection(raw):
    result = pick(raw, ['schema_version', 'event_type', 'source', 'frame', 'metrics', 'algorithms'])
    result['multi_camera_monitor'] = pick(raw.get('multi_camera_monitor'), ['enabled', 'state', 'cameras'])
    mm = raw.get('multimodal_perception') or {}
    result['multimodal_perception'] = pick(mm, ['enabled', 'state'])
    rows = []
    for row in mm.get('results', []):
        one = pick(row, ['channel', 'camera_code', 'location_name', 'timestamp', 'stale'])
        one['domains'] = pick(row.get('domains'), ['spatial_attribute'])
        rows.append(one)
    result['multimodal_perception']['results'] = rows
    for name in ['speech_recognition', 'audio_perception']:
        result[name] = pick(raw.get(name), ['ready', 'state'])
    return result

def poll(kind, period):
    while True:
        try:
            if kind == 'acoustics':
                raw = ACOUSTICS.read_bytes()
                obj = json.loads(raw)
                if obj.get('schema') != 'aos.demo.acoustics.v1':
                    raise ValueError('invalid acoustics schema')
                value = (200, obj)
                with LOCK:
                    CACHE[kind] = value
                time.sleep(period)
                continue
            request = urllib.request.Request(URLS[kind], headers={'Accept': 'application/json', 'User-Agent': 'AOS-Pulse/1.3'})
            with OPENER.open(request, timeout=3) as response:
                raw = response.read(2_097_153)
            if len(raw) > 2_097_152:
                raise ValueError('payload too large')
            obj = json.loads(raw)
            if kind == 'vision':
                obj = projection(obj)
            elif obj.get('status') != 'ok':
                raise ValueError('invalid health response')
            value = (200, obj)
        except Exception as error:
            value = (503, {'error': f'{type(error).__name__}: {str(error)[:100]}'})
        with LOCK:
            CACHE[kind] = value
        time.sleep(period)

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)
    def do_GET(self):
        if self.path.split('?')[0] == '/overview.html':
            body = (Path(__file__).parent / 'overview.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        kind = self.path.split('?')[0].removeprefix('/api/')
        if self.path.startswith('/api/'):
            with LOCK:
                status, obj = CACHE.get(kind, (503, {'error': '等待首次采集'}))
            body = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()
    def log_message(self, *args):
        pass

if __name__ == '__main__':
    for kind, period in [('vision', 2), ('network', 15), ('acoustics', 2)]:
        threading.Thread(target=poll, args=(kind, period), daemon=True).start()
    print('Four screen render feed: http://127.0.0.1:18784/index.html', flush=True)
    ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('PULSE_FEED_PORT','18784'))), Handler).serve_forever()
