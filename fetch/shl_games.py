"""SHL per-game statistics from stats.swehockey.se game pages (/Game/Events/<id>): shots, saves, goals per
period and the starting goalies with their saves and shots against. Input for hockey model 1.2 on the SHL.

Current season (robot, every run): data/shl/game_stats.json is a cache; each run fetches only the played games
that are not in it yet (a handful per day), with a pause between requests.
History (manual, workflow "fetch SHL history"): python fetch/shl_games.py history  -> history/shl/games_<season>.json

Conventions (same as the NHL data 1.2 was built on): shots and goals include overtime but NOT the shootout
(the page lists the shootout as a 5th "period"; it is dropped here); the starting goalie is the one with
"GK In" at 00:00, else the first goalie listed for the team; his saves / shots against exclude the shootout
when he was the team's only goalie (otherwise the page's own totals are used).
"""
import html
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, ROOT, Log, get_text, now_iso, write_json  # noqa: E402

BASE = "https://stats.swehockey.se"
SERIES = "20961"                                  # 2026-27 SHL regular season
# Regular-season series per season. For 2018-19 and 2020-21..2024-25 the season menu points at a play-off
# stage; the regular season ("SHL") is found from that overview page (resolve_regular).
SEASONS = {"2018-19": "9820", "2019-20": "10371", "2020-21": "12267", "2021-22": "13348", "2022-23": "14296",
           "2023-24": "15791", "2024-25": "17556", "2025-26": "18263"}
PAUSE = 1.0


def _clean(c):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c or ""))).strip()


def resolve_regular(overview_page, sid):
    """The id of the sub-series labelled exactly "SHL" on an overview page (the regular season), else sid."""
    for oid, label in re.findall(r'<option id="/ScheduleAndResults/Overview/(\d+)"[^>]*>([^<]*)', overview_page):
        if _clean(label) == "SHL":
            return oid
    return sid


def game_ids(schedule_page):
    """Played games on a schedule page: [(game id, date)] in page order."""
    out, cur = [], None
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", schedule_page, flags=re.S):
        m = re.search(r"(20\d\d-\d\d-\d\d)", _clean(row))
        if m:
            cur = m.group(1)
        g = re.search(r"Game/Events/(\d+)", row)
        if g and cur:
            out.append((g.group(1), cur))
    return out


def _periods(s):
    return [int(x) for x in s.split(":")] if s else []


def parse_game(page, gid=None):
    title = _clean(re.search(r"<title>(.*?)</title>", page, re.S).group(1))
    codes = re.match(r"(\S+) - (\S+)", title)
    hcode, acode = codes.group(1), codes.group(2)
    names = _clean(re.search(r"<h2>(.*?)</h2>", page, re.S).group(1))
    home, away = [x.strip() for x in names.split(" - ", 1)]
    when = re.search(r"<h3>\s*(20\d\d-\d\d-\d\d)(?:\s+(\d\d:\d\d))?\s*</h3>", page)
    sv = re.findall(r'>(Shots|Saves)</td><td class="tdOdd ?"><strong>(\d+)</strong></td><td class="tdOdd leftPad">\s*\(([\d:]+)\)', page)
    shots = [_periods(p) for k, _, p in sv if k == "Shots"]
    saves = [_periods(p) for k, _, p in sv if k == "Saves"]
    if len(shots) != 2 or len(saves) != 2:
        raise ValueError(f"game {gid}: shots/saves rows not found")
    score = re.search(r'font-weight: bold;">(\d+)\s*-\s*(\d+)</div><div>\s*\(([^)]*)\)', page)   # \xa0 around "-"
    per = [tuple(int(y) for y in x.split("-")) for x in re.findall(r"\d+-\d+", score.group(3))]
    shootout = len(per) >= 5
    keep = 4                                         # periods 1-3 and overtime; a 5th is the shootout
    k = page.find("Goalkeeper Summary")
    seg = page[k:page.find("<h3>", k + 30)] if k >= 0 else ""
    gks = [(c, _clean(n), int(s), int(a)) for c, _, n, s, a in re.findall(
        r'<td class="tdOdd">([^<]{2,6})</td><td class="tdOdd">(\d+)\.\s*([^<]*?)</td><td class="tdOdd"><strong>[^<]*</strong>\s*\((\d+)/(\d+)\)', seg)]
    text = _clean(page)
    first_in = {c: _clean(n) for c, n in re.findall(r"00:00 GK In (\S+) \d+\. (\S+, \S+)", text)}
    out = {"id": gid, "date": when.group(1) if when else None, "time": when.group(2) if when and when.group(2) else None,
           "home": home, "away": away, "codes": [hcode, acode],
           "goals": [sum(p[0] for p in per[:keep]), sum(p[1] for p in per[:keep])],
           "reg": [sum(p[0] for p in per[:3]), sum(p[1] for p in per[:3])],
           "shots": [sum(shots[0][:keep]), sum(shots[1][:keep])],
           "overtime": len(per) > 3, "shootout": shootout, "starters": [None, None], "goalies": gks}
    for i, (code, opp) in enumerate(((hcode, 1), (acode, 0))):
        mine = [g for g in gks if g[0] == code]
        if not mine:
            continue
        name = first_in.get(code) or mine[0][1]
        if len(mine) == 1:      # only goalie: the team's own lines, without the shootout
            st = {"name": name, "saves": sum(saves[i][:keep]), "shotsAgainst": sum(shots[opp][:keep])}
        else:
            g = next((g for g in mine if g[1] == name), mine[0])
            st = {"name": name, "saves": g[2], "shotsAgainst": g[3]}
        out["starters"][i] = st
    return out


def fetch_games(ids, have=None, pause=PAUSE, log_every=50):
    have = dict(have or {})
    for n, (gid, _) in enumerate(ids):      # _ = date on the schedule page
        if gid in have:
            continue
        g = parse_game(get_text(f"{BASE}/Game/Events/{gid}"), gid)
        g["date"] = g["date"] or _
        have[gid] = g
        if log_every and n % log_every == 0:
            print("  game", n, gid, flush=True)
        time.sleep(pause)
    return have


def run(log: Log):
    url = f"{BASE}/ScheduleAndResults/Schedule/{SERIES}"
    try:
        path = os.path.join(DATA, "shl", "game_stats.json")
        old = {}
        if os.path.exists(path):
            old = {g["id"]: g for g in json.load(open(path, encoding="utf-8")).get("games", [])}
        ids = game_ids(get_text(url))
        if not ids:
            raise ValueError("no played games on the schedule page")
        new = [i for i in ids if i[0] not in old][:150]    # at most 150 new pages a run (the first run catches up)
        games = fetch_games(new, old)
        keep = [games[i] for i, _ in ids if i in games]
        write_json("shl/game_stats.json", {"meta": {"season": "2026/27", "series": SERIES, "source": f"{BASE}/Game/Events/<id>",
                   "fetchedAt": now_iso(), "games": len(keep), "played": len(ids), "fetchedThisRun": len(new),
                   "note": "shots/goals incl. overtime, excl. shootout; starters = GK In at 00:00"}, "games": keep})
        log.ok("shl game stats", url, len(keep), f"{len(new)} new game pages")
    except Exception as e:
        log.fail("shl game stats", url, e)


def history(seasons):
    out_dir = os.path.join(ROOT, "history", "shl")
    os.makedirs(out_dir, exist_ok=True)
    for label in seasons:
        sid = SEASONS[label]
        reg = resolve_regular(get_text(f"{BASE}/ScheduleAndResults/Overview/{sid}"), sid)
        ids = game_ids(get_text(f"{BASE}/ScheduleAndResults/Schedule/{reg}"))
        print(label, "series", reg, len(ids), "games", flush=True)
        games, errors = {}, []
        for gid, d in ids:
            try:
                games.update(fetch_games([(gid, d)], log_every=0))
            except Exception as e:
                errors.append(f"{gid}: {str(e)[:100]}")
        with open(os.path.join(out_dir, f"games_{label}.json"), "w", encoding="utf-8") as f:
            json.dump({"season": label, "series": reg, "games": [games[i] for i, _ in ids if i in games], "errors": errors}, f,
                      ensure_ascii=False)
        print(label, len(games), "parsed,", len(errors), "errors", flush=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["history"]:
        history(sys.argv[2:] or list(SEASONS))
