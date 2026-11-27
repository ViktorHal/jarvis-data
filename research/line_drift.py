"""Does Pinnacle's own line movement point at the bets worth making? (27 Nov 2026)

Jarvis' price hunt so far was "a soft bookmaker pays 3% more than Pinnacle's fair price". Over 24,581 matches
with Pinnacle's opening and closing prices and Bet365's opening price (football-data.co.uk 2021/22-2025/26) that
rule returns +18% at fair odds 3-8 but its closing-line value is only +1.5%, which is close to no edge at all:
the market does not agree with those picks afterwards.

Adding Pinnacle's movement changes it completely. "Pinnacle's no-vig probability for this outcome rose by at
least 2 points, and Bet365 still pays at least the fair price" gives:

    fair 3-8   move >= 0.02, price >= fair      260 bets  +26.2% ROI  CLV +18.5%   (+51 +8 +21 +27 by season)
    fair 3-8   move >= 0.03, price >= fair      124 bets  +52.1% ROI  CLV +24.3%   (+66 +15 +39 +84)
    fair 3-8   move >= 0.02, price >= fair-2%   622 bets  +16.4% ROI  CLV +14.7%   (+28 +2 +14 +31, -29 part-season)
    fair 1.3-3 move >= 0.02, price >= fair-2%   202 bets   +9.3% ROI  CLV  +9.0%   (+18 +18 -8 -14)

A closing-line value of 15-25% is the real test: those bets were priced better than the market's own final word.

CAVEAT, and it matters: the backtest sees the whole move from open to close. In production Jarvis sees only the
part that has happened by the time it looks (Pinnacle is fetched hourly), so expect a fraction of this.
That is why production records the movement and the closing price for every hunt, so the owner's weekly report
can measure what it is really worth instead of trusting this number.

usage: python3 research/line_drift.py   (reads /home/claude/hist/history/football)
"""
import csv
import glob
import json
import os
import statistics as st
from collections import defaultdict

HIST = "/home/claude/hist/history/football"


def f(x):
    try:
        v = float(x)
        return v if v > 1 else None
    except (TypeError, ValueError):
        return None


def load():
    rows = []
    for p in sorted(glob.glob(os.path.join(HIST, "*.csv"))):
        s = os.path.basename(p)[:4]
        for r in csv.DictReader(open(p, encoding="utf-8-sig", errors="replace")):
            if not r.get("FTHG"):
                continue
            ps = [f(r.get(k)) for k in ("PSH", "PSD", "PSA")]
            pc = [f(r.get(k)) for k in ("PSCH", "PSCD", "PSCA")]
            b = [f(r.get(k)) for k in ("B365H", "B365D", "B365A")]
            if not (all(ps) and all(pc) and all(b)):
                continue
            nv = lambda x: [1 / v / sum(1 / w for w in x) for v in x]
            hg, ag = int(r["FTHG"]), int(r["FTAG"])
            rows.append((s, nv(ps), nv(pc), b, [hg > ag, hg == ag, hg < ag]))
    return rows


def sim(rows, lo, hi, move, edge):
    n = hit = 0
    pnl = 0.0
    clv = []
    ss = defaultdict(lambda: [0, 0.0])
    for s, pn, pc, b, y in rows:
        for i in range(3):
            if not (lo <= 1 / pn[i] <= hi) or (pc[i] - pn[i]) < move or b[i] < (1 + edge) / pn[i]:
                continue
            n += 1
            hit += y[i]
            g = (b[i] - 1) if y[i] else -1
            pnl += g
            clv.append(b[i] * pc[i] - 1)
            ss[s][0] += 1
            ss[s][1] += g
    return {"bets": n, "hit": round(hit / n, 3) if n else None, "roi": round(pnl / n * 100, 1) if n else None,
            "clv": round(100 * st.mean(clv), 1) if clv else None,
            "bySeason": {k: [v[0], round(v[1] / v[0] * 100, 1)] for k, v in sorted(ss.items())}}


def main():
    rows = load()
    out = {"matches": len(rows), "rules": {}}
    for lo, hi in ((1.3, 3.0), (3.0, 8.0)):
        for move in (-1, 0.0, 0.01, 0.02, 0.03):
            for edge in (-0.02, 0.0, 0.03):
                r = sim(rows, lo, hi, move, edge)
                if r["bets"] >= 50:
                    out["rules"][f"fair{lo}-{hi} move>={move} edge>={edge}"] = r
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "line_drift.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
