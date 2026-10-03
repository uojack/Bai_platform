#!/usr/bin/env python3
"""Push one media URL to the verified Huawei Vision display set."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.huawei_dlna import HuaweiDLNAAdapter


from tools.huawei_panorama_autostart import DISPLAYS
VERIFIED_DISPLAYS = tuple(display.address for display in DISPLAYS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media_url")
    parser.add_argument("--title", default="BaiPlayer 华为电视连接测试")
    parser.add_argument("--timeout", type=float, default=4.0, help="DLNA 控制请求超时秒数")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--status", action="store_true", help="只读取播放状态，不更改电视")
    parser.add_argument("--address", action="append", choices=VERIFIED_DISPLAYS, help="只操作指定电视，可重复使用")
    args = parser.parse_args()
    if not VERIFIED_DISPLAYS: parser.error("No verified private display configuration")
    adapter = HuaweiDLNAAdapter(timeout=args.timeout)
    results = []
    for address in args.address or VERIFIED_DISPLAYS:
        try:
            if args.status:
                result = {"ok": True, "device": adapter.describe(address), "transport": adapter.transport_info(address), "position": adapter.position_info(address)}
            else:
                result = adapter.stop(address) if args.stop else adapter.push(address, args.media_url, args.title)
            results.append({"address": address, **result})
        except Exception as exc:
            results.append({"address": address, "ok": False, "error": str(exc)})
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(item.get("ok") for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
