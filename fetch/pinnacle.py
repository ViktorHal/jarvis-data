"""Pinnacle pre-match odds via pinnapi.com (free trial key, 100 requests/day, no card on file).

HARD DAILY CAP: data/pinnacle/usage.json counts every request per UTC day and the robot never makes request
number CAP + 1 (CAP = 80, below pinnapi's 100). The count is written BEFORE each request, so a crash
mid-request still counts it. The key comes from the PINNAPI_KEY secret and is never printed or stored.

One request per sport returns every pre-match fixture of that sport with its markets. We keep only the
competitions Jarvis covers and store the periods exactly as Pinnacle publishes them:
  soccer  num_0 = full time (90 min + stoppage), num_1 = first half
  hockey  num_0 = the whole game INCLUDING overtime and shootout (Pinnacle's hockey convention): its
          money line is a "Moneyline (incl. OT)" price; its totals/spreads are NOT 60-minute markets.
Writes data/pinnacle/odds.json and data/pinnacle/usage.json.
"""
import json
import os
from datetime import datetime, timezone

from common import DATA, Log, get, write_json, now_iso
import common

CAP = 80
BASE = "https://pinnapi.com/kit/v1/prematch/fixtures?sport_id={sid}"
SPORTS = {"soccer": 1, "hockey": 4}
KEEP = {
    # 28 Nov 2026: the second divisions are kept too. research/where_is_the_edge.json, 125,000 lines with
    # Pinnacle's closing price as the sharp reference and Bet365's as the soft one: where a soft bookmaker pays
    # at least 3% over the sharp fair price, flat stakes returned +9.3% in the second and lower divisions and
    # +7.8% in the smaller leagues, against +1.8% in the big five. The bookmakers are not more generous down
    # there - their margin is in fact wider - but when they are wrong they are wrong by more. Jarvis needs
    # Pinnacle's price in those leagues to see it at all, and the goal rates come from Pinnacle's own prices
    # where there is no xG, so no extra data is needed.
    "soccer": ["England - Premier League", "England - Championship", "Spain - La Liga", "Spain - Segunda",
               "Italy - Serie A", "Italy - Serie B", "Germany - Bundesliga", "France - Ligue 1",
               "France - Ligue 2", "Netherlands - Eredivisie", "Portugal - Primeira", "Greece - Super",
               "Belgium - ", "Turkey - Super", "Scotland - Premiership", "Sweden - Allsvenskan", "UEFA",
               "Japan - J1", "Japan - J.League", "Australia - A-League", "Saudi Arabia", "AFC Champions"],
    "hockey": ["NHL", "Sweden - SHL", "SHL"],
}


def _usage_path():
    return os.path.join(common.DATA, "pinnacle", "usage.json")


def load_usage():
    try:
        with open(_usage_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def take_request(usage, day):
    """Reserve one request for `day` if under CAP; persist immediately. Returns True if allowed."""
    n = usage.get(day, 0)
    if n >= CAP:
        return False
    usage[day] = n + 1
    for d in sorted(usage)[:-14]:          # keep two weeks of history
        usage.pop(d, None)
    write_json("pinnacle/usage.json", usage)
    return True


def keep(sport, league):
    return any(k.lower() in (league or "").lower() for k in KEEP[sport])


def run(log: Log, key=None, now=None):
    key = key if key is not None else os.environ.get("PINNAPI_KEY", "")
    if not key:
        log.fail("pinnacle", "pinnapi.com", "no PINNAPI_KEY secret")
        return
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    usage = load_usage()
    events, calls = [], []
    for sport, sid in SPORTS.items():
        url = BASE.format(sid=sid)
        if not take_request(usage, day):
            log.fail(f"pinnacle {sport}", url, f"daily cap {CAP} reached; no request made")
            continue
        try:
            js = json.loads(get(url, retries=1, timeout=60, headers={"x-portal-apikey": key}).decode("utf-8"))
            evs = js.get("events") or []
            def main_line(e):
                """Pinnacle lists each soccer match up to three times (the main matchup plus alternative
                matchups without a draw price or totals). Keep the main one: 3-way money line or totals."""
                p0 = (e.get("periods") or {}).get("num_0") or {}
                if sport != "soccer":
                    return bool(p0)
                return (p0.get("money_line") or {}).get("draw") is not None or bool(p0.get("totals"))
            leagues_seen = {}
            for e in evs:
                leagues_seen[e.get("league_name")] = leagues_seen.get(e.get("league_name"), 0) + 1
            kept = [{"sport": sport, "event_id": e.get("event_id"), "league": e.get("league_name"), "league_id": e.get("league_id"),
                     "home": e.get("home"), "away": e.get("away"), "starts": e.get("starts"), "status": e.get("status"),
                     "periods": e.get("periods")} for e in evs if keep(sport, e.get("league_name")) and main_line(e)]
            events += kept
            calls.append({"sport": sport, "eventsTotal": len(evs), "kept": len(kept), "last": js.get("last"),
                          "leagues": dict(sorted(leagues_seen.items(), key=lambda x: -x[1])[:60])})
            log.ok(f"pinnacle {sport}", url, len(kept), note=f"{len(evs)} events from Pinnacle, {len(kept)} in scope")
        except Exception as e:
            msg = str(e).replace(key, "***")
            log.fail(f"pinnacle {sport}", url, msg)
    if events:
        write_json("pinnacle/odds.json", {"meta": {"fetchedAt": now_iso(), "source": "pinnapi.com (Pinnacle pre-match)",
                   "requestsToday": usage.get(day), "cap": CAP, "calls": calls,
                   "note": "decimal prices as published; soccer num_0 = full time, num_1 = first half; hockey num_0 includes OT and shootout"},
                   "events": events})
