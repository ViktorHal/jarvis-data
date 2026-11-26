"""Copy one match's hockey 1.2 inputs out of data/<league>/h12_state.json, exactly, for the Jarvis scan.

usage: python fetch/h12_match.py <nhl|shl> "<home>" "<away>" <game date YYYY-MM-DD, local> [home starter] [away starter]
  team: full name as in the file ("Montréal Canadiens"), its abbreviation ("MTL"), or the same without accents.
  starter: goalie name as in the file ("Igor Shesterkin" for the NHL); "-" or missing = starter unknown.
Prints JSON {h12, provenance}: put h12 into the match's "h12" field and provenance into provenance.H6, by code.
Exit code 2 (and nothing usable) if a team is not in the file. A named starter missing from the file is not an
error: the backtest gives such a goalie the league prior, and so does this block (inData: false).
"""
import json
import os
import subprocess
import sys
import unicodedata
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hockey12 as H  # noqa: E402
from common import DATA, ROOT  # noqa: E402


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).lower().strip()


def find_team(snap, q):
    T = snap["teams"]
    if q in T:
        return q
    # an abbreviation can belong to a renamed franchise too (UTA: Utah Hockey Club, then Utah Mammoth):
    # the team that played most recently is the current one
    ab = [k for k, v in T.items() if (v.get("abbrev") or "").upper() == q.upper()]
    if ab:
        return max(ab, key=lambda k: T[k].get("lastDate") or "")
    hits = [k for k in T if fold(k) == fold(q)]
    return hits[0] if len(hits) == 1 else None


def find_goalie(snap, q):
    if not q or q == "-":
        return None
    if q in snap["goalies"]:
        return q
    hits = [k for k in snap["goalies"] if fold(k) == fold(q)]
    return hits[0] if len(hits) == 1 else q          # unknown -> kept as named, league prior


def block(snap, home, away, day, gh=None, ga=None, league="nhl"):
    h, a = find_team(snap, home), find_team(snap, away)
    if not h or not a:
        raise KeyError(f"team not in h12 state: {home if not h else away}")
    prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    T = snap["teams"]
    b = H.match_block(snap, h, a, find_goalie(snap, gh), find_goalie(snap, ga),
                      T[h]["lastDate"] == prev, T[a]["lastDate"] == prev)
    b["gameDate"] = day
    # yesterday's games must be in the state, or a back-to-back (and one game of form) could be missed:
    # the state holds every game completed before its fetch, and the last of yesterday's games ends before
    # 08:00 UTC on game day (NHL, US evenings) or before midnight UTC (SHL)
    done = f"{day}T08:00:00Z" if league == "nhl" else f"{day}T00:00:00Z"
    b["stateBehind"] = (snap.get("meta") or {}).get("fetchedAt", "") < done
    return b


def main(argv):
    league, home, away, day = argv[:4]
    gh = argv[4] if len(argv) > 4 else None
    ga = argv[5] if len(argv) > 5 else None
    rel = f"{league}/h12_state.json"
    snap = json.load(open(os.path.join(DATA, rel), encoding="utf-8"))
    try:
        b = block(snap, home, away, day, gh, ga, league)
    except KeyError as e:
        print(json.dumps({"error": str(e)}))
        return 2
    try:
        commit = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = None
    prov = {"status": "verified", "source": f"jarvis-data/data/{rel}", "repoCommit": commit,
            "repoFetchedAt": snap["meta"]["fetchedAt"], "at": snap["meta"]["fetchedAt"],
            "values": {"home": b["home"], "away": b["away"], "goalieHome": b["goalieHome"], "goalieAway": b["goalieAway"],
                       "b2bHome": b["b2bHome"], "b2bAway": b["b2bAway"], "league": b["league"]},
            "note": "copied by fetch/h12_match.py" + ("; state may miss yesterday's games" if b["stateBehind"] else "")}
    print(json.dumps({"h12": b, "provenance": prov}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
