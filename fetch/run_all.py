"""Run every fetcher and write data/manifest.json with the outcome of each source.

A failing source is recorded as "failed" and never stops the others; files from the last
successful fetch stay in place (each file carries its own fetchedAt), so Jarvis can see how old they are.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import elo  # noqa: E402
import football  # noqa: E402
import nhl  # noqa: E402
from common import Log, now_iso, write_json  # noqa: E402


def main():
    log = Log()
    for name, mod in (("football", football), ("nhl", nhl), ("elo", elo)):
        try:
            mod.run(log)
        except Exception as e:  # a bug in one module must not lose the others' manifest entries
            log.fail(f"{name} (module)", "", e)
    ok = sum(1 for s in log.sources.values() if s["status"] == "ok")
    write_json("manifest.json", {"runAt": now_iso(), "ok": ok, "failed": len(log.sources) - ok, "sources": log.sources})
    print(f"{ok}/{len(log.sources)} sources ok")
    for n, s in sorted(log.sources.items()):
        print(f"  {s['status']:6} {n} {s.get('rows', '')} {s.get('error', '')}")


if __name__ == "__main__":
    main()
