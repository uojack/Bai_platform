#!/usr/bin/env python3
"""Rediscover managed Huawei televisions by stable UDN/MAC and push their assigned media."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.huawei_dlna import HuaweiDLNAAdapter
from tools.huawei_panorama_autostart import DISPLAYS, address_for_mac, discover_huawei


def main() -> int:
    adapter = HuaweiDLNAAdapter(timeout=12)
    discovered: dict[str, str] = {}
    for _ in range(3):
        discovered.update(discover_huawei(adapter, timeout=3.0))
        time.sleep(0.4)

    results: list[dict[str, object]] = []
    for display in DISPLAYS:
        candidates = [
            discovered.get(display.udn),
            address_for_mac(display.mac_address),
            display.address,
        ]
        address = ""
        for candidate in dict.fromkeys(item for item in candidates if item):
            try:
                identity = adapter.describe(candidate)
                if identity.get("UDN") == display.udn:
                    address = candidate
                    break
            except Exception:
                continue
        if not address:
            results.append({"role": display.role, "ok": False, "error": "device not discovered"})
            continue
        try:
            pushed = adapter.push(address, display.url, display.title, mime_type="video/m3u8" if display.filename.endswith(".m3u8") else "video/mp4")
            results.append({
                "role": display.role,
                "address": address,
                "mac": display.mac_address,
                "udn": display.udn,
                "media": display.filename,
                "ok": True,
                "transport": pushed.get("transport", {}),
                "position": pushed.get("position", {}),
            })
        except Exception as exc:
            results.append({"role": display.role, "address": address, "ok": False, "error": str(exc)})
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(item.get("ok") for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
