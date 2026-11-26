"""Final results for settling bets BY CODE (owner, 26 Nov 2026): no web search, no guessing.

Writes data/results/results.json: every finished match of the last 4 days that Jarvis could have priced:
  football (FotMob public JSON): score, half-time score, corners, yellow and red cards per team, how it ended
    (FT / AET / Pen), and every player who played with minutes, started, shots and shots on target
  NHL (api-web.nhle.com, keyless): final score, how it ended (REG / OT / SO), regulation score
  SHL (data/shl/games.json, refreshed here by fetch/shl.py): final score, regulation score, OT / SO
Runs every hour (results.yml); only matches not yet in the file are fetched in detail.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, Log, get_json, now_iso, write_json  # noqa: E402
import json  # noqa: E402

FM = "https://www.fotmob.com/api/data"
NHL = "https://api-web.nhle.com/v1/score"
KEEP_DAYS = 4
# FotMob league ids Jarvis prices (autoprice LEAGUES + Europe + Saudi)
FM_LEAGUES = {42: "Champions League", 73: "Europa League", 10216: "Conference League",
              47: "Premier League", 48: "Championship", 87: "LaLiga", 140: "LaLiga2", 55: "Serie A", 86: "Serie B",
              54: "Bundesliga", 146: "2. Bundesliga", 53: "Ligue 1", 110: "Ligue 2", 57: "Eredivisie",
              40: "First Division A", 61: "Liga Portugal", 71: "Super Lig", 135: "Super League 1", 64: "Premiership",
              67: "Allsvenskan", 536: "Saudi Pro League"}
MAX_DETAILS = 120          # per run, so one run never hammers FotMob


def _stat(md, key):
    """[home, away] ints for a FotMob stats key (corners, yellow_cards, red_cards), or None."""
    try:
        for g in md["content"]["stats"]["Periods"]["All"]["stats"]:
            for x in g.get("stats", []):
                if x.get("key") == key and isinstance(x.get("stats"), list) and len(x["stats"]) == 2:
                    a, b = x["stats"]
                    if a is None or b is None:
                        continue
                    return [int(a), int(b)]
    except (KeyError, TypeError, ValueError):
        return None
    return None


def football_detail(md):
    """matchDetails JSON -> result record (players: everyone who played)."""
    h = md["header"]
    st = h.get("status") or {}
    teams = h["teams"]
    reason = (st.get("reason") or {}).get("short")
    c = md["content"]
    hid = str(md["general"]["homeTeam"]["id"])
    players = {}
    for side in ("homeTeam", "awayTeam"):
        t = (c.get("lineup") or {}).get(side) or {}
        team = "home" if str(t.get("id")) == hid else "away"
        for p in t.get("starters", []) or []:
            players[p["id"]] = {"name": p["name"], "team": team, "started": True, "shots": 0, "sot": 0}
        for p in t.get("subs", []) or []:
            ev = (p.get("performance") or {}).get("substitutionEvents") or []
            if any(e.get("type") == "subIn" for e in ev):
                players[p["id"]] = {"name": p["name"], "team": team, "started": False, "shots": 0, "sot": 0}
    for s in (c.get("shotmap") or {}).get("shots", []) or []:
        p = players.get(s.get("playerId"))
        if p and not s.get("isOwnGoal"):
            p["shots"] += 1
            p["sot"] += 1 if s.get("isOnTarget") and not s.get("isBlocked") else 0
    return {"sport": "football", "id": str(md["general"]["matchId"]), "league": md["general"].get("leagueName"),
            "leagueId": md["general"].get("parentLeagueId") or md["general"].get("leagueId"),
            "start": md["general"].get("matchTimeUTCDate"), "home": teams[0]["name"], "away": teams[1]["name"],
            "score": [teams[0].get("score"), teams[1].get("score")], "ended": reason,
            "corners": _stat(md, "corners"), "yellow": _stat(md, "yellow_cards"), "red": _stat(md, "red_cards"),
            "playersKnown": bool(players), "players": list(players.values())}


def football(prev, log, today):
    have = {r["id"] for r in prev if r.get("sport") == "football"}
    out, todo, n = [], [], 0
    for d in (today - timedelta(days=1), today):
        js = get_json(f"{FM}/matches?date={d.strftime('%Y%m%d')}")
        for lg in js.get("leagues", []):
            if lg.get("primaryId") not in FM_LEAGUES:
                continue
            for m in lg.get("matches", []):
                s = m.get("status") or {}
                if s.get("finished") and not s.get("cancelled") and not s.get("awarded") and str(m["id"]) not in have:
                    todo.append(m["id"])
    for mid in todo[:MAX_DETAILS]:
        try:
            out.append(football_detail(get_json(f"{FM}/matchDetails?matchId={mid}")))
            n += 1
        except Exception as e:  # one broken match never stops the rest
            log.fail(f"fotmob match {mid}", FM, e)
    return out, len(todo)


def nhl(prev, today):
    have = {r["id"] for r in prev if r.get("sport") == "nhl"}
    out = []
    for d in (today - timedelta(days=1), today):
        js = get_json(f"{NHL}/{d.isoformat()}")
        for g in js.get("games", []):
            if g.get("gameState") not in ("OFF", "FINAL") or str(g["id"]) in have:
                continue
            h, a = g["homeTeam"], g["awayTeam"]
            ended = ((g.get("gameOutcome") or {}).get("lastPeriodType") or "REG").upper()
            hs, as_ = h.get("score"), a.get("score")
            reg = [hs, as_] if ended == "REG" else [min(hs, as_), min(hs, as_)]
            out.append({"sport": "nhl", "id": str(g["id"]), "league": "NHL", "start": g.get("startTimeUTC"),
                        "home": (h.get("name") or {}).get("default"), "away": (a.get("name") or {}).get("default"),
                        "homeAbbrev": h.get("abbrev"), "awayAbbrev": a.get("abbrev"),
                        "score": [hs, as_], "ended": ended, "reg": reg})
    return out


def shl(log):
    import shl as S
    S.run(log)
    games = json.load(open(os.path.join(DATA, "shl", "games.json"), encoding="utf-8"))["games"]
    out = []
    for g in games:
        if not g.get("played") or "reg_hg" not in g:
            continue
        ended = "SO" if g.get("shootout") else "OT" if g.get("overtime") else "REG"
        out.append({"sport": "shl", "id": f'{g["date"]}-{g["home"]}-{g["away"]}', "league": "SHL", "start": g.get("startUTC"),
                    "home": g["home"], "away": g["away"], "score": [g["hg"], g["ag"]], "ended": ended,
                    "reg": [g["reg_hg"], g["reg_ag"]]})
    return out


def run(log, now=None):
    now = now or datetime.now(timezone.utc)
    today = now.date()
    path = os.path.join(DATA, "results", "results.json")
    prev = []
    if os.path.exists(path):
        prev = json.load(open(path, encoding="utf-8")).get("matches", [])
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M")
    keep = [r for r in prev if (r.get("start") or "") >= cutoff and r.get("sport") != "shl"]
    meta = {"fetchedAt": now_iso(), "sources": {}, "note": "settle bets with jarvis/settle.py; football cards = yellow + red as "
            "FotMob counts them; goals markets settle on the score after 90 minutes (ended AET/Pen -> needs a manual check)"}
    try:
        f, todo = football(keep, log, today)
        keep += f
        meta["sources"]["fotmob"] = {"ok": True, "new": len(f), "pending": max(0, todo - len(f))}
        log.ok("results fotmob", FM, len(f))
    except Exception as e:
        meta["sources"]["fotmob"] = {"ok": False, "error": str(e)[:200]}
        log.fail("results fotmob", FM, e)
    try:
        h = nhl(keep, today)
        keep += h
        meta["sources"]["nhl"] = {"ok": True, "new": len(h)}
        log.ok("results nhl", NHL, len(h))
    except Exception as e:
        meta["sources"]["nhl"] = {"ok": False, "error": str(e)[:200]}
        log.fail("results nhl", NHL, e)
    try:
        s = [g for g in shl(log) if (g.get("start") or "") >= cutoff]
        keep += s
        meta["sources"]["shl"] = {"ok": True, "games": len(s)}
    except Exception as e:
        meta["sources"]["shl"] = {"ok": False, "error": str(e)[:200]}
        log.fail("results shl", "shl", e)
    keep.sort(key=lambda r: r.get("start") or "")
    meta["matches"] = len(keep)
    write_json("results/results.json", {"meta": meta, "matches": keep})
    return meta


if __name__ == "__main__":
    lg = Log()
    print(json.dumps(run(lg)))
    for k, v in lg.sources.items():
        if v["status"] != "ok":
            print(k, v.get("error"))
