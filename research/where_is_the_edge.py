"""Where does a soft bookmaker actually pay more than the sharp price? (28 Nov 2026)

The owner is 92 kr down in three days and tonight all three picks - under 4.5 goals in big-league matches -
were unplayable at every bookmaker. He asked the right question: how does one build a model that makes money,
and should Jarvis be hunting in the second division of Syria instead?

This measures it instead of guessing. For every division and every market in football-data's history, with
Pinnacle's closing price as the sharp reference and Bet365's as the soft one:

  generosity   the mean of (Bet365 price / Pinnacle's fair price) - above 1.00 means Bet365 pays more than the
               sharp price is worth, below means it pays less. A number like 0.95 means the market is sewn up.
  playable     the share of lines where Bet365 paid at least 3% over Pinnacle's fair price, which is Jarvis'
               own minimum. If this is near zero, nothing will ever be playable there, however good the model.
  roi / clv    what flat stakes on those lines returned, and how they stood against the closing price.

usage: python3 research/where_is_the_edge.py
"""
import csv
import glob
import json
import os
import statistics as st
from collections import defaultdict

HIST = "/home/claude/hist/history/football"
HERE = os.path.dirname(os.path.abspath(__file__))
EDGE = 0.03

DIVS = {"E0": "England Premier League", "E1": "England Championship", "E2": "England League One",
        "E3": "England League Two", "EC": "England Conference", "SC0": "Scotland Premiership",
        "SC1": "Scotland Championship", "SC2": "Scotland League One", "SC3": "Scotland League Two",
        "D1": "Germany Bundesliga", "D2": "Germany 2. Bundesliga", "I1": "Italy Serie A", "I2": "Italy Serie B",
        "SP1": "Spain La Liga", "SP2": "Spain Segunda", "F1": "France Ligue 1", "F2": "France Ligue 2",
        "N1": "Netherlands Eredivisie", "B1": "Belgium Pro League", "P1": "Portugal Primeira",
        "T1": "Turkey Super Lig", "G1": "Greece Super League"}


def f(x):
    try:
        v = float(x)
        return v if v > 1 else None
    except (TypeError, ValueError):
        return None


def novig(ps):
    inv = [1 / p for p in ps]
    s = sum(inv)
    return [x / s for x in inv]


def rows():
    for p in sorted(glob.glob(os.path.join(HIST, "*.csv"))):
        for r in csv.DictReader(open(p, encoding="utf-8-sig", errors="replace")):
            if not r.get("FTR"):
                continue
            yield r


def main():
    acc = defaultdict(lambda: {"gen": [], "play": 0, "n": 0, "pnl": [], "clv": []})
    for r in rows():
        div = r.get("Div", "")
        if div not in DIVS:
            continue
        try:
            hg, ag = int(r["FTHG"]), int(r["FTAG"])
        except (TypeError, ValueError, KeyError):
            continue
        sets = []
        pin = [f(r.get(k)) for k in ("PSCH", "PSCD", "PSCA")]
        b65 = [f(r.get(k)) for k in ("B365H", "B365D", "B365A")]
        if all(pin) and all(b65):
            won = [r["FTR"] == "H", r["FTR"] == "D", r["FTR"] == "A"]
            sets.append(("1X2", pin, b65, won))
        pin_t = [f(r.get(k)) for k in ("PC>2.5", "PC<2.5")]
        b65_t = [f(r.get(k)) for k in ("B365>2.5", "B365<2.5")]
        if all(pin_t) and all(b65_t):
            sets.append(("over/under 2.5", pin_t, b65_t, [hg + ag > 2.5, hg + ag < 2.5]))
        for market, pin_s, soft_s, won_s in sets:
            fair = novig(pin_s)
            for i, (p_fair, soft, won) in enumerate(zip(fair, soft_s, won_s)):
                a = acc[(div, market)]
                a["n"] += 1
                a["gen"].append(soft * p_fair)                 # 1.00 = exactly the sharp fair price
                if soft * p_fair - 1 >= EDGE:
                    a["play"] += 1
                    a["pnl"].append(soft - 1 if won else -1)
                    a["clv"].append(soft * p_fair - 1)
    out = {"edgeRequired": EDGE, "note": "Pinnacle closing as the sharp price, Bet365 closing as the soft one",
           "byDivision": {}}
    for (div, market), a in sorted(acc.items(), key=lambda kv: -st.mean(kv[1]["gen"])):
        if a["n"] < 500:
            continue
        out["byDivision"][f"{DIVS[div]} | {market}"] = {
            "lines": a["n"], "generosity": round(st.mean(a["gen"]), 4),
            "playableShare": round(a["play"] / a["n"], 4), "playableLines": a["play"],
            "roi": round(100 * st.mean(a["pnl"]), 1) if len(a["pnl"]) >= 30 else None,
            "clv": round(100 * st.mean(a["clv"]), 1) if len(a["clv"]) >= 30 else None}
    json.dump(out, open(os.path.join(HERE, "where_is_the_edge.json"), "w"), indent=1)
    print(f'{"division | market":44s} {"lines":>7} {"generosity":>10} {"playable":>9} {"roi":>7} {"clv":>6}')
    for k, v in out["byDivision"].items():
        print(f'{k:44s} {v["lines"]:>7} {v["generosity"]:>10.4f} {100*v["playableShare"]:>8.1f}% '
              f'{(f"{v["roi"]:+.1f}%" if v["roi"] is not None else "-"):>7} '
              f'{(f"{v["clv"]:+.1f}%" if v["clv"] is not None else "-"):>6}')


if __name__ == "__main__":
    main()
