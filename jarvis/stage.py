"""When a pick becomes a final call (owner, 26 Nov 2026): a pick found early is PRELIMINARY; it becomes FINAL ("play
now") in the last scheduled scan before its kick-off, when most news is known. Player picks additionally wait for
the official line-up (the hourly kick-off check confirms the player starts).

usage: python3 jarvis/stage.py <start ISO UTC> [<now ISO UTC>]  -> {"finalAt": ISO UTC, "isFinalNow": bool}
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

STO = ZoneInfo("Europe/Stockholm")
SCANS = ((7, 44), (11, 44), (13, 44), (17, 44), (20, 10))     # Stockholm time, the scheduled scans
MIN_BEFORE = timedelta(minutes=30)                            # decision.py never posts inside 30 minutes


def _dt(s):
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def final_call(start):
    """The last scan at least 30 minutes before kick-off (looking back up to two days)."""
    st = _dt(start)
    local = st.astimezone(STO)
    best = None
    for back in range(0, 3):
        day = (local - timedelta(days=back)).date()
        for h, m in SCANS:
            s = datetime(day.year, day.month, day.day, h, m, tzinfo=STO)
            if s + MIN_BEFORE <= st and (best is None or s > best):
                best = s
        if best:
            break
    return best.astimezone(timezone.utc)


def stage_for(start, now):
    """'final' from the pick's final-call scan on (5 minutes' grace), else 'preliminary'."""
    fc = final_call(start)
    n = _dt(now)
    return ("final" if n >= fc - timedelta(minutes=5) else "preliminary"), fc


if __name__ == "__main__":
    st = sys.argv[1]
    now = sys.argv[2] if len(sys.argv) > 2 else datetime.now(timezone.utc).isoformat()
    s, fc = stage_for(st, now)
    print(json.dumps({"finalAt": fc.strftime("%Y-%m-%dT%H:%M:%SZ"), "stage": s}))
