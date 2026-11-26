"""Shared helpers for the J.A.R.V.I.S. data robot. Standard library only."""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = "jarvis-data/1.0 (personal, non-commercial; github.com/ViktorHal/jarvis-data)"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(url, retries=3, timeout=40, pause=4):
    """GET with retries. Returns bytes; raises the last error after `retries` attempts."""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403, 404):
                break                      # not worth retrying
            time.sleep(pause * (i + 1))
    raise last


def get_json(url, **kw):
    return json.loads(get(url, **kw).decode("utf-8"))


def get_text(url, **kw):
    raw = get(url, **kw)
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def write_json(rel, obj):
    path = os.path.join(DATA, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)          # never leave a half-written file behind


def write_text(rel, text):
    path = os.path.join(DATA, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


class Log:
    """Per-source outcome for data/manifest.json: ok / failed, rows, time, error."""

    def __init__(self):
        self.sources = {}

    def ok(self, name, url, rows, note=None):
        self.sources[name] = {"status": "ok", "url": url, "rows": rows, "at": now_iso(), "note": note}

    def fail(self, name, url, err):
        self.sources[name] = {"status": "failed", "url": url, "rows": 0, "at": now_iso(), "error": str(err)[:300]}
