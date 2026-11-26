# Jarvis pipeline code

`decision.py` is the reviewed and tested decision pipeline. The Jarvis scans use this file only when its
sha256 (first 16 hex characters) equals `brain/decision.sha16` in the Jarvis database, so a change here is never
run until the owner's database names its hash. `fetch/hockey12.py` is pinned the same way by `brain/hockey12.sha16`.
