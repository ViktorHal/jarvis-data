"""Safety net for GitHub's cron: GitHub may delay or skip scheduled runs (it skipped every one on the repo's
first day). This loop runs inside the "clock" workflow and starts fetch.yml / odds.yml / results.yml with workflow_dispatch
whenever a slot has passed without a run of that workflow (scheduled or not). It never fetches anything itself,
so it adds no requests to any data source; the odds cap (80/day) is still enforced inside pinnacle.py.
Before its job time runs out it starts the next clock run, so it keeps going without a schedule.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.github.com/repos/" + os.environ.get("GITHUB_REPOSITORY", "ViktorHal/jarvis-data")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
RUN_FOR = timedelta(hours=5, minutes=40)
FETCH_SLOTS = ((4, 20), (10, 20), (16, 20))        # UTC, as fetch.yml's cron
ODDS_HOURS, ODDS_MIN = range(6, 23), 35             # UTC, as odds.yml's cron
RESULTS_MIN = 5                                     # UTC, every hour, as results.yml's cron
GRACE = timedelta(minutes=10)                       # give GitHub's own schedule 10 minutes first


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode()
        return json.loads(txt) if txt else {}


def last_run(wf):
    runs = call("GET", f"/actions/workflows/{wf}/runs?per_page=1").get("workflow_runs", [])
    return datetime.fromisoformat(runs[0]["created_at"].replace("Z", "+00:00")) if runs else None


def due_slots(now):
    """(workflow, slot time) for the latest slot of each workflow that has passed (plus grace)."""
    out = []
    day = now.replace(second=0, microsecond=0)
    fs = [day.replace(hour=h, minute=m) for h, m in FETCH_SLOTS] + [day.replace(hour=h, minute=m) - timedelta(days=1) for h, m in FETCH_SLOTS]
    fs = [s for s in fs if s + GRACE <= now]
    if fs:
        out.append(("fetch.yml", max(fs)))
    if now.hour in ODDS_HOURS and now.minute >= ODDS_MIN + GRACE.seconds // 60:
        out.append(("odds.yml", now.replace(minute=ODDS_MIN, second=0, microsecond=0)))
    rs = now.replace(minute=RESULTS_MIN, second=0, microsecond=0)
    if rs + GRACE > now:
        rs -= timedelta(hours=1)
    out.append(("results.yml", rs))
    return out


def tick(now, last=last_run, dispatch=None):
    started = []
    for wf, slot in due_slots(now):
        lr = last(wf)
        if (lr is None or lr < slot) and now - slot < timedelta(hours=3):
            (dispatch or (lambda w: call("POST", f"/actions/workflows/{w}/dispatches", {"ref": "main"})))(wf)
            started.append(wf)
    return started


def main():
    t0 = datetime.now(timezone.utc)
    while datetime.now(timezone.utc) - t0 < RUN_FOR:
        try:
            s = tick(datetime.now(timezone.utc))
            if s:
                print(datetime.now(timezone.utc).strftime("%H:%M"), "dispatched", s, flush=True)
        except Exception as e:                      # a failed API call is retried on the next tick
            print("tick failed:", str(e)[:200], flush=True)
        time.sleep(120)
    call("POST", "/actions/workflows/clock.yml/dispatches", {"ref": "main"})
    print("next clock run started", flush=True)


if __name__ == "__main__":
    main()
