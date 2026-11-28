"""Do head-to-head history, recent form and table position add anything to the market's price? (28 Nov 2026)

The owner looked at the Stryktipset coupon and asked why Jarvis had not weighed missing players, how the two
sides usually do against each other, where they stand in the table and their last five matches. Those are the
right questions, so they are measured here rather than argued about: for every match with Pinnacle's closing
1X2, each factor is added to the market's own de-margined probability and scored out of sample. If a factor
carries information the market has not already priced, the log-loss falls. If it does not, it cannot help.

Factors, all built only from matches played BEFORE the one being predicted:
  h2h      the two sides' previous meetings: (home wins - away wins) / meetings, last 6 meetings
  form     points per game over each side's last 5 league matches, home minus away
  table    points per game over the season so far, home minus away (a proxy for the table)
  gdiff    goal difference per game over the season so far, home minus away

Method: logistic regression of "home win" on logit(market probability) plus one factor at a time, fitted on the
first 70% of matches by date and scored on the last 30%. A coefficient near zero, or a log-loss that does not
improve, means the market has the factor already. Gradient descent, stdlib only.

usage: python3 research/extra_factors.py
"""
import csv
import glob
import json
import math
import os
from collections import defaultdict, deque

HIST = "/home/claude/hist/history/football"
HERE = os.path.dirname(os.path.abspath(__file__))


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


def load():
    rows = []
    for p in sorted(glob.glob(os.path.join(HIST, "*.csv"))):
        for r in csv.DictReader(open(p, encoding="utf-8-sig", errors="replace")):
            ml = [f(r.get(k)) for k in ("PSCH", "PSCD", "PSCA")]
            if not all(ml) or not r.get("FTR") or not r.get("HomeTeam"):
                continue
            d = (r.get("Date") or "")
            try:                                      # dd/mm/yy or dd/mm/yyyy
                dd, mm, yy = d.split("/")
                yy = int(yy) + (2000 if len(yy) == 2 else 0)
                iso = f"{yy:04d}-{int(mm):02d}-{int(dd):02d}"
            except ValueError:
                continue
            try:
                hg, ag = int(r["FTHG"]), int(r["FTAG"])
            except (TypeError, ValueError, KeyError):
                continue
            rows.append({"date": iso, "div": r.get("Div", ""), "home": r["HomeTeam"], "away": r["AwayTeam"],
                         "ftr": r["FTR"], "hg": hg, "ag": ag, "p": novig(ml)})
    rows.sort(key=lambda x: x["date"])
    return rows


def build(rows):
    """Attach the factors, each from earlier matches only."""
    h2h = defaultdict(list)                    # (team, team) sorted -> [+1 home win, 0 draw, -1 away win] for A
    last = defaultdict(lambda: deque(maxlen=5))
    season = defaultdict(lambda: [0, 0, 0])    # team -> [points, games, goal difference]
    out = []
    cur_season = None
    for r in rows:
        y, m = int(r["date"][:4]), int(r["date"][5:7])
        s = y if m >= 7 else y - 1              # a season starts in July
        if s != cur_season:
            season.clear()
            cur_season = s
        a, b = sorted((r["home"], r["away"]))
        past = h2h[(a, b)][-6:]
        sign = 1 if a == r["home"] else -1
        x = {}
        x["h2h"] = sign * sum(past) / len(past) if past else None
        fh, fa = last[r["home"]], last[r["away"]]
        x["form"] = (sum(fh) / len(fh) - sum(fa) / len(fa)) if len(fh) == 5 and len(fa) == 5 else None
        ph, pa = season[r["home"]], season[r["away"]]
        x["table"] = (ph[0] / ph[1] - pa[0] / pa[1]) if ph[1] >= 5 and pa[1] >= 5 else None
        x["gdiff"] = (ph[2] / ph[1] - pa[2] / pa[1]) if ph[1] >= 5 and pa[1] >= 5 else None
        out.append({**r, "x": x})
        # update, after predicting
        res = 1 if r["ftr"] == "H" else 0 if r["ftr"] == "D" else -1
        h2h[(a, b)].append(res if a == r["home"] else -res)
        hp, ap = (3, 0) if res == 1 else (1, 1) if res == 0 else (0, 3)
        last[r["home"]].append(hp)
        last[r["away"]].append(ap)
        for t, pts, gd in ((r["home"], hp, r["hg"] - r["ag"]), (r["away"], ap, r["ag"] - r["hg"])):
            season[t][0] += pts
            season[t][1] += 1
            season[t][2] += gd
    return out


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def fit(rows, key, iters=4000, lr=0.05):
    """Logistic regression of a home win on logit(market) and one factor. Returns the fit and the out-of-sample
    log-loss with and without the factor."""
    data = [(logit(r["p"][0]), r["x"][key], 1 if r["ftr"] == "H" else 0) for r in rows if r["x"][key] is not None]
    if len(data) < 2000:
        return {"n": len(data), "note": "too few matches"}
    cut = int(0.7 * len(data))
    tr, te = data[:cut], data[cut:]
    sd = (sum((d[1] - sum(y[1] for y in tr) / len(tr)) ** 2 for d in tr) / len(tr)) ** 0.5 or 1.0
    mu = sum(y[1] for y in tr) / len(tr)
    z = lambda v: (v - mu) / sd
    w = [0.0, 1.0, 0.0]                        # intercept, market, factor
    for _ in range(iters):
        g = [0.0, 0.0, 0.0]
        for lm, fx, y in tr:
            pr = 1 / (1 + math.exp(-(w[0] + w[1] * lm + w[2] * z(fx))))
            e = pr - y
            g[0] += e
            g[1] += e * lm
            g[2] += e * z(fx)
        for i in range(3):
            w[i] -= lr * g[i] / len(tr)
    ll = lambda f_: -sum(math.log(max(1e-9, (lambda pr: pr if y else 1 - pr)(1 / (1 + math.exp(-f_(lm, fx))))))
                         for lm, fx, y in te) / len(te)
    base = ll(lambda lm, fx: logit(1 / (1 + math.exp(-lm))))          # the market itself
    with_f = ll(lambda lm, fx: w[0] + w[1] * lm + w[2] * z(fx))
    without = ll(lambda lm, fx: w[0] + w[1] * lm)
    se = (1 / sum((lambda pr: pr * (1 - pr))(1 / (1 + math.exp(-(w[0] + w[1] * lm + w[2] * z(fx))))) * z(fx) ** 2
                  for lm, fx, y in tr)) ** 0.5
    return {"n": len(data), "test": len(te), "coefFactor": round(w[2], 4), "se": round(se, 4),
            "tStat": round(w[2] / se, 2) if se else None, "coefMarket": round(w[1], 3),
            "logloss": {"marketAlone": round(base, 5), "refitMarket": round(without, 5), "plusFactor": round(with_f, 5)},
            "improvementVsMarket": round(base - with_f, 5)}


if __name__ == "__main__":
    rows = build(load())
    res = {"matches": len(rows), "source": "football-data.co.uk with Pinnacle closing 1X2, de-margined",
           "method": "logistic regression of a home win on logit(market) + one factor, fitted on the first 70% by "
                     "date and scored on the last 30%; a positive improvement means the factor adds something the "
                     "market had not priced"}
    for k in ("h2h", "form", "table", "gdiff"):
        res[k] = fit(rows, k)
        print(k, json.dumps(res[k]))
    json.dump(res, open(os.path.join(HERE, "extra_factors.json"), "w"), indent=1)
