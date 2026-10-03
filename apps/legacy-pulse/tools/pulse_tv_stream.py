#!/usr/bin/env python3
"""Render validated Pulse readings as a silent, continuous HLS TV program.

This process has no TV control capability. The existing supervisor alone owns
playback. Readings keep source times and expire; it never renders demo values.
"""
import argparse
import math
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pulse import Pulse
from PIL import Image, ImageDraw, ImageFont

BACKGROUND='#11100e';FG='#e9e5dd';MUTED='#b4aa99';ACCENT='#bf9f74'
def frame(data,font_path,now):
    image=Image.new('RGB',(1920,1080),BACKGROUND);draw=ImageDraw.Draw(image)
    def text(x,y,value,size=26,color=FG):
        draw.text((x,y),str(value),font=ImageFont.truetype(font_path,size),fill=color)
    def date(value):return time.strftime('%H:%M:%S',time.localtime(value)) if isinstance(value,(float,int)) else '—'
    def card(x,y,w,h,title):
        draw.rounded_rectangle((x,y,x+w,y+h),12,fill='#1c1914',outline='#514735',width=2);text(x+28,y+22,title,27,ACCENT)
    text(64,35,'百音集团研发总部 / '+data.get('region','百灵岛'),24,ACCENT)
    text(60,80,'LAGOOON Pulse',66)
    text(64,176,'附近摄像头 · B&O 音响 · 真实网络状态',27,MUTED)
    text(1440,52,time.strftime('%Y.%m.%d',time.localtime(now)),28,MUTED)
    text(1440,91,time.strftime('%H:%M:%S',time.localtime(now)),54)
    fresh=data.get('fresh',False)
    notice='视觉服务器检修中 · 音响与网络状态独立更新' if data.get('maintenance') else '逐路核验采集时间 · 过期读数自动清空'
    if not fresh:notice='采集服务暂不可用 · 当前读数已清空'
    draw.rectangle((64,234,1856,304),fill='#302719');text(87,251,notice,29)
    cameras=data.get('cameras',[]);speakers=data.get('audioSources',[])
    card(64,336,552,330,'相邻摄像头 · '+str(len(cameras))+' 路关联')
    if cameras:
        c=cameras[int(now//12)%len(cameras)];valid=fresh and c.get('state')=='live';people=c.get('people') if valid else None
        text(92,407,people if people is not None else '—',86);text(94,525,str(c.get('cameraCode',''))+' · 本通道人数',26,MUTED)
        text(94,577,'采集 '+date(c.get('capturedAt')),23,MUTED)
    else:
        text(92,407,'—',86);text(94,529,'摄像头关联待确认',28,MUTED);text(94,577,'未接入时不使用其他区域人数',22,MUTED)
    card(642,336,604,330,'附近音响 · '+str(len(speakers))+' 台关联')
    if speakers:
        s=speakers[int(now//12)%len(speakers)];valid=fresh and s.get('identityVerified') is True and s.get('state') in {'live','partial'}
        text(670,395,(s.get('name') or 'B&O')[:26],26)
        text(670,431,s.get('volume') if valid and s.get('volume') is not None else '—',78)
        text(830,470,'音量档位 / 上限 '+str(s.get('maximum') if valid else '—'),23,MUTED)
        power={'standby':'待机','on':'已开机','networkStandby':'网络待机'}.get(s.get('power'),'电源状态未返回')
        mute='已静音' if s.get('muted') is True else '未静音' if s.get('muted') is False else '静音状态未返回'
        text(670,548,power+' · '+mute if valid else '接口不可用 · 读数已清空',25,MUTED)
        text(670,597,'检查 '+date(s.get('checkedAt'))+' · '+('无活动播放源' if s.get('source')=='' else str(s.get('source') or '播放源未返回'))[:44],21,MUTED)
    else:text(670,446,'—',78);text(670,566,'附近音响待登记',26,MUTED)
    card(1272,336,584,330,'总部管理接口响应')
    network=data.get('network',{});ok=fresh and network.get('ok') is True
    text(1300,423,network.get('latencyMs') if ok else '—',76);text(1304,529,'ms · 实际 HTTP 响应耗时',25,MUTED)
    text(1304,577,'接口在线' if ok else '接口不可用',23,MUTED)
    card(64,698,1182,231,'网络响应记录 · 仅展示实际检查')
    history=data.get('history',[])[-50:];maximum=max([1]+[p['latencyMs'] for p in history if isinstance(p.get('latencyMs'),(int,float)) and math.isfinite(p['latencyMs'])])
    for i,p in enumerate(history):
        value=p.get('latencyMs');height=3 if value is None else max(3,min(92,value/maximum*92));x=94+i*22
        draw.rectangle((x,875-height,x+12,875),fill='#5d4033' if value is None else ACCENT)
    text(94,887,'失败留空；不将延迟解释为带宽、丢包率或网络评分。',19,MUTED)
    card(1272,698,584,231,'来源与有效性')
    text(1300,762,'视觉服务器：由私有配置指定',22,MUTED)
    text(1300,804,'音响：已配置的 Beosound Shape',22,MUTED)
    text(1300,849,'接口检查 '+date(data.get('checkedAt'))+' · 直播有数秒延迟',20,MUTED)
    text(64,962,'音响音量是设备设置值，不是环境噪声分贝，也不证明正在发声。',25,MUTED)
    text(64,1007,'各摄像头独立展示，不累加跨镜头人数。电视直播不包含原始监控音视频。',23,MUTED)
    return image

def run():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--output',required=True);parser.add_argument('--font',required=True);parser.add_argument('--preview')
    args=parser.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    pulse=Pulse(args.config,lambda:{'tv-04':{'name':'百灵岛'}});pulse.start()
    command=['ffmpeg','-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pix_fmt','rgb24','-s','1920x1080','-r','2','-i','pipe:0','-an','-vf','fps=10','-c:v','libx264','-preset','ultrafast','-tune','zerolatency','-threads','2','-pix_fmt','yuv420p','-b:v','1600k','-maxrate','2000k','-bufsize','4000k','-g','20','-keyint_min','20','-sc_threshold','0','-f','hls','-hls_time','2','-hls_list_size','8','-hls_delete_threshold','3','-hls_start_number_source','epoch','-hls_flags','delete_segments+temp_file+omit_endlist','-hls_segment_filename',str(out/'seg_%d.ts'),str(out/'live.m3u8')]
    proc=subprocess.Popen(command,stdin=subprocess.PIPE)
    count=0
    try:
        while proc.poll() is None:
            start=time.monotonic();picture=frame(pulse.snapshot('tv-04'),args.font,time.time())
            if args.preview and count%20==0:picture.save(args.preview)
            proc.stdin.write(picture.tobytes());proc.stdin.flush();count+=1
            time.sleep(max(0,.5-(time.monotonic()-start)))
    finally:
        proc.stdin.close();proc.terminate();proc.wait(timeout=5)
    raise RuntimeError('TV stream encoder stopped')
if __name__=='__main__':run()
