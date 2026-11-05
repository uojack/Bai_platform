#!/usr/bin/env python3
"""Install the local read-only supplementary display feed, never a field controller."""
import argparse,os,plistlib,subprocess
from pathlib import Path
ROOT=Path(os.environ.get('BAIPLAY_ROOT',Path(__file__).resolve().parents[2]))
def main():
 p=argparse.ArgumentParser();p.add_argument('--stop',action='store_true');a=p.parse_args();label='com.baiyin.spatial-display';domain='gui/'+str(os.getuid());target=Path.home()/'Library/LaunchAgents'/(label+'.plist')
 if a.stop:
  subprocess.run(['launchctl','bootout',domain+'/'+label],check=False);return
 if not(ROOT/'private/spatial-display.json').is_file():raise SystemExit('Owner site configuration is required')
 target.parent.mkdir(parents=True,exist_ok=True);(ROOT/'runtime/logs').mkdir(parents=True,exist_ok=True)
 target.write_bytes(plistlib.dumps({'Label':label,'ProgramArguments':[str(ROOT/'.venv/bin/python'),str(ROOT/'apps/spatial_display/server.py')],'WorkingDirectory':str(ROOT),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':5,'StandardOutPath':str(ROOT/'runtime/logs/spatial-display.log'),'StandardErrorPath':str(ROOT/'runtime/logs/spatial-display-error.log')}))
 exists=subprocess.run(['launchctl','print',domain+'/'+label],capture_output=True).returncode==0
 if exists:subprocess.run(['launchctl','kickstart','-k',domain+'/'+label],check=True)
 else:subprocess.run(['launchctl','bootstrap',domain,str(target)],check=True)
 print(label+' installed for current login session')
if __name__=='__main__':main()
