# BaiPlay development workspace

This repository is the active BaiPlay development source. Make subsequent changes here; the former AOS development host is a migration/rollback source, not a second development authority.

- Preserve `private/`, runtime databases, credentials, generated media and original site deliveries. Never publish them to the public Git repository.
- Tests must use synthetic inventory and must not contact or control field devices.
- Display data must retain freshness, source provenance and uncertainty; do not invent sensor values.
- Television control requires stable UDN verification, an explicit local site configuration and a single active supervisor.
- Do not send clock synchronization or broad high-frequency discovery broadcasts.
- Distinguish local render checks, media transfer evidence and physical-screen acceptance. Keep the former host serving until local cross-network operation is proven.
- Hourly Codex monitoring remains paused until the user explicitly resumes it.
