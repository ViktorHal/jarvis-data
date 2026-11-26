"""Football player shot rates from playerstats.football (robots.txt allows everything except /referees?).

For each league two leaderboards (top 50): total shots and shots on target, with minutes, totals and per-90.
Writes data/players/<slug>.json: {meta, players: {name: {team, minutes, shots, shots_p90, sot, sot_p90, id}}}.
A player in only one leaderboard keeps only that stat (never guessed).
"""
import html
import re
import time

from common import Log, get_text, write_json, now_iso

LEAGUES = {"premier-league": "Premier League", "championship": "Championship", "la-liga": "La Liga",
           "serie-a": "Serie A", "bundesliga": "Bundesliga", "ligue-1": "Ligue 1"}
STATS = {"total-shots": ("shots", "shots_p90"), "shots-on-target": ("sot", "sot_p90")}
URL = "https://playerstats.football/{slug}/stats/{stat}"


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse(page):
    """-> (season, [(id, name, team, minutes, total, per90)]) from the first (player) table."""
    season = (re.findall(r"20\d\d/20\d\d", page) or [None])[0]
    tables = re.findall(r"<table.*?</table>", page, flags=re.S)
    if not tables:
        return season, []
    out = []
    for row in re.findall(r"<tr.*?</tr>", tables[0], flags=re.S)[1:]:
        pm = re.search(r'href="[^"]*/player/(\d+)"[^>]*>\s*([^<]+?)\s*</a>', row)
        tm = re.search(r'<span[^>]*title="([^"]+)"', row)
        if not tm:                       # unabbreviated club names come without a title attribute
            tm = re.search(r'href="https://playerstats\.football/(?!player/)[a-z0-9-]+/[a-z0-9-]+"[^>]*>\s*(?:<span[^>]*>)?\s*([^<]+?)\s*<', row)
        nums = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)][-3:]
        if not pm or len(nums) < 3:
            continue
        mins, total, p90 = (_num(n) for n in nums)
        if None in (mins, total, p90):
            continue
        out.append((pm.group(1), html.unescape(pm.group(2)), html.unescape(tm.group(1)) if tm else None, int(mins), total, p90))
    return season, out


def run(log: Log):
    for slug, name in LEAGUES.items():
        players, ok, seasons = {}, 0, set()
        for stat, (tot_key, p90_key) in STATS.items():
            url = URL.format(slug=slug, stat=stat)
            try:
                season, rows = parse(get_text(url, retries=2))
                if season != "2026/2027":
                    raise ValueError(f"page shows season {season}, not 2026/2027")
                if len(rows) < 20:
                    raise ValueError(f"only {len(rows)} rows")
                seasons.add(season)
                for pid, pname, team, mins, total, p90 in rows:
                    p = players.setdefault(pname, {"id": pid, "team": team, "minutes": mins})
                    p[tot_key], p[p90_key] = total, p90
                ok += 1
                log.ok(f"players {slug} {stat}", url, len(rows))
            except Exception as e:
                log.fail(f"players {slug} {stat}", url, e)
            time.sleep(2)
        if ok:
            write_json(f"players/{slug}.json", {"meta": {"league": name, "season": "2026/27", "fetchedAt": now_iso(),
                       "source": URL.format(slug=slug, stat="<stat>"),
                       "note": "top-50 leaderboards for total shots and shots on target; per-90 as published"},
                       "players": players})
