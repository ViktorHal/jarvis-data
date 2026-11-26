"""NHL: the free, keyless JSON behind NHL.com (api-web.nhle.com, api.nhle.com/stats/rest).

Writes:
  data/nhl/teams.json      per team: season and last-10 goals for/against per game, shots for/against
                           per game, power play %, penalty kill %, played yesterday (back-to-back flag source)
  data/nhl/goalies.json    per goalie: team, games, starts, save %, GAA
  data/nhl/skaters.json    per skater (>= 5 games): team, games, shots per game, points per game, TOI per game
  data/nhl/schedule.json   today's and tomorrow's games (UTC dates)
"""
from datetime import date, timedelta
from urllib.parse import quote

from common import Log, get_json, write_json, now_iso

SEASON = "20262027"
WEB = "https://api-web.nhle.com/v1"
REST = "https://api.nhle.com/stats/rest/en"
EXP = quote(f"seasonId={SEASON} and gameTypeId=2")


def _pg(x, n):
    return round(x / n, 3) if n else None


def standings_table(js):
    out = {}
    for t in js.get("standings", []):
        ab = (t.get("teamAbbrev") or {}).get("default")
        gp, l10 = t.get("gamesPlayed") or 0, t.get("l10GamesPlayed") or 0
        out[ab] = {"name": (t.get("teamName") or {}).get("default"), "gp": gp,
                   "gf_pg": _pg(t.get("goalFor", 0), gp), "ga_pg": _pg(t.get("goalAgainst", 0), gp),
                   "l10_gp": l10, "l10_gf_pg": _pg(t.get("l10GoalsFor", 0), l10), "l10_ga_pg": _pg(t.get("l10GoalsAgainst", 0), l10),
                   "home_gp": t.get("homeGamesPlayed"), "home_gf": t.get("homeGoalsFor"), "home_ga": t.get("homeGoalsAgainst"),
                   "road_gp": t.get("roadGamesPlayed"), "road_gf": t.get("roadGoalsFor"), "road_ga": t.get("roadGoalsAgainst")}
    return out


def merge_summary(teams, js):
    """Add shots and special teams from stats/rest team summary (matched on full team name)."""
    by_name = {v["name"]: k for k, v in teams.items() if v.get("name")}
    matched = 0
    for r in js.get("data", []):
        ab = by_name.get(r.get("teamFullName")) or next((k for k, v in teams.items() if v.get("name") and v["name"] in (r.get("teamFullName") or "")), None)
        if not ab:
            continue
        matched += 1
        teams[ab].update({"shots_for_pg": r.get("shotsForPerGame"), "shots_against_pg": r.get("shotsAgainstPerGame"),
                          "pp_pct": r.get("powerPlayPct"), "pk_pct": r.get("penaltyKillPct")})
    return matched


def played_on(js):
    """Team abbreviations that played in a /score/<date> response."""
    out = set()
    for g in js.get("games", []):
        for side in ("homeTeam", "awayTeam"):
            ab = (g.get(side) or {}).get("abbrev")
            if ab:
                out.add(ab)
    return out


def run(log: Log, today=None):
    today = today or date.today()
    teams = {}
    url = f"{WEB}/standings/now"
    try:
        teams = standings_table(get_json(url))
        log.ok("nhl standings", url, len(teams))
    except Exception as e:
        log.fail("nhl standings", url, e)
    url = f"{REST}/team/summary?cayenneExp={EXP}"
    try:
        n = merge_summary(teams, get_json(url))
        log.ok("nhl team summary", url, n)
    except Exception as e:
        log.fail("nhl team summary", url, e)
    y = (today - timedelta(days=1)).isoformat()
    url = f"{WEB}/score/{y}"
    try:
        yesterday = played_on(get_json(url))
        for ab, t in teams.items():
            t["played_yesterday"] = ab in yesterday
        log.ok("nhl yesterday", url, len(yesterday), note=f"teams that played on {y} (US date)")
    except Exception as e:
        log.fail("nhl yesterday", url, e)
    if teams:
        write_json("nhl/teams.json", {"meta": {"season": "2026-27", "fetchedAt": now_iso(),
                   "sources": [f"{WEB}/standings/now", f"{REST}/team/summary", f"{WEB}/score/{y}"]}, "teams": teams})
    url = f"{REST}/goalie/summary?limit=-1&cayenneExp={EXP}"
    try:
        g = {r["goalieFullName"]: {"team": r.get("teamAbbrevs"), "gp": r.get("gamesPlayed"), "gs": r.get("gamesStarted"),
                                   "sv_pct": r.get("savePct"), "gaa": r.get("goalsAgainstAverage"), "shots_against": r.get("shotsAgainst")}
             for r in get_json(url).get("data", [])}
        write_json("nhl/goalies.json", {"meta": {"season": "2026-27", "fetchedAt": now_iso(), "source": url}, "goalies": g})
        log.ok("nhl goalies", url, len(g))
    except Exception as e:
        log.fail("nhl goalies", url, e)
    url = f"{REST}/skater/summary?limit=-1&cayenneExp={EXP}"
    try:
        s = {}
        for r in get_json(url).get("data", []):
            gp = r.get("gamesPlayed") or 0
            if gp < 5:
                continue
            s[r["skaterFullName"]] = {"team": r.get("teamAbbrevs"), "pos": r.get("positionCode"), "gp": gp,
                                      "shots_pg": _pg(r.get("shots") or 0, gp), "points_pg": r.get("pointsPerGame"),
                                      "toi_pg_sec": r.get("timeOnIcePerGame")}
        write_json("nhl/skaters.json", {"meta": {"season": "2026-27", "fetchedAt": now_iso(), "source": url, "minGames": 5}, "skaters": s})
        log.ok("nhl skaters", url, len(s))
    except Exception as e:
        log.fail("nhl skaters", url, e)
    games = []
    for d in (today, today + timedelta(days=1)):
        url = f"{WEB}/score/{d.isoformat()}"
        try:
            day = [{"date": d.isoformat(), "start": g.get("startTimeUTC"), "home": (g.get("homeTeam") or {}).get("abbrev"),
                    "away": (g.get("awayTeam") or {}).get("abbrev"), "state": g.get("gameState")}
                   for g in get_json(url).get("games", [])]
            games += day
            log.ok(f"nhl schedule {d.isoformat()}", url, len(day))
        except Exception as e:
            log.fail(f"nhl schedule {d.isoformat()}", url, e)
    write_json("nhl/schedule.json", {"meta": {"fetchedAt": now_iso()}, "games": games})
