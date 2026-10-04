# BaiPlay macOS operations

BaiPlay uses six system LaunchDaemons, each running as the project owner's ordinary account: `manager`, `media`, `feed`, `acoustics`, `stream`, and `supervisor`. `RunAtLoad` and `KeepAlive` provide automatic process startup and restart. The supervisor is installed separately, after confirming media delivery and stopping the former controller.

## Verify without sending commands to televisions

```sh
launchctl print system/com.baiyin.baiplay.manager
launchctl print system/com.baiyin.baiplay.media
launchctl print system/com.baiyin.baiplay.feed
launchctl print system/com.baiyin.baiplay.acoustics
launchctl print system/com.baiyin.baiplay.stream
launchctl print system/com.baiyin.baiplay.supervisor
```

Inspect `runtime/four-screen-evidence/*.json`: each role should have a recently updated `checkedAt`, a progressing source frame, and explicit data freshness. Missing or expired upstream data is a distinct condition from a failed display renderer.

Inspect `runtime/logs/media-access.jsonl`: each configured television should request its assigned playlist and new `.ts` segments. Count HTTP errors and compare request timestamps across successive observations. Range requests may legitimately return HTTP 206. A single HTTP 200 does not establish sustained delivery.

Read `runtime/huawei-*-status.json` for observed UDN, address and current media URI. Compare with the private site configuration. `TRANSITIONING` can accompany ongoing Huawei HLS delivery; it is not proof that the physical screen is displaying the intended pixels.

Do not use a multihomed relay's forced-source HTTP test as the sole cutover gate. It may follow a different network path from the television. Verify actual television requests before changing routes or declaring a firewall failure.

## Controlled failover and rollback

Only one television supervisor may be active. Stop the old supervisor before enabling the new one; retain the old media service until all four televisions request the new host's media. Once the new host is verified, stop and disable the old BaiPlay services and remove its desktop auto-start entry. Keep an independent full archive, consistent database backup, prior service enablement list and an explicit restore procedure.

On Linux, `systemctl disable` may remove an externally linked unit definition as well as its enablement links. Preserve both the original link and its target content before disabling. Do not infer service state from a failed combined `disable --now` call: inspect and verify every unit separately.

A rollback must first stop the new supervisor. Then restore the former host's unit definitions, auto-start entry and originally enabled services. Never start both controllers to see which one wins.

## Startup limitations

- The Mac must remain powered and connected to the site network. `caffeinate -i` prevents idle system sleep while services run; it does not prevent manual sleep, shutdown or power loss.
- FileVault can require an operator to unlock the disk after a cold start. Keep this separate from service startup verification.
- Reserve the media server's IP through the site's normal network administration process, or update the private configuration when it changes. A DHCP lease alone is not a permanent address guarantee.
- Test a physical television power cycle on site before claiming boot recovery has been accepted. A process restart or simulated unit test is not equivalent.
- An optional Codex inspection schedule is separate from these OS services. Preserve the user's paused state; installing playback services does not authorize resuming scheduled inspections.

Credentials, addresses, identities, access logs and acceptance evidence stay in private local files, outside this public repository.
