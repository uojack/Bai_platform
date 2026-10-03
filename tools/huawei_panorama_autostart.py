#!/usr/bin/env python3
"""Auto-start the panorama experience once per Huawei TV online cycle.

The supervisor deliberately does not enforce the BaiPlayer source after a user
switches away with the remote. It only deploys after an offline→online cycle,
or replays when the managed video genuinely reaches its end. Device identity is
anchored to MAC + DLNA UDN, so DHCP address changes do not swap screen roles.
"""

from __future__ import annotations

import os
import logging
import json
import concurrent.futures
import urllib.request
import socket
import sys
import time
from urllib.parse import urlparse
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.huawei_dlna import HuaweiDLNAAdapter


MEDIA_BASE = os.environ.get("BAIPLAYER_MEDIA_BASE", "http://127.0.0.1:4341/media")
POLL_SECONDS = 8
BOOT_GRACE_SECONDS = 18
OFFLINE_RESET_SECONDS = 20
MANAGED_RECOVERY_SECONDS = 12
ARP_TABLE = Path("/proc/net/arp")
DISCOVERY_SECONDS = 300
DISCOVERY_PREFIX = os.environ.get("BAIPLAYER_DISCOVERY_PREFIX", "192.0.2")


@dataclass
class Display:
    address: str
    mac_address: str
    udn: str
    role: str
    filename: str
    title: str
    online: bool = False
    offline_since: float | None = None
    deploy_due: float | None = None
    managed: bool = False
    deploy_attempted: bool = False
    recovery_due: float | None = None
    retry_count: int = 0
    last_position: int = 0
    last_duration: int = 0
    last_playing_at: float | None = None
    eof_confirmed: bool = False

    @property
    def url(self) -> str:
        return f"{MEDIA_BASE}/{self.filename}"


CONFIG = Path(os.environ.get('BAIPLAYER_DISPLAYS',str(Path(__file__).resolve().parents[1]/'private/displays.json')))
DISPLAYS = [Display(**row) for row in json.loads(CONFIG.read_text())] if CONFIG.exists() else []


def address_for_mac(mac_address: str, arp_table: Path = ARP_TABLE) -> str | None:
    """Return the current complete ARP entry for a remembered display MAC."""
    wanted = mac_address.lower()
    try:
        lines = arp_table.read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        columns = line.split()
        if len(columns) >= 6 and columns[3].lower() == wanted:
            try:
                flags = int(columns[2], 16)
            except ValueError:
                continue
            if flags & 0x2:
                return columns[0]
    return None


def refresh_address(adapter: HuaweiDLNAAdapter, display: Display) -> None:
    discovered = address_for_mac(display.mac_address)
    if not discovered or discovered == display.address:
        return
    identity = adapter.describe(discovered)
    if identity.get("UDN") != display.udn:
        logging.warning("ignored MAC candidate %s for %s: UDN mismatch", discovered, display.role)
        return
    previous = display.address
    display.address = discovered
    display.online = False
    display.offline_since = None
    display.deploy_due = None
    display.managed = False
    display.deploy_attempted = False
    display.recovery_due = None
    logging.info("address changed for %s: %s -> %s (MAC %s)", display.role, previous, discovered, display.mac_address)


def discover_huawei(adapter: HuaweiDLNAAdapter, timeout: float = 0.7) -> dict[str, str]:
    """Bounded unicast discovery; never send SSDP or clock broadcasts."""
    probe = HuaweiDLNAAdapter(timeout=timeout)
    wanted = {display.udn for display in DISPLAYS}
    def check(address: str):
        try:
            identity = probe.describe(address)
            if identity.get("UDN") in wanted:
                return identity["UDN"], address
        except Exception:
            pass
        return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        return dict(item for item in pool.map(check, (f"{DISCOVERY_PREFIX}.{n}" for n in range(2, 255))) if item)


def seconds(value: str) -> int:
    try:
        hours, minutes, secs = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + int(float(secs))
    except (AttributeError, TypeError, ValueError):
        return 0


def deploy(adapter: HuaweiDLNAAdapter, display: Display) -> bool:
    try:
        result = adapter.push(display.address, display.url, display.title, mime_type="video/m3u8") if display.filename.endswith(".m3u8") else adapter.push(display.address, display.url, display.title)
        state = result.get("transport", {}).get("CurrentTransportState", "")
        display.eof_confirmed = False
        display.last_playing_at = None
        display.last_position = 0
        display.last_duration = 0
        display.managed = state in {"PLAYING", "TRANSITIONING", "PAUSED_PLAYBACK"}
        logging.info("deployed %s (%s): %s", display.address, display.role, state)
        return display.managed
    except Exception as exc:
        logging.warning("deployment deferred for %s: %s", display.address, exc)
        return False


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    adapter = HuaweiDLNAAdapter(timeout=8)
    next_discovery = time.monotonic() + 60
    if not DISPLAYS:
        raise RuntimeError("No verified television configuration; refusing to deploy")
    while True:
        now = time.monotonic()
        discovered: dict[str, str] = {}
        if now >= next_discovery and any(not display.online for display in DISPLAYS):
            try:
                discovered = discover_huawei(adapter)
            except Exception as exc:
                logging.debug("unicast discovery deferred: %s", exc)
            next_discovery = now + DISCOVERY_SECONDS
        for display in DISPLAYS:
            current = discovered.get(display.udn)
            if current and current != display.address:
                previous = display.address
                display.address = current
                display.online = False
                display.offline_since = None
                display.deploy_due = None
                display.managed = False
                display.deploy_attempted = False
                display.recovery_due = None
                logging.info("verified address changed for %s: %s -> %s", display.role, previous, current)
            try:
                refresh_address(adapter, display)
            except Exception as exc:
                logging.debug("MAC resolution deferred for %s: %s", display.role, exc)
            try:
                identity = adapter.describe(display.address)
                if identity.get("UDN") != display.udn:
                    raise RuntimeError(f"UDN mismatch at {display.address}")
                if not display.online:
                    display.online = True
                    display.offline_since = None
                    display.deploy_due = now + BOOT_GRACE_SECONDS
                    display.deploy_attempted = False
                    display.retry_count = 0
                    display.recovery_due = None
                    try:
                        existing_transport = adapter.transport_info(display.address)
                        existing_position = adapter.position_info(display.address)
                        if existing_transport.get("CurrentTransportState") in {"PLAYING", "TRANSITIONING", "PAUSED_PLAYBACK"} and existing_position.get("TrackURI") == display.url:
                            display.managed = True
                            display.deploy_due = None
                            display.deploy_attempted = True
                            logging.info("existing playback adopted without restart: %s", display.address)
                    except Exception:
                        pass
                    logging.info("online: %s; deployment due=%s", display.address, display.deploy_due)
                else:
                    # A Huawei TV may keep its network stack down for less than
                    # OFFLINE_RESET_SECONDS during a warm reboot. Any observed
                    # describe failure followed by a verified UDN reconnect is
                    # therefore treated as a boot cycle and deployed once.
                    if display.offline_since is not None:
                        gap = now - display.offline_since
                        display.offline_since = None
                        display.deploy_due = now + BOOT_GRACE_SECONDS
                        display.deploy_attempted = False
                        display.managed = False
                        logging.info("reconnected after %.1fs: %s; deployment scheduled", gap, display.address)
            except Exception:
                if display.online:
                    if display.offline_since is None:
                        display.offline_since = now
                    elif now - display.offline_since >= OFFLINE_RESET_SECONDS:
                        display.online = False
                        display.deploy_due = None
                        display.managed = False
                        display.deploy_attempted = False
                        display.recovery_due = None
                        logging.info("offline: %s", display.address)
                continue

            if display.deploy_due is not None and now >= display.deploy_due and not display.deploy_attempted:
                display.deploy_attempted = True
                if not deploy(adapter, display):
                    display.recovery_due = now + 30
                display.deploy_due = None
                continue

            if not display.managed:
                if display.recovery_due is not None and now >= display.recovery_due and display.retry_count < 3:
                    display.retry_count += 1
                    display.recovery_due = None if deploy(adapter, display) else now + 60
                continue
            try:
                transport = adapter.transport_info(display.address)
            except Exception as exc:
                logging.debug("transport unavailable for %s: %s", display.address, exc)
                continue
            try:
                position = adapter.position_info(display.address)
            except Exception as exc:
                logging.debug("position unavailable for %s: %s", display.address, exc)
                position = {}
            status_path = Path(__file__).resolve().parents[1] / "runtime" / f"huawei-{display.role}-status.json"
            status = {"observedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "ip": display.address, "mac": display.mac_address, "udn": display.udn, "role": display.role, "transport": transport, "position": position}
            tmp = status_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2))
            tmp.replace(status_path)
            uri = position.get("TrackURI", "")
            state = transport.get("CurrentTransportState", "")
            if uri and display.filename not in uri:
                display.managed = False
                display.recovery_due = None
                logging.info("remote/source override respected on %s", display.address)
                continue
            duration = seconds(position.get("TrackDuration", ""))
            elapsed = seconds(position.get("RelTime", ""))
            observed = time.monotonic()
            if state in {"PLAYING", "TRANSITIONING"} and uri == display.url:
                # Some Huawei firmware retains TRANSITIONING while decoded
                # position advances. Two progressing samples provide evidence;
                # a frozen loading counter must not refresh EOF confidence.
                advancing = display.last_duration > 0 and elapsed > display.last_position
                if state == "PLAYING" or advancing:
                    display.last_playing_at = observed
                display.last_position = elapsed
                if duration > 0: display.last_duration = duration
                display.eof_confirmed = False
            if state == "PAUSED_PLAYBACK":
                display.recovery_due = None
                display.eof_confirmed = False
                continue
            if state in {"STOPPED", "NO_MEDIA_PRESENT"}:
                # Huawei resets duration and position to zero at EOF. Evidence
                # must come from a recent, verified PLAYING sample near the end.
                gap = observed - display.last_playing_at if display.last_playing_at is not None else float("inf")
                reset_at_end = (0 <= gap <= 30 and display.last_duration > 0
                    and display.last_position >= display.last_duration - 20
                    and display.last_position + gap >= display.last_duration - 3)
                explicit_end = uri == display.url and duration > 0 and elapsed >= duration - 3
                if not (display.eof_confirmed or reset_at_end or explicit_end):
                    display.managed = False
                    display.recovery_due = None
                    logging.info("manual stop/source switch respected on %s", display.address)
                    continue
                display.eof_confirmed = True
                if display.recovery_due is None:
                    display.recovery_due = observed + MANAGED_RECOVERY_SECONDS
                    logging.info("verified EOF on %s; same-program replay scheduled", display.address)
                elif observed >= display.recovery_due:
                    display.recovery_due = None if deploy(adapter, display) else observed + 30
                continue
            display.recovery_due = None
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
