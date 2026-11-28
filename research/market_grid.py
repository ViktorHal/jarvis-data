"""Can Pinnacle's own prices fix the goal lines Pinnacle does not quote? (27 Nov 2026)

The market anchor (decision.py 3.8) replaced Jarvis' probability with Pinnacle's wherever Pinnacle prices a
complete market. That left a hole: Pinnacle quotes 1X2 and the totals around 2.5-3.5, so "over 1.5 goals",
"both teams to score", "a team to score" and every bet-builder combination were still priced by the model - in
the very same match where the model's view of over 2.5 had been overruled. Either the model knows this match's
goal rates or it does not.

The alternative, now decision.py 3.10: fit the two goal rates of the Dixon-Coles grid to Pinnacle's de-margined
1X2 and totals (two parameters, four or more numbers) and let that grid price the rest. The grid also carries one
global extra parameter, cv, the spread the market's own goal distribution has over independent Poisson.

  TEST 1  (today's Pinnacle feed) how close does the fitted grid come to Pinnacle's own de-margined price at the
          half-lines it was NOT fitted to, and what does cv do? Whole lines (2, 3) and quarter lines (2.25) are
          excluded: those prices are pushes and splits, not plain overs.
  TEST 1b (the same lines) how far is Jarvis' own model from Pinnacle on them?
  TEST 2  (football-data history: Pinnacle closing prices and real results) fit to the closing 1X2 and 2.5 total,
          then score the fitted grid on outcomes it was not fitted to - over 1.5, over 3.5, both teams to score,
          home team over 1.5 - against the base rate of each.

This script calls the production functions in jarvis/decision.py, so it tests the code that actually runs.

usage: python3 research/market_grid.py [--quick]     N=<matches> limits test 2 (default 800)
"""
import csv
import glob
import json
import math
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "jarvis"))
import decision as D  # noqa: E402
import model  # noqa: E402

HIST = "/home/claude/hist/history/football"
RHO = model.P["dc_rho"]
CV = D.DEFAULT_CONFIG["marketGrid"]["cv"]


def over(tot, line):
    return sum(p for k, p in tot.items() if k > line)


def half_lines(totals):
    """Only x.5 lines: a whole line is a push and a quarter line is two bets in one."""
    return {L: p for L, p in totals.items() if abs(L * 2 - round(L * 2)) < 1e-9 and abs(L - round(L)) > 1e-9}


def pinnacle_football():
    """[(novig 1X2, {half-line: over probability})] from the robot's own odds file."""
    p = os.path.join(ROOT, "data", "pinnacle", "odds.json")
    if not os.path.exists(p):
        return []
    out = []
    for e in json.load(open(p)).get("events") or []:
        lg = str(e.get("league") or "")
        if lg.endswith(" Corners") or lg.endswith(" Bookings"):
            continue
        p0 = (e.get("periods") or {}).get("num_0") or {}
        ml, tt = p0.get("money_line") or {}, p0.get("totals") or {}
        if not all(isinstance(ml.get(k), (int, float)) for k in ("home", "draw", "away")):
            continue
        q = {}
        for k, v in tt.items():
            try:
                L = float(k)
            except ValueError:
                continue
            if isinstance(v, dict) and v.get("over") and v.get("under"):
                q[L] = D.novig([v["over"], v["under"]])[0]
        q = half_lines(q)
        if 2.5 in q and len(q) >= 2:
            out.append((D.novig([ml["home"], ml["draw"], ml["away"]]), q))
    return out


def test_today(cvs=(0.0, 0.15, 0.25, 0.35), fit_lines=(2.5,)):
    rows = pinnacle_football()
    out = {"matches": len(rows), "fittedTo": list(fit_lines), "byCv": {}}
    for cv in cvs:
        errs, res = {}, []
        for (h, d, a), q in rows:
            use = {L: q[L] for L in fit_lines if L in q}
            if not use:
                continue
            got = D.fit_market_grid(h, d, a, use, cv, RHO)
            if not got:
                continue
            res.append(got[2])
            _, _, _, tot = D._grid_1x2_totals(D.mixed_grid(got[0], got[1], cv, RHO))
            for L, p in q.items():
                if L in use:
                    continue
                errs.setdefault(L, []).append(100 * (over(tot, L) - p))
        out["byCv"][str(cv)] = {"fitRmsPP": round(100 * st.mean(res), 3),
                                **{f"over{L}": {"n": len(v), "meanPP": round(st.mean(v), 2),
                                                "meanAbsPP": round(st.mean(abs(x) for x in v), 2),
                                                "maxAbsPP": round(max(abs(x) for x in v), 2)}
                                   for L, v in sorted(errs.items()) if len(v) >= 20}}
    return out


def test_today_model():
    """The same lines, priced by Jarvis' own model from the robot's inputs (probModel on the anchored rows)."""
    p = os.path.join(ROOT, "data", "auto", "payload.json")
    if not os.path.exists(p):
        return {"error": "no data/auto/payload.json"}
    pay = json.load(open(p))
    res = D.run({k: v for k, v in pay.items() if k != "meta"})
    err = {}
    for m in res["matches"]:
        for r in m["markets"]:
            if r["group"] != "Total goals" or not r["selection"].startswith("Over"):
                continue
            if r.get("pMarket") is None or r.get("pMarketKind") != "novig":
                continue
            pm = r["probModel"] if r.get("probModel") is not None else r["prob"]
            err.setdefault(float(r["selection"].split()[1]), []).append(pm - r["pMarket"])
    return {f"over{L}": {"n": len(v), "meanPP": round(st.mean(v), 2),
                         "meanAbsPP": round(st.mean(abs(x) for x in v), 2),
                         "maxAbsPP": round(max(abs(x) for x in v), 2)}
            for L, v in sorted(err.items()) if len(v) >= 20}


# ---------------------------------------------------------------- test 2: against real outcomes
def _f(x):
    try:
        v = float(x)
        return v if v > 1 else None
    except (TypeError, ValueError):
        return None


def load_hist():
    rows = []
    for p in sorted(glob.glob(os.path.join(HIST, "*.csv"))):
        for r in csv.DictReader(open(p, encoding="utf-8-sig", errors="replace")):
            if not r.get("FTHG"):
                continue
            ml = [_f(r.get(k)) for k in ("PSCH", "PSCD", "PSCA")]
            ou = [_f(r.get(k)) for k in ("PC>2.5", "PC<2.5")]
            if not all(ml) or not all(ou):
                continue
            try:
                hg, ag = int(r["FTHG"]), int(r["FTAG"])
            except (TypeError, ValueError):
                continue
            rows.append((D.novig(ml), D.novig(ou)[0], hg, ag))
    return rows


def ll(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


MARKETS = {"over1.5": (lambda h, a: h + a > 1.5, lambda i, j: i + j > 1.5),
           "over3.5": (lambda h, a: h + a > 3.5, lambda i, j: i + j > 3.5),
           "btts": (lambda h, a: h > 0 and a > 0, lambda i, j: i > 0 and j > 0),
           "homeOver1.5": (lambda h, a: h > 1.5, lambda i, j: i > 1.5)}


def test_history(limit=800, cvs=(0.0, 0.25)):
    rows = load_hist()
    if not rows:
        return {"error": f"no history in {HIST}"}
    rows = rows[:: max(1, len(rows) // limit)]
    base = {k: st.mean(1 if f(h, a) else 0 for _, _, h, a in rows) for k, (f, _) in MARKETS.items()}
    acc = {cv: {k: [] for k in MARKETS} for cv in cvs}
    bacc = {k: [] for k in MARKETS}
    for (h, d, a), o25, hg, ag in rows:
        for cv in cvs:
            got = D.fit_market_grid(h, d, a, {2.5: o25}, cv, RHO)
            if not got:
                continue
            g = D.mixed_grid(got[0], got[1], cv, RHO)
            for k, (outc, pred) in MARKETS.items():
                acc[cv][k].append(ll(D.grid_prob(g, [pred]), 1 if outc(hg, ag) else 0))
        for k, (outc, _) in MARKETS.items():
            bacc[k].append(ll(base[k], 1 if outc(hg, ag) else 0))
    out = {"matches": len(rows), "baseRates": {k: round(v, 4) for k, v in base.items()}}
    for k in MARKETS:
        out[k] = {"baseRate": round(st.mean(bacc[k]), 5),
                  **{f"fittedGrid_cv{cv}": round(st.mean(acc[cv][k]), 5) for cv in cvs}}
    return out


if __name__ == "__main__":
    res = {"test1_fittedGridVsPinnaclesOtherLines": test_today(),
           "test1_fittedToTwoLines": test_today(cvs=(0.25,), fit_lines=(2.5, 3.5)),
           "test1b_jarvisModelVsPinnacleSameLines": test_today_model()}
    if "--quick" not in sys.argv:
        res["test2_outcomesNotFittedTo"] = test_history(int(os.environ.get("N", 800)))
    print(json.dumps(res, indent=1))
    with open(os.path.join(HERE, "market_grid.json"), "w") as f:
        json.dump(res, f, indent=1)
