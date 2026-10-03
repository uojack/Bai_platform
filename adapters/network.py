"""Direct LAN HTTP transport; no proxy and optional macOS interface binding."""
import http.client
import os
import socket
import struct
import sys
import urllib.request

class LANConnection(http.client.HTTPConnection):
    def connect(self):
        interface=os.environ.get('BAIPLAYER_NETWORK_INTERFACE','')
        if sys.platform=='darwin' and interface:
            sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            try:
                sock.setsockopt(socket.IPPROTO_IP,25,struct.pack('I',socket.if_nametoindex(interface)))
                sock.connect((self.host,self.port))
            except Exception:
                sock.close()
                raise
            self.sock=sock
        else:
            super().connect()

class LANHandler(urllib.request.HTTPHandler):
    def http_open(self,request):
        return self.do_open(LANConnection,request)

def direct_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}),LANHandler())
