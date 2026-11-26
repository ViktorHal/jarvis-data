"""Jarvis Elo for one match, copied exactly from data/elo/jelo.json, for the scan.

usage: python fetch/elo_match.py "<home club>" "<away club>"      (FotMob club names, accents optional)
Prints JSON {league_elo: {home, away}, clubs: {home: {...}, away: {...}}, provenance: {...}}. Put league_elo into the
match's inputs by code when the two clubs play in DIFFERENT domestic leagues (European ties, cups); same league:
leave it out. Exit 2 when a club is not found (then no league_elo; the gap check guards the result markets).
"""
import json
import os
import subprocess
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, ROOT  # noqa: E402


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).lower().strip()


ALIASES = {"hearts": "heart of midlothian", "spurs": "tottenham hotspur", "man utd": "manchester united",
           "man united": "manchester united", "man city": "manchester city", "psg": "paris saint-germain",
           "inter milan": "inter", "ac milan": "milan", "wolves": "wolverhampton wanderers", "bayern munich": "bayern munchen",
           "sporting cp": "sporting cp", "real sociedad": "real sociedad"}


def find(clubs, q):
    q = ALIASES.get(fold(q), q)
    exact = [k for k, c in clubs.items() if fold(c["name"]) == fold(q)]
    if len(exact) == 1:
        return exact[0]
    part = [k for k, c in clubs.items() if fold(q) in fold(c["name"]) or fold(c["name"]) in fold(q)]
    part = [k for k in part if clubs[k].get("league")]
    return max(part, key=lambda k: clubs[k]["games"]) if len(part) >= 1 and len({clubs[k]["name"] for k in part}) == 1 else None


def main(home, away):
    js = json.load(open(os.path.join(DATA, "elo", "jelo.json"), encoding="utf-8"))
    C, L = js["clubs"], js["leagues"]
    h, a = find(C, home), find(C, away)
    if not h or not a:
        print(json.dumps({"error": f"club not found or ambiguous: {home if not h else away}"}))
        return 2
    ch, ca = C[h], C[a]
    out = {"clubs": {"home": dict(ch, id=h, leagueMean=L.get(ch["league"], {}).get("mean")),
                     "away": dict(ca, id=a, leagueMean=L.get(ca["league"], {}).get("mean"))}}
    if ch["league"] != ca["league"] and out["clubs"]["home"]["leagueMean"] and out["clubs"]["away"]["leagueMean"]:
        out["league_elo"] = {"home": out["clubs"]["home"]["leagueMean"], "away": out["clubs"]["away"]["leagueMean"]}
    commit = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    out["provenance"] = {"status": "verified", "source": "jarvis-data/data/elo/jelo.json", "repoCommit": commit,
                         "repoFetchedAt": js["meta"]["fetchedAt"], "at": js["meta"]["fetchedAt"],
                         "values": {"home": out["clubs"]["home"], "away": out["clubs"]["away"], "league_elo": out.get("league_elo")},
                         "note": "copied by fetch/elo_match.py"}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
