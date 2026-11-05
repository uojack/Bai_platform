#!/usr/bin/env python3
"""Install only BaiTV's two login services. Existing six BaiPlay daemons remain."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
def main():
 p=argparse.ArgumentParser();p.add_argument('--hosts',action='store_true');p.add_argument('--stop',action='store_true');args=p.parse_args()
 if args.hosts:
  if os.geteuid()!=0:raise SystemExit('本机短域名需要管理员写入 /etc/hosts；请用 sudo 运行此脚本 --hosts。')
  path=Path('/etc/hosts');raw=path.read_bytes();text=raw.decode()
  matches=[l for l in text.splitlines()if not l.lstrip().startswith('#')and 'baitv'in l.split()[1:]]
  if matches:
   if all(l.split()[0]=='127.0.0.1'for l in matches):print('baitv 已指向本机');return
   raise SystemExit('已有其他 baitv 映射，保留原文件，请人工核对。')
  backup=Path('/etc/hosts.baitv-backup')
  if not backup.exists():backup.write_bytes(raw);os.chmod(backup,0o600)
  fd=os.open(path,os.O_WRONLY|os.O_APPEND)
  try:os.write(fd,b'\n127.0.0.1 baitv # BaiTV local application\n')
  finally:os.close(fd)
  subprocess.run(['/usr/bin/dscacheutil','-flushcache'],check=False)
  print('已加入 http://baitv，仅影响本机；手机请用 http://baitv.local');return
 site=json.loads((ROOT/'private/site.json').read_text());folder=Path.home()/'Library/LaunchAgents';folder.mkdir(exist_ok=True)
 python=str(ROOT/'.venv/bin/python');domain='gui/'+str(os.getuid())
 jobs={'com.baiyin.baitv':[python,str(Path(__file__).with_name('server.py'))],'com.baiyin.baitv.bonjour':['/usr/bin/dns-sd','-P','BaiTV','_http._tcp','local','80','baitv.local',site['localAddress'],'path=/']}
 for label,cmd in jobs.items():
  target=folder/(label+'.plist')
  if args.stop:
   subprocess.run(['launchctl','bootout',domain+'/'+label],capture_output=True);continue
  data={'Label':label,'ProgramArguments':cmd,'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,'WorkingDirectory':str(ROOT),'EnvironmentVariables':{'HOME':str(Path.home()),'PYTHONUNBUFFERED':'1'},'StandardOutPath':str(ROOT/'runtime/logs'/(label+'.log')),'StandardErrorPath':str(ROOT/'runtime/logs'/(label+'.error.log'))}
  if target.exists()and plistlib.loads(target.read_bytes())!=data:raise SystemExit('Existing service differs; preserve and review '+str(target))
  target.write_bytes(plistlib.dumps(data));os.chmod(target,0o600)
  existing=subprocess.run(['launchctl','print',domain+'/'+label],capture_output=True)
  if existing.returncode:subprocess.run(['launchctl','bootstrap',domain,str(target)],check=True)
  print(label,'installed')
if __name__=='__main__':main()
