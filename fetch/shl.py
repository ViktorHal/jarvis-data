"""SHL from stats.swehockey.se (Svenska Ishockeyförbundet's official statistics; plain HTML).

Writes:
  data/shl/games.json   every regular-season game: date, local start, UTC start, home, away, final score,
                        period scores, regulation-time score, overtime / shootout flags
  data/shl/teams.json   per team: games, goals for/against per game (final and regulation time), last 10,
                        home/away, played yesterday, last game date
  data/shl/schedule.json  today's and tomorrow's games
"""
import html
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from common import Log, get_text, write_json, now_iso

SERIES = 20961   # SHL 2026/27 on stats.swehockey.se
URL = f"https://stats.swehockey.se/ScheduleAndResults/Schedule/{SERIES}"
STO = ZoneInfo("Europe/Stockholm")


def _clean(c):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c or ""))).strip()


def parse_schedule(page):
    """HTML -> list of games. A row with a date sets the date for the following rows."""
    games, cur = [], None
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S):
        text = _clean(row)
        m = re.search(r"(20\d\d-\d\d-\d\d)", text)
        if m:
            cur = m.group(1)
        cells = [_clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
        teams = next((c for c in cells if re.fullmatch(r"(?=.*[A-Za-zÅÄÖåäö].* - .*[A-Za-zÅÄÖåäö]).+ - .+", c)
                      and not c.startswith("(")), None)
        if not teams or not cur:
            continue
        home, away = [t.strip() for t in teams.split(" - ", 1)]
        tm = next((c for c in cells if re.fullmatch(r"\d\d:\d\d", c)), None)
        score = next((c for c in cells if re.fullmatch(r"\d+ - \d+", c)), None)
        per = next((c for c in cells if c.startswith("(") and c.endswith(")")), None)
        g = {"date": cur, "time": tm, "home": home, "away": away, "played": bool(score)}
        if tm:
            local = datetime.fromisoformat(f"{cur}T{tm}").replace(tzinfo=STO)
            g["startUTC"] = local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        if score:
            hg, ag = [int(x) for x in score.split(" - ")]
            periods = [tuple(int(x) for x in p.split("-")) for p in re.findall(r"\d+-\d+", per or "")]
            g.update({"hg": hg, "ag": ag, "periods": periods})
            if len(periods) >= 3:
                rh, ra = sum(p[0] for p in periods[:3]), sum(p[1] for p in periods[:3])
                # periods 1-3 = regulation, 4 = overtime, 5 = shootout (the page lists the shootout as a 5th
                # "period" worth one goal to the winner)
                g.update({"reg_hg": rh, "reg_ag": ra, "overtime": len(periods) > 3, "shootout": len(periods) >= 5})
        games.append(g)
    return games


def _avg(xs):
    return round(sum(xs) / len(xs), 3) if xs else None


def team_table(games, today):
    played = sorted((g for g in games if g["played"] and "reg_hg" in g), key=lambda g: (g["date"], g.get("time") or ""))
    yesterday = (today - timedelta(days=1)).isoformat()
    teams = {}
    for g in played:
        for side, me, opp in (("H", g["home"], g["away"]), ("A", g["away"], g["home"])):
            t = teams.setdefault(me, [])
            gf, ga = (g["hg"], g["ag"]) if side == "H" else (g["ag"], g["hg"])
            rgf, rga = (g["reg_hg"], g["reg_ag"]) if side == "H" else (g["reg_ag"], g["reg_hg"])
            if g["shootout"]:            # the shootout winner's extra goal is not a real goal
                gf, ga = (rgf + _ot(g, side)[0], rga + _ot(g, side)[1])
            t.append({"date": g["date"], "venue": side, "opp": opp, "gf": gf, "ga": ga, "reg_gf": rgf, "reg_ga": rga})
    out = {}
    for name, log in teams.items():
        l10 = log[-10:]
        home = [x for x in log if x["venue"] == "H"]
        away = [x for x in log if x["venue"] == "A"]
        out[name] = {"gp": len(log), "gf_pg": _avg([x["gf"] for x in log]), "ga_pg": _avg([x["ga"] for x in log]),
                     "reg_gf_pg": _avg([x["reg_gf"] for x in log]), "reg_ga_pg": _avg([x["reg_ga"] for x in log]),
                     "l10_gp": len(l10), "l10_gf_pg": _avg([x["gf"] for x in l10]), "l10_ga_pg": _avg([x["ga"] for x in l10]),
                     "home_gp": len(home), "home_gf_pg": _avg([x["gf"] for x in home]), "home_ga_pg": _avg([x["ga"] for x in home]),
                     "away_gp": len(away), "away_gf_pg": _avg([x["gf"] for x in away]), "away_ga_pg": _avg([x["ga"] for x in away]),
                     "lastGame": log[-1]["date"], "played_yesterday": log[-1]["date"] == yesterday, "log": log[-10:]}
    return out


def _ot(g, side):
    """Overtime goals (period 4+) for (me, them); 0 if none."""
    extra = g["periods"][3:4]       # overtime only; the shootout "period" is not goals
    h, a = sum(p[0] for p in extra), sum(p[1] for p in extra)
    return (h, a) if side == "H" else (a, h)


def run(log: Log, today=None):
    today = today or datetime.now(STO).date()
    try:
        games = parse_schedule(get_text(URL))
        played = sum(g["played"] for g in games)
        if len(games) < 300 or played == 0:
            raise ValueError(f"unexpected page content: {len(games)} games, {played} played")
        meta = {"season": "2026/27", "series": SERIES, "source": URL, "fetchedAt": now_iso(), "games": len(games), "played": played,
                "note": "gf/ga exclude the shootout winner's extra goal; reg_* = regulation time (periods 1-3)"}
        write_json("shl/games.json", {"meta": meta, "games": games})
        write_json("shl/teams.json", {"meta": meta, "teams": team_table(games, today)})
        days = {today.isoformat(), (today + timedelta(days=1)).isoformat()}
        write_json("shl/schedule.json", {"meta": {"fetchedAt": now_iso(), "source": URL},
                   "games": [g for g in games if g["date"] in days]})
        log.ok("shl schedule", URL, len(games), note=f"{played} played")
    except Exception as e:
        log.fail("shl schedule", URL, e)
