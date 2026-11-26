"""Hourly odds-only run: Pinnacle via pinnapi (two requests per run, hard daily cap in pinnacle.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pinnacle  # noqa: E402
from common import Log, now_iso, write_json  # noqa: E402


def main():
    log = Log()
    try:
        pinnacle.run(log)
    except Exception as e:
        log.fail("pinnacle (module)", "", e)
    write_json("pinnacle/manifest.json", {"runAt": now_iso(), "sources": log.sources})
    for n, s in sorted(log.sources.items()):
        print(f"  {s['status']:6} {n} {s.get('rows', '')} {s.get('note') or s.get('error') or ''}")


if __name__ == "__main__":
    main()
