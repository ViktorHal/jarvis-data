"""One-off: build data/nhl/h12_seed.json (hockey 1.2 State after 2018/19-2025/26) from the history branch files.

usage: python fetch/h12_seed_nhl.py <dir with team_<season>.json and goalie_<season>.json>
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hockey12 as H  # noqa: E402
from common import write_json  # noqa: E402
from h12_nhl import games_from_rows, replay  # noqa: E402

if __name__ == "__main__":
    d = sys.argv[1]
    st = H.State()
    seasons = sorted(os.path.basename(f)[5:13] for f in glob.glob(os.path.join(d, "team_*.json")))
    for s in seasons:
        replay(st, games_from_rows(json.load(open(os.path.join(d, f"team_{s}.json"))),
                                   json.load(open(os.path.join(d, f"goalie_{s}.json"))), s))
    snap = st.snapshot()
    snap["meta"] = {"builtFrom": "history branch, api.nhle.com/stats/rest per-game summaries", "seasons": seasons}
    write_json("nhl/h12_seed.json", snap)
    print(len(st.teams), "teams", len(st.goalies), "goalies", st.games, "games through", st.through)
