#!/usr/bin/env python3
"""Run one configured BaiPlay component on macOS. No credentials in command lines."""
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
config=json.loads((ROOT/'private/site.json').read_text())
component=sys.argv[1]
runtime=ROOT/'runtime'
for name in ['logs','acoustics','four-screen-evidence']:(runtime/name).mkdir(parents=True,exist_ok=True)
app=ROOT/'apps/four-screen'
env=dict(os.environ)
env.update({
 'PYTHONUNBUFFERED':'1',
 'BAIPLAYER_NETWORK_INTERFACE':config['interface'],
 'BAIPLAYER_TV_SUBNET':config['tvSubnet'],
 'BAIPLAYER_DISCOVERY_PREFIX':config['tvSubnet'].rsplit('.',1)[0],
 'BAIPLAYER_DISPLAYS':str(ROOT/'private/displays.json'),
 'BAIPLAYER_INVENTORY':str(ROOT/'private/inventory.local.json'),
 'BAIPLAYER_MEDIA_BASE':f"http://{config['localAddress']}:{config['mediaPort']}/media",
 'BAIPLAYER_VISION_URL':config['visionUrl'],
 'BAIPLAYER_NETWORK_URL':config['networkUrl'],
 'BAIPLAYER_ACOUSTICS_URL':config['acousticsUrl'],
 'BAIPLAYER_ACOUSTICS_FILE':str(runtime/'acoustics/live.json'),
 'PULSE_FEED_PORT':str(config['feedPort']),
 'PULSE_MEDIA_ROOT':str(ROOT/'web/media/pulse-v14'),
 'PULSE_EVIDENCE_ROOT':str(runtime/'four-screen-evidence'),
 'PLAYWRIGHT_MODULE':config['playwright'],
 'CHROME_EXECUTABLE':config['chrome'],
 'FFMPEG_EXECUTABLE':config['ffmpeg'],
 'PATH':str(Path(config['node']).parent)+':'+str(Path(config['python']).parent)+':/usr/bin:/bin:/usr/sbin:/sbin',
 'NO_PROXY':'127.0.0.1,localhost,172.16.0.0/16,192.168.0.0/16',
})
commands={
 'manager':[config['python'],str(ROOT/'server.py'),'--bind','127.0.0.1','--port',str(config['managementPort']),'--aos',config.get('aosReadOnlyUrl','')],
 'media':[config['python'],str(ROOT/'tools/media_range_server.py'),'--bind',config['localAddress'],'--port',str(config['mediaPort']),'--directory',str(ROOT/'web'),'--access-log',str(runtime/'logs/media-access.jsonl')],
 'feed':[config['python'],str(app/'tools/feed_server.py')],
 'acoustics':[config['node'],str(ROOT/'adapters/acoustics/collector.mjs'),'--output',str(runtime/'acoustics/live.json')],
 'stream':[config['node'],str(app/'tools/stream.cjs')],
 'supervisor':[config['python'],str(ROOT/'tools/huawei_panorama_autostart.py')],
}
if component not in commands:raise SystemExit('Unknown component')
os.chdir(ROOT)
os.execvpe(commands[component][0],commands[component],env)
