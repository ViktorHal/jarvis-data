"""One-off: NHL game-level history for model development (team and goalie rows per game, regular season).

Writes history/nhl/team_<season>.json and history/nhl/goalie_<season>.json (written to the "history" branch
by the history workflow, never to main's data/). Source: api.nhle.com/stats/rest (free, keyless), 100 rows a page.
"""
import json
import os
import sys
import time
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import get_json  # noqa: E402

REST = "https://api.nhle.com/stats/rest/en"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "history", "nhl")


def pages(kind, season):
    exp = quote(f"seasonId={season} and gameTypeId=2")
    rows, start = [], 0
    while True:
        # a stable sort is required: without it the API pages overlap (duplicate rows) and skip others
        key = "teamId" if kind == "team" else "playerId"
        sort = quote(json.dumps([{"property": "gameId", "direction": "ASC"}, {"property": key, "direction": "ASC"}]))
        js = get_json(f"{REST}/{kind}/summary?isAggregate=false&isGame=true&start={start}&limit=100&sort={sort}&cayenneExp={exp}")
        page = js.get("data", [])
        rows += page
        start += len(page)
        if not page or start >= (js.get("total") or 0) or start > 20000:
            return rows
        time.sleep(0.3)


def main(seasons):
    os.makedirs(OUT, exist_ok=True)
    for s in seasons:
        for kind in ("team", "goalie"):
            rows = pages(kind, s)
            with open(os.path.join(OUT, f"{kind}_{s}.json"), "w") as f:
                json.dump(rows, f)
            print(kind, s, len(rows), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:] or ["20182019", "20192020", "20202021", "20212022", "20222023", "20232024", "20242025", "20252026"])
