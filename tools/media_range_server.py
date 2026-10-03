#!/usr/bin/env python3
"""Serve BaiPlayer media with HTTP byte-range support for smart TVs."""

from __future__ import annotations

import socket
import struct
import sys
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from urllib.parse import urlsplit
import mimetypes
import os
import re
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class LANHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        interface=os.environ.get('BAIPLAYER_NETWORK_INTERFACE','')
        if sys.platform=='darwin' and interface:
            self.socket.setsockopt(socket.IPPROTO_IP,25,struct.pack('I',socket.if_nametoindex(interface)))
        super().server_bind()

class RangeRequestHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    access_logger = None

    def log_request(self, code='-', size='-'):
        if self.access_logger:
            self.access_logger.info(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'ip':self.client_address[0],'path':urlsplit(self.path).path,'status':code},ensure_ascii=False))
        else:
            super().log_request(code,size)


    def send_head(self):
        path = Path(self.translate_path(self.path))
        if path.is_dir():
            return super().send_head()
        try:
            file = path.open("rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None

        size = os.fstat(file.fileno()).st_size
        start, end = 0, size - 1
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match:
                file.close()
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return None
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else end
            elif match.group(2):
                start = max(0, size - int(match.group(2)))
            if start >= size or end < start:
                file.close()
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return None
            end = min(end, size - 1)

        self.send_response(HTTPStatus.PARTIAL_CONTENT if range_header else HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "public, max-age=3600" if self.path.startswith("/media/") and path.suffix.lower() != ".m3u8" else "no-store")
        self.end_headers()
        file.seek(start)
        self._range_remaining = end - start + 1
        return file

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_range_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining:
            chunk = source.read(min(256 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--access-log",type=Path)
    parser.add_argument("--port", type=int, default=4341)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if args.access_log:
        args.access_log.parent.mkdir(parents=True,exist_ok=True)
        logger=logging.getLogger('media-access');logger.setLevel(logging.INFO)
        logger.addHandler(RotatingFileHandler(args.access_log,maxBytes=10_000_000,backupCount=3))
        RangeRequestHandler.access_logger=logger
    handler = lambda *a, **kw: RangeRequestHandler(*a, directory=str(args.directory.resolve()), **kw)
    LANHTTPServer((args.bind, args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
