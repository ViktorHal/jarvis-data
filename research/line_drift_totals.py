"""Does Pinnacle's movement pay on the GOAL TOTALS too, not only 1X2? (27 Nov 2026)

research/line_drift.json established the rule Jarvis' price hunt now uses: back an outcome only where Pinnacle's
own de-margined price has moved toward it and a soft bookmaker still pays the fair price. That was measured on
1X2. decision.py 3.10 prices goal lines from a grid fitted to Pinnacle, and goal lines are the bets his
bookmakers actually offer, so the same question has to be asked of the over/under market before any claim is made
about it: football-data carries Pinnacle's opening and closing over/under 2.5 prices and Bet365's opening price.

usage: python3 research/line_drift_totals.py
"""
import csv
import glob
import json
import os
import statistics as st

HIST = "/home/claude/hist/history/football"
HERE = os.path.dirname(os.path.abspath(__file__))


def f(x):
    try:
        v = float(x)
        return v if v > 1 else None
    except (TypeError, ValueError):
        return None


def novig(prices):
    inv = [1 / p for p in prices]
    s = sum(inv)
    return [x / s for x in inv]


def load():
    rows = []
    for p in sorted(glob.glob(os.path.join(HIST, "*.csv"))):
        season = os.path.basename(p)[:4]
        for r in csv.DictReader(open(p, encoding="utf-8-sig", errors="replace")):
            if not r.get("FTHG"):
                continue
            po = [f(r.get(k)) for k in ("P>2.5", "P<2.5")]          # Pinnacle, opening
            pc = [f(r.get(k)) for k in ("PC>2.5", "PC<2.5")]        # Pinnacle, closing
            b = [f(r.get(k)) for k in ("B365>2.5", "B365<2.5")]     # a soft book, opening
            if not (all(po) and all(pc) and all(b)):
                continue
            try:
                tot = int(r["FTHG"]) + int(r["FTAG"])
            except (TypeError, ValueError):
                continue
            rows.append((season, novig(po), novig(pc), b, [tot > 2.5, tot < 2.5]))
    return rows


def run(rows, moves=(-1, 0.0, 0.01, 0.02, 0.03), edges=(-0.02, 0.0, 0.03)):
    out = {}
    for mv in moves:
        for ed in edges:
            bets = []
            for season, po, pc, b, won in rows:
                for i in (0, 1):                     # over, then under
                    move = pc[i] - po[i]
                    if move < mv or b[i] * po[i] - 1 < ed:
                        continue
                    bets.append((season, b[i] * (1 if won[i] else 0) - 1, b[i] * pc[i] - 1, won[i], i))
            if len(bets) < 25:
                continue
            by = {}
            for s, pnl, clv, w, i in bets:
                by.setdefault(s, []).append(pnl)
            out[f"move>={mv} edge>={ed}"] = {
                "bets": len(bets), "overs": sum(1 for x in bets if x[4] == 0),
                "hit": round(st.mean(1 if x[3] else 0 for x in bets), 3),
                "roi": round(100 * st.mean(x[1] for x in bets), 1),
                "clv": round(100 * st.mean(x[2] for x in bets), 1),
                "bySeason": {s: [len(v), round(100 * st.mean(v), 1)] for s, v in sorted(by.items())}}
    return out


if __name__ == "__main__":
    rows = load()
    res = {"matches": len(rows), "market": "over/under 2.5 goals, Pinnacle open -> close, Bet365 opening price",
           "rules": run(rows)}
    print(json.dumps(res, indent=1))
    with open(os.path.join(HERE, "line_drift_totals.json"), "w") as fh:
        json.dump(res, fh, indent=1)
