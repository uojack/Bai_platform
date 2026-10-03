#!/usr/bin/env python3
"""Read-only status probe for the four managed Huawei televisions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.huawei_dlna import HuaweiDLNAAdapter


from tools.huawei_panorama_autostart import DISPLAYS as CONFIGURED_DISPLAYS
DISPLAYS = {display.role: display.address for display in CONFIGURED_DISPLAYS}


def main() -> None:
    adapter = HuaweiDLNAAdapter(timeout=5)
    results: list[dict[str, object]] = []
    for role, address in DISPLAYS.items():
        item: dict[str, object] = {"role": role, "address": address, "online": False}
        try:
            item["identity"] = adapter.describe(address)
            item["transport"] = adapter.transport_info(address)
            item["position"] = adapter.position_info(address)
            item["online"] = True
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        results.append(item)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
