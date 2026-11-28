"""BetMGM and Svenska Spel prices from the-odds-api.com, so the owner stops checking prices by hand.

Why this exists (owner, 28 Nov 2026): "it is impossible for me to go through 30 matches a day at three
bookmakers, that is a full-time job on its own". Jarvis can never open a bookmaker's site - that rule stands -
but the-odds-api.com is a licensed odds feed with a documented free tier, and its SWEDISH region carries both
of the bookmakers he mostly plays at: BetMGM and Svenska Spel (also ATG, Betsson, LeoVegas, Mr Green,
NordicBet, Unibet, 888sport, Betinia, CampoBet, Expekt, Hajper). Bet365 is not in any European region, so it
stays a manual extra. With these prices Jarvis compares its own minimum with what the bookmaker actually pays
BEFORE posting, and only posts bets he can place - no checking at all.

THE BUDGET IS THE WHOLE DESIGN. The free plan is 500 credits a MONTH, not a day, and one request costs
(markets x regions) credits. So the robot spends them only on the matches it is about to post:
  * it asks /v4/sports first, which costs nothing, to learn the exact sport keys;
  * it then looks at data/auto/board.json and takes the leagues of the products that could actually be posted
    (every row priced by the market) starting inside --hours;
  * at most --max-keys leagues per run, one market by default (totals), so a run costs 2-4 credits;
  * data/oddsapi/usage.json counts the month and the robot never makes the request that would pass CAP (450,
    below the 500 the plan gives, so a mistake cannot cost him money). The count is written BEFORE the
    request, so a crash mid-request still counts.
The key comes from the ODDSAPI_KEY secret and is never printed, logged or put in a stored URL.

Writes data/oddsapi/odds.json: per event, the de-margined-ready raw prices per bookmaker, in the API's own
team names and with its own numbers, nothing computed. autoprice.py matches them to Jarvis' matches.

usage: python3 fetch/oddsapi.py [--markets=totals] [--hours=10] [--max-keys=3] [--all-leagues]
"""
import json
import os
import re
import sys
import unicodedata
import urllib.error
from datetime import datetime, timedelta, timezone

from common import Log, get, now_iso, write_json
import common

CAP = 450                     # credits a month; the free plan gives 500
# A request costs [markets] x [regions], whatever the number of events it returns, so one league with one
# market is one credit and the whole league's card comes back. 1X2 is the market to spend them on:
# research/where_is_the_edge.json, 125,000 lines over 16 divisions, puts 1X2 at 8.7% closing-line value with
# 11.5% of lines playable, against 6.5% and 8.0% for over/under 2.5. So the default is h2h alone, and the
# credits buy LEAGUES instead of a second market - the edge is in the leagues Jarvis cannot see at all.
#
# The budget is a day's allowance, not a fixed number of leagues per run: 14 credits a day is 420 a month,
# inside the cap with room for a run by hand. DAY_BUDGET is cumulative by UTC hour, so what an empty hour
# does not spend rolls forward to the next one instead of being lost.
DAY = 14
DAY_BUDGET = {5: 5, 11: 9, 18: 14}      # by the hour the workflow fetches; see .github/workflows/odds.yml
MAX_LEAGUES = 10                        # a ceiling per run, so one run can never eat a whole day
BASE = "https://api.the-odds-api.com/v4"
REGION = "se"                 # the only region with BetMGM and Svenska Spel
# The region's exact keys, as the API's own bookmaker list gives them: most Swedish books carry a _se suffix,
# Betsson and NordicBet do not. Getting these wrong is silent - the book simply never appears - which is what
# happened on the first live run, when only betsson and nordicbet came through. Everything the region returns is
# stored; which of them he can actually BET at is a separate question, decided by HIS_BOOKS below.
WANT_BOOKS = ("betmgm_se", "svenskaspel_se", "betsson", "nordicbet", "unibet_se", "leovegas_se", "mrgreen_se",
              "sport888_se", "betinia_se", "campobet_se", "expekt_se", "hajper_se", "atg_se")
HIS_BOOKS = ("betmgm_se", "svenskaspel_se")     # the only two with an account; Bet365 is in no European region
# Jarvis' league names (as they appear on the board) -> a word to look for in the API's sport titles
# Measured closing-line value on 1X2 per division, from research/where_is_the_edge.json (125,000 lines,
# Pinnacle's closing price as the sharp one, Bet365's as the soft one). It orders the leagues the day's
# remaining allowance is spent on: Greece and the smaller leagues pay about half again what La Liga does,
# and that is the whole reason for buying breadth instead of a second market. A league not listed gets 7.0,
# a shade below the worst measured one. These numbers never touch a probability or a price.
LEAGUE_CLV = {
    "Greece - Super League": 12.1, "Netherlands - Eredivisie": 9.6, "Belgium - Pro League": 9.6,
    "Turkey - Super League": 9.6, "Portugal - Primeira Liga": 9.5, "Scotland - Premiership": 9.1,
    "Spain - Segunda Division": 8.7, "France - Ligue 1": 8.4, "England - Premier League": 8.3,
    "Italy - Serie A": 8.1, "Germany - Bundesliga": 7.9, "Germany - Bundesliga 2": 7.9,
    "France - Ligue 2": 7.9, "England - Championship": 7.7, "Spain - La Liga": 7.6, "Italy - Serie B": 7.6,
}

LEAGUE_HINT = {
    "England - Premier League": "epl", "England - Championship": "championship",
    "Spain - La Liga": "la liga", "Italy - Serie A": "serie a", "Germany - Bundesliga": "bundesliga",
    "France - Ligue 1": "ligue 1", "Netherlands - Eredivisie": "eredivisie",
    "Portugal - Primeira Liga": "primeira", "Belgium - Pro League": "belgium",
    "Turkey - Super League": "turkey", "Scotland - Premiership": "scotland",
    "Spain - Segunda Division": "la liga 2", "Italy - Serie B": "serie b", "France - Ligue 2": "ligue 2",
    "Germany - Bundesliga 2": "bundesliga 2", "Greece - Super League": "greece",
    "Sweden - Allsvenskan": "allsvenskan", "UEFA - Champions League": "champions league",
    "UEFA - Europa League": "europa league", "UEFA - Europa Conference League": "conference league",
    "NHL": "nhl", "Sweden - SHL": "sweden hockey", "Saudi Arabia - Pro League": "saudi",
    "Japan - J1 League": "japan", "Australia - A-League": "aleague",
}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower())


def _usage_path():
    return os.path.join(common.DATA, "oddsapi", "usage.json")


def load_usage():
    try:
        with open(_usage_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def take_credits(usage, month, n, day=None):
    """Reserve n credits if that stays under CAP; persist immediately. True if allowed.

    Written BEFORE the request, so a crash costs credits rather than hiding them. The day counter is what
    the allowance below reads; the month counter is the hard cap.
    """
    used = int(usage.get(month, 0))
    if used + n > CAP:
        return False
    usage[month] = used + n
    if day:
        usage[day] = int(usage.get(day, 0)) + n
    for m in sorted(k for k in usage if re.fullmatch(r"\d{4}-\d{2}", k))[:-6]:
        usage.pop(m, None)
    for d in sorted(k for k in usage if re.fullmatch(r"\d{4}-\d{2}-\d{2}", k))[:-45]:
        usage.pop(d, None)
    write_json("oddsapi/usage.json", usage)
    return True


def allowance(usage, now, hour=None):
    """How many credits this run may spend: its share of the day plus whatever earlier hours left unused."""
    hour = now.hour if hour is None else hour
    cum = DAY
    for h in sorted(DAY_BUDGET):
        if hour <= h:
            cum = DAY_BUDGET[h]
            break
    left_today = cum - int(usage.get(now.strftime("%Y-%m-%d"), 0))
    left_month = CAP - int(usage.get(now.strftime("%Y-%m"), 0))
    return max(0, min(left_today, left_month))


def sport_keys(log):
    """/v4/sports costs no credits. Returns [{key, title, group}] of the active sports."""
    try:
        js = json.loads(get(f"{BASE}/sports/?apiKey={os.environ['ODDSAPI_KEY']}", retries=2, timeout=40)
                        .decode("utf-8"))
        return [s for s in js if s.get("active")]
    except Exception as e:
        log.fail("oddsapi sports", f"{BASE}/sports", e)
        return []


def leagues_wanted(board, now, hours, market_only=True):
    """The leagues of the products the scan could actually post, soonest first."""
    out = []
    starts = {m.get("id"): m.get("start") for m in board.get("matches") or []}
    lg = {m.get("id"): m.get("league") for m in board.get("matches") or []}
    horizon = (now + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for p in board.get("products") or []:
        rows = [p["line"]] if p.get("line") else [l for l in (p.get("legs") or []) if isinstance(l, dict)]
        if market_only and not all(r.get("anchored") for r in rows):
            continue
        for mid in {p.get("matchId")} | {l.get("matchId") for l in (p.get("legs") or []) if isinstance(l, dict)}:
            s = starts.get(mid) or p.get("start")
            if mid and s and s <= horizon and lg.get(mid) and lg[mid] not in out:
                out.append(lg[mid])
    # Then every other league with a match in the window, the busiest first, to fill the day's remaining
    # allowance: a league Jarvis has no price for is a league it cannot find a bet in at all, and the measured
    # edge is largest exactly in the leagues it would otherwise never ask about (where_is_the_edge.json).
    rest = {}
    for m in board.get("matches") or []:
        s2, l2 = m.get("start"), m.get("league")
        if l2 and l2 not in out and s2 and s2 <= horizon:
            rest[l2] = rest.get(l2, 0) + 1
    return out + [l for l, _ in sorted(rest.items(), key=lambda kv: (-LEAGUE_CLV.get(kv[0], 7.0), -kv[1]))]


def match_key(league, sports):
    hint = LEAGUE_HINT.get(league)
    if not hint:
        return None
    for s in sports:
        if norm(hint) in norm(s.get("title")) or norm(hint) in norm(s.get("key")):
            return s["key"]
    return None


def parse_event(ev):
    """One event -> {start, home, away, books: {book: {"h2h": {...}, "totals": {line: {over, under}}}}}."""
    out = {"id": ev.get("id"), "start": ev.get("commence_time"), "home": ev.get("home_team"),
           "away": ev.get("away_team"), "books": {}}
    for b in ev.get("bookmakers") or []:
        key = b.get("key")
        if key not in WANT_BOOKS:
            continue
        got = {}
        for m in b.get("markets") or []:
            if m.get("key") == "h2h":
                h = {}
                for o in m.get("outcomes") or []:
                    side = ("home" if o.get("name") == ev.get("home_team") else
                            "away" if o.get("name") == ev.get("away_team") else
                            "draw" if str(o.get("name")).lower() == "draw" else None)
                    if side and isinstance(o.get("price"), (int, float)):
                        h[side] = o["price"]
                if h:
                    got["h2h"] = h
            elif m.get("key") == "totals":
                t = {}
                for o in m.get("outcomes") or []:
                    pt, nm, pr = o.get("point"), str(o.get("name")).lower(), o.get("price")
                    if pt is None or nm not in ("over", "under") or not isinstance(pr, (int, float)):
                        continue
                    t.setdefault(f"{float(pt):g}", {})[nm] = pr
                if t:
                    got["totals"] = {k: v for k, v in t.items() if "over" in v and "under" in v}
        if got:
            out["books"][key] = got
    return out if out["books"] else None


def run(log: Log, now=None, markets="h2h", hours=10, max_keys=None, all_leagues=False):
    if not os.environ.get("ODDSAPI_KEY"):
        log.fail("oddsapi", "the-odds-api.com", "no ODDSAPI_KEY secret; set it and this runs by itself")
        return
    now = now or datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")
    usage = load_usage()
    try:
        board = json.load(open(os.path.join(common.DATA, "auto", "board.json"), encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.fail("oddsapi", "data/auto/board.json", f"no board to aim at: {e}")
        return
    per = len(markets.split(","))
    budget = allowance(usage, now)
    room = min(MAX_LEAGUES, budget // per) if max_keys is None else max_keys
    if room < 1:
        log.ok("oddsapi", BASE, 0, f"no credits left in today's allowance ({budget} of {DAY}); nothing fetched")
        return
    sports = sport_keys(log)
    if not sports:
        return
    want = leagues_wanted(board, now, hours) if not all_leagues else list(LEAGUE_HINT)
    keys, seen = [], set()
    for lg in want:
        k = match_key(lg, sports)
        if k and k not in seen:
            seen.add(k)
            keys.append((lg, k))
        if len(keys) >= room:
            break
    if not keys:
        log.ok("oddsapi", BASE, 0, f"nothing to price in the next {hours} h")
        write_json("oddsapi/odds.json", {"meta": {"fetchedAt": now_iso(), "events": 0, "keys": [],
                                                  "markets": markets, "region": REGION,
                                                  "note": "no market-priced product inside the window"},
                                         "events": []})
        return
    events, calls, remaining, seen = [], [], None, set()
    for lg, k in keys:
        if not take_credits(usage, month, per, now.strftime("%Y-%m-%d")):
            log.fail("oddsapi", f"{BASE}/sports/{k}/odds", f"monthly cap {CAP} reached; no request made")
            break
        url = f"{BASE}/sports/{k}/odds/?regions={REGION}&markets={markets}&oddsFormat=decimal"
        try:
            raw = get(url + f"&apiKey={os.environ['ODDSAPI_KEY']}", retries=2, timeout=60)
            js = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            log.fail(f"oddsapi {k}", url, f"HTTP {e.code}")
            calls.append({"key": k, "league": lg, "error": f"HTTP {e.code}", "at": now_iso()})
            continue
        except Exception as e:
            log.fail(f"oddsapi {k}", url, e)
            calls.append({"key": k, "league": lg, "error": str(e)[:120], "at": now_iso()})
            continue
        for e in js:                      # every key the region returned, kept or not: a wrong filter in
            for b in (e.get("bookmakers") or []):   # WANT_BOOKS silently drops a book, so it must be visible
                if b.get("key"):
                    seen.add(b["key"])
        got = [p for p in (parse_event(e) for e in js) if p]
        for g in got:
            g["league"] = lg
            g["sportKey"] = k
        events += got
        calls.append({"key": k, "league": lg, "events": len(got), "credits": per, "at": now_iso()})
    write_json("oddsapi/odds.json", {
        "meta": {"fetchedAt": now_iso(), "events": len(events), "region": REGION, "markets": markets,
                 "keys": [k for _, k in keys], "calls": calls, "creditsThisMonth": usage.get(month, 0),
                 "creditsToday": usage.get(now.strftime("%Y-%m-%d"), 0), "allowanceThisRun": budget,
                 "leaguesAsked": len(keys), "leaguesWanted": len(want), "day": DAY,
                 "cap": CAP, "books": list(WANT_BOOKS), "booksSeen": sorted(seen),
                 "booksDropped": sorted(b for b in seen if b not in WANT_BOOKS),
                 "hisBooks": [b for b in HIS_BOOKS if b in seen],
                 "note": "prices exactly as the-odds-api.com returned them, in its own team names"},
        "events": events})
    log.ok("oddsapi", BASE, len(events), f"{usage.get(month, 0)}/{CAP} credits used this month")


if __name__ == "__main__":
    kw = {}
    for a in sys.argv[1:]:
        if a.startswith("--markets="):
            kw["markets"] = a.split("=", 1)[1]
        elif a.startswith("--hours="):
            kw["hours"] = int(a.split("=", 1)[1])
        elif a.startswith("--max-keys="):
            kw["max_keys"] = int(a.split("=", 1)[1])
        elif a == "--all-leagues":
            kw["all_leagues"] = True
    log = Log()
    run(log, **kw)
    print(json.dumps(log.sources, indent=1))
