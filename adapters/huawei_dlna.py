"""Minimal, allow-listed UPnP/DLNA adapter for Huawei Vision televisions."""

from __future__ import annotations

import os
import html
import ipaddress
import mimetypes
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any
from adapters.network import direct_opener


AV_TRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERING_CONTROL = "urn:schemas-upnp-org:service:RenderingControl:1"


class HuaweiDLNAAdapter:
    def __init__(self, subnet: str | None = None, port: int = 25826, timeout: float = 4.0) -> None:
        self.subnet = ipaddress.ip_network(subnet or os.environ.get("BAIPLAYER_TV_SUBNET","192.0.2.0/24"), strict=False)
        self.port = port
        self.timeout = timeout

    def _host(self, address: str) -> str:
        host = str(ipaddress.ip_address(address))
        if ipaddress.ip_address(host) not in self.subnet:
            raise ValueError(f"device address {host} is outside the allowed display subnet")
        return host

    def _request(self, request: urllib.request.Request) -> bytes:
        with direct_opener().open(request, timeout=self.timeout) as response:
            return response.read(1024 * 1024)

    def describe(self, address: str) -> dict[str, str]:
        host = self._host(address)
        data = self._request(urllib.request.Request(
            f"http://{host}:{self.port}/description.xml",
            headers={"Accept": "text/xml", "User-Agent": "BaiPlayer/1.4"},
        ))
        root = ET.fromstring(data)
        values: dict[str, str] = {}
        for key in ("friendlyName", "manufacturer", "modelName", "UDN"):
            node = root.find(f".//{{*}}{key}")
            values[key] = node.text.strip() if node is not None and node.text else ""
        if values["manufacturer"] != "HuaweiVision" or "MediaRenderer" not in values["modelName"]:
            raise RuntimeError(f"{host} is not a verified HuaweiVision MediaRenderer")
        return values

    def _soap(self, address: str, service: str, path: str, action: str, arguments: dict[str, Any]) -> dict[str, str]:
        host = self._host(address)
        body_args = "".join(f"<{key}>{html.escape(str(value))}</{key}>" for key, value in arguments.items())
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f'<s:Body><u:{action} xmlns:u="{service}">{body_args}</u:{action}></s:Body></s:Envelope>'
        ).encode("utf-8")
        request = urllib.request.Request(
            f"http://{host}:{self.port}{path}", data=body, method="POST",
            headers={"Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{service}#{action}"', "User-Agent": "BaiPlayer/1.4"},
        )
        response = self._request(request)
        if not response:
            return {}
        root = ET.fromstring(response)
        result = root.find(f".//{{{service}}}{action}Response")
        return {node.tag.rsplit("}", 1)[-1]: node.text or "" for node in list(result or [])}

    def transport_info(self, address: str) -> dict[str, str]:
        return self._soap(address, AV_TRANSPORT, "/upnp/service/AVTransport/Control", "GetTransportInfo", {"InstanceID": 0})

    def position_info(self, address: str) -> dict[str, str]:
        return self._soap(address, AV_TRANSPORT, "/upnp/service/AVTransport/Control", "GetPositionInfo", {"InstanceID": 0})

    def push(self, address: str, media_url: str, title: str, mime_type: str | None = None) -> dict[str, Any]:
        identity = self.describe(address)
        mime_type = mime_type or mimetypes.guess_type(media_url.split("?", 1)[0])[0] or "application/octet-stream"
        if mime_type.startswith("video/"):
            upnp_class = "object.item.videoItem"
        elif mime_type.startswith("audio/"):
            upnp_class = "object.item.audioItem"
        else:
            upnp_class = "object.item.imageItem.photo"
        metadata = (
            '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            f'<item id="0" parentID="-1" restricted="1"><dc:title>{html.escape(title)}</dc:title>'
            f'<upnp:class>{upnp_class}</upnp:class>'
            f'<res protocolInfo="http-get:*:{mime_type}:*">{html.escape(media_url)}</res></item></DIDL-Lite>'
        )
        set_uri_timed_out = False
        try:
            self._soap(address, AV_TRANSPORT, "/upnp/service/AVTransport/Control", "SetAVTransportURI", {"InstanceID": 0, "CurrentURI": media_url, "CurrentURIMetaData": metadata})
        except TimeoutError:
            # HuaweiVision fetches and displays the image but some firmware does
            # not close the SetAVTransportURI HTTP response. Verify by state.
            set_uri_timed_out = True
        except urllib.error.URLError as exc:
            if not isinstance(exc.reason, TimeoutError):
                raise
            set_uri_timed_out = True
        if not set_uri_timed_out:
            if mime_type.startswith("video/"):
                try:
                    self._soap(address, AV_TRANSPORT, "/upnp/service/AVTransport/Control", "SetPlayMode", {"InstanceID": 0, "NewPlayMode": "REPEAT_ALL"})
                except Exception:
                    pass
            self._soap(address, AV_TRANSPORT, "/upnp/service/AVTransport/Control", "Play", {"InstanceID": 0, "Speed": 1})
        transport = self.transport_info(address)
        if set_uri_timed_out and transport.get("CurrentTransportState") not in {"PLAYING", "PAUSED_PLAYBACK", "TRANSITIONING"}:
            # Some HuaweiVision firmware accepts the URI only when DIDL metadata
            # is omitted. Retry that narrow compatibility form before failing.
            try:
                self._soap(address, AV_TRANSPORT, "/upnp/service/AVTransport/Control", "SetAVTransportURI", {"InstanceID": 0, "CurrentURI": media_url, "CurrentURIMetaData": ""})
            except (TimeoutError, urllib.error.URLError):
                pass
            try:
                self._soap(address, AV_TRANSPORT, "/upnp/service/AVTransport/Control", "Play", {"InstanceID": 0, "Speed": 1})
            except (TimeoutError, urllib.error.URLError):
                pass
            transport = self.transport_info(address)
            if transport.get("CurrentTransportState") not in {"PLAYING", "PAUSED_PLAYBACK", "TRANSITIONING"}:
                raise TimeoutError("HuaweiVision did not acknowledge the media URI")
        try:
            position = self.position_info(address)
        except Exception:
            position = {}
        return {"ok": True, "device": identity, "transport": transport, "position": position, "acceptedAfterTimeout": set_uri_timed_out}

    def stop(self, address: str) -> dict[str, Any]:
        identity = self.describe(address)
        self._soap(address, AV_TRANSPORT, "/upnp/service/AVTransport/Control", "Stop", {"InstanceID": 0})
        return {"ok": True, "device": identity, "transport": self.transport_info(address)}

    def set_volume(self, address: str, volume: int) -> dict[str, Any]:
        identity = self.describe(address)
        volume = max(0, min(100, int(volume)))
        self._soap(address, RENDERING_CONTROL, "/upnp/service/RenderingControl/Control", "SetVolume", {"InstanceID": 0, "Channel": "Master", "DesiredVolume": volume})
        return {"ok": True, "device": identity, "volume": volume}
