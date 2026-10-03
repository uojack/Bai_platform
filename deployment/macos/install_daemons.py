#!/usr/bin/env python3
"""Install system-managed BaiPlay services, running as the project owner (not root)."""
import argparse,json,os,plistlib,pwd,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--supervisor',action='store_true');args=p.parse_args()
if os.geteuid()!=0:raise SystemExit('Administrator installation required: sudo <venv-python> deployment/macos/install_daemons.py')
owner=pwd.getpwuid(ROOT.stat().st_uid)
if owner.pw_uid==0:raise SystemExit('Project must belong to a non-root user')
cfg=json.loads((ROOT/'private/site.json').read_text())
components=['supervisor'] if args.supervisor else ['manager','media','feed','acoustics','stream']
backup=ROOT/'private/disabled-launch-agents';backup.mkdir(exist_ok=True)
for component in components:
 label='com.baiyin.baiplay.'+component
 agent=Path(owner.pw_dir)/'Library/LaunchAgents'/(label+'.plist')
 if agent.exists():
  subprocess.run(['launchctl','bootout',f'gui/{owner.pw_uid}',str(agent)],capture_output=True)
  agent.rename(backup/agent.name)
 target=Path('/Library/LaunchDaemons')/(label+'.plist')
 if target.exists():subprocess.run(['launchctl','bootout','system',str(target)],capture_output=True)
 data={'Label':label,'UserName':owner.pw_name,'GroupName':__import__('grp').getgrgid(owner.pw_gid).gr_name,
       'ProgramArguments':['/usr/bin/caffeinate','-i',cfg['python'],str(ROOT/'deployment/macos/run.py'),component],
       'WorkingDirectory':str(ROOT),'EnvironmentVariables':{'HOME':owner.pw_dir},
       'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':15,
       'StandardOutPath':str(ROOT/'runtime/logs'/f'{component}.log'),
       'StandardErrorPath':str(ROOT/'runtime/logs'/f'{component}-error.log')}
 target.write_bytes(plistlib.dumps(data));os.chown(target,0,0);os.chmod(target,0o644)
 subprocess.run(['launchctl','bootstrap','system',str(target)],check=True)
 print(label+' installed for '+owner.pw_name)
