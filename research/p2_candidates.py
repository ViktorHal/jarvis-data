"""Candidates A and B: fit B's elasticities and run the transfer test (no look-ahead).

Data: xgabora/Club-Football-Match-Data (MIT). ClubElo snapshots before 2025-06-01 only.
Goals stand in for xG. The data has no cross-league matches, so nothing here validates either
candidate for cross-league (e.g. Champions League) matches.

1. alpha/delta: within each division-season, regress ln(attack index) and ln(defence index) on
   (team Elo at season start - division mean Elo). Training seasons <= 2017/18 only.
2. Transfer test: teams that changed division between seasons (promotion/relegation) in 2018/19-2024/25.
   Their first 10 matches in the new division are predicted with the team's prior taken from its
   previous-season indices in the OLD division:
     m12  : model 1.2 as specified (promoted -> fitted constant prior; relegated -> old indices, untranslated)
     naive: old indices, untranslated, for both directions
     B    : old indices translated with exp(+-alpha/delta * (Elo_old_div - Elo_new_div))
   All three regress the prior one third to the mean. Opponents and everything else are identical.
"""
import bisect, csv, json, math, os, sys
from collections import defaultdict
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import model12 as M  # noqa: E402

DATA = sys.argv[1]
GROUPS = {"ENG": ["E0", "E1", "E2", "E3"], "ESP": ["SP1", "SP2"], "ITA": ["I1", "I2"], "GER": ["D1", "D2"],
          "FRA": ["F1", "F2"], "SCO": ["SC0", "SC1", "SC2", "SC3"]}
DIV2C = {d: c for c, ds in GROUPS.items() for d in ds}
TRAIN_END, EVAL_FROM, LAST = 2017, 2018, 2024
ELO_CUTOFF = date(2025, 6, 1)


def season_of(d):
    return d.year if d.month >= 7 else d.year - 1


snap = defaultdict(list)
with open(os.path.join(DATA, "EloRatings.csv")) as f:
    for r in csv.DictReader(f):
        d = date.fromisoformat(r["date"])
        if d < ELO_CUTOFF and r["elo"]:
            snap[r["club"]].append((d, float(r["elo"])))
for v in snap.values():
    v.sort()
sdates = {k: [d for d, _ in v] for k, v in snap.items()}


def elo_before(club, d, max_age=31):
    ds = sdates.get(club)
    if not ds:
        return None
    i = bisect.bisect_left(ds, d) - 1
    if i < 0 or (d - ds[i]).days > max_age:
        return None
    return snap[club][i][1]


matches = []
with open(os.path.join(DATA, "Matches.csv")) as f:
    for r in csv.DictReader(f):
        if r["Division"] not in DIV2C or not r["FTHome"] or not r["FTAway"]:
            continue
        d = date.fromisoformat(r["MatchDate"])
        s = season_of(d)
        if s < 2012 or s > LAST or d >= ELO_CUTOFF:
            continue
        try:
            odds = [float(r["OddHome"]), float(r["OddDraw"]), float(r["OddAway"])]
            odds = odds if min(odds) > 1 else None
        except ValueError:
            odds = None
        matches.append({"lg": r["Division"], "d": d, "s": s, "h": r["HomeTeam"], "a": r["AwayTeam"],
                        "gh": int(float(r["FTHome"])), "ga": int(float(r["FTAway"])), "odds": odds})
matches.sort(key=lambda m: m["d"])

# division-season: first date, teams, venue means, team season indices, Elo at season start
first = {}
teams = defaultdict(set)
agg = defaultdict(lambda: [0, 0, 0])
for m in matches:
    k = (m["lg"], m["s"])
    first.setdefault(k, m["d"])
    teams[k].update((m["h"], m["a"]))
    a = agg[k]; a[0] += m["gh"]; a[1] += m["ga"]; a[2] += 1
ts = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0])
for m in matches:
    a = agg[(m["lg"], m["s"])]; mh, ma = a[0] / a[2], a[1] / a[2]
    for t, gf, ga_, mf, mg in ((m["h"], m["gh"], m["ga"], mh, ma), (m["a"], m["ga"], m["gh"], ma, mh)):
        x = ts[(m["lg"], m["s"], t)]; x[0] += gf; x[1] += ga_; x[2] += mf; x[3] += mg; x[4] += 1
idx = {k: (v[0] / v[2], v[1] / v[3]) for k, v in ts.items() if v[4] >= 10 and v[0] > 0 and v[1] > 0}
elo0 = {}
emean = {}
for k, tset in teams.items():
    vals = []
    for t in tset:
        e = elo_before(t, first[k], max_age=45)
        if e is not None:
            elo0[(k[0], k[1], t)] = e
            vals.append(e)
    if len(vals) >= 0.8 * len(tset):
        emean[k] = sum(vals) / len(vals)

# 1. fit alpha, delta on training seasons
xa, ya, yd = [], [], []
for (lg, s, t), (ai, di) in idx.items():
    if s <= TRAIN_END and (lg, s) in emean and (lg, s, t) in elo0:
        x = elo0[(lg, s, t)] - emean[(lg, s)]
        xa.append(x); ya.append(math.log(ai)); yd.append(math.log(di))


def ols(x, y):
    n = len(x); mx = sum(x) / n; my = sum(y) / n
    sxx = sum((u - mx) ** 2 for u in x); b = sum((u - mx) * (v - my) for u, v in zip(x, y)) / sxx
    c = my - b * mx; se = math.sqrt(sum((v - c - b * u) ** 2 for u, v in zip(x, y)) / (n - 2) / sxx)
    return b, c, se, n


alpha, a0, se_a, n_a = ols(xa, ya)
dslope, d0, se_d, _ = ols(xa, yd)
delta = -dslope
print("alpha", alpha, se_a, "delta", delta, se_d, "n", n_a, file=sys.stderr)
promoted = {"attack": 0.838, "defence": 1.108}
M.PARAMS.update({"promoted_attack": promoted["attack"], "promoted_defence": promoted["defence"]})

# 2. transfer test
prevdiv = {}
for (lg, s, t) in ts:
    prevdiv.setdefault((DIV2C[lg], s, t), lg)
movers = {}
for (lg, s, t) in ts:
    if EVAL_FROM <= s <= LAST:
        pd = prevdiv.get((DIV2C[lg], s - 1, t))
        if pd and pd != lg and (pd, s - 1, t) in idx and (pd, s - 1) in emean and (lg, s) in emean:
            up = GROUPS[DIV2C[lg]].index(lg) < GROUPS[DIV2C[lg]].index(pd)
            movers[(lg, s, t)] = {"from": pd, "up": up, "gap": emean[(pd, s - 1)] - emean[(lg, s)]}
print("movers", len(movers), file=sys.stderr)


def prior_for(lg, s, t, variant):
    mv = movers.get((lg, s, t))
    if mv is None:
        p = idx.get((lg, s - 1, t))
        if p:
            return {"prev": {"attack": p[0], "defence": p[1]}, "promoted": False}
        return {"prev": None, "promoted": (DIV2C[lg], s - 1, t) in prevdiv}
    a_old, d_old = idx[(mv["from"], s - 1, t)]
    if variant == "m12":
        return {"prev": None, "promoted": True} if mv["up"] else {"prev": {"attack": a_old, "defence": d_old}, "promoted": False}
    if variant == "naive":
        return {"prev": {"attack": a_old, "defence": d_old}, "promoted": False}
    g = mv["gap"]   # old division mean Elo - new division mean Elo
    return {"prev": {"attack": a_old * math.exp(alpha * g), "defence": d_old * math.exp(-delta * g)}, "promoted": False}


def p1x2(lh, la):
    return M._p1x2(lh, la)


hist = defaultdict(list)
roll = defaultdict(list)
count = defaultdict(int)
res_rows = []
for m in matches:
    lg, d, s = m["lg"], m["d"], m["s"]
    keyh, keya = (lg, s, m["h"]), (lg, s, m["a"])
    involved = [k for k in (keyh, keya) if k in movers and count[k] < 10]
    recent = [(x, y) for (dd, x, y) in roll[lg] if 0 < (d - dd).days <= 365]
    if involved and len(recent) >= 100:
        mu_h = sum(x for x, _ in recent) / len(recent); mu_a = sum(y for _, y in recent) / len(recent)
        out = {}
        for v in ("m12", "naive", "B"):
            p2 = {"date": d.isoformat(), "leagues": {lg: {"mu_home": mu_h, "mu_away": mu_a}},
                  "home": dict(league=lg, history=hist[keyh], **prior_for(lg, s, m["h"], v)),
                  "away": dict(league=lg, history=hist[keya], **prior_for(lg, s, m["a"], v))}
            lh, la, _ = M.goal_rates(p2, {"half_life": 120, "k": 10})
            pr = p1x2(lh, la)
            r = 0 if m["gh"] > m["ga"] else 1 if m["gh"] == m["ga"] else 2
            out[v] = -math.log(max(pr[r], 1e-9))
        if m["odds"]:
            inv = [1 / o for o in m["odds"]]; r = 0 if m["gh"] > m["ga"] else 1 if m["gh"] == m["ga"] else 2
            out["market"] = -math.log(inv[r] / sum(inv))
        out["up"] = any(movers[k]["up"] for k in involved)
        res_rows.append(out)
    for k in involved:
        count[k] += 1
    hist[keyh].append({"date": d.isoformat(), "venue": "H", "xgf": m["gh"], "xga": m["ga"]})
    hist[keya].append({"date": d.isoformat(), "venue": "A", "xgf": m["ga"], "xga": m["gh"]})
    roll[lg].append((d, m["gh"], m["ga"]))


def ci(dd):
    n = len(dd); mu = sum(dd) / n; sd = math.sqrt(sum((x - mu) ** 2 for x in dd) / (n - 1))
    return {"mean": round(mu, 4), "lo95": round(mu - 1.96 * sd / math.sqrt(n), 4), "hi95": round(mu + 1.96 * sd / math.sqrt(n), 4), "n": n}


def summary(rows):
    out = {v: round(sum(r[v] for r in rows) / len(rows), 4) for v in ("m12", "naive", "B")}
    mk = [r for r in rows if "market" in r]
    out.update({"B_minus_m12": ci([r["B"] - r["m12"] for r in rows]), "B_minus_naive": ci([r["B"] - r["naive"] for r in rows]),
                "naive_minus_m12": ci([r["naive"] - r["m12"] for r in rows]),
                "B_minus_market": ci([r["B"] - r["market"] for r in mk]), "m12_minus_market": ci([r["m12"] - r["market"] for r in mk])})
    return out


result = {"alpha": alpha, "alpha_se": se_a, "delta": delta, "delta_se": se_d, "fit_n": n_a,
          "movers": len(movers), "matches_scored": len(res_rows),
          "all": summary(res_rows), "promoted": summary([r for r in res_rows if r["up"]]),
          "relegated": summary([r for r in res_rows if not r["up"]])}
# league mean Elo, last real ClubElo snapshot (for the worked example)
last = date(2025, 5, 31)
for lg in ("E0", "P1", "I1", "D1", "SP1", "F1", "B1", "N1"):
    ts_ = set()
with open(os.path.join(DATA, "Matches.csv")) as f:
    tl = defaultdict(set)
    for r in csv.DictReader(f):
        if r["MatchDate"] >= "2024-08-01" and r["MatchDate"] < "2025-06-01":
            tl[r["Division"]].update((r["HomeTeam"], r["AwayTeam"]))
result["leagueEloMean_2025_05"] = {}
for lg in ("E0", "P1", "I1", "D1", "SP1", "F1", "B1", "N1", "T1"):
    v = [e for e in (elo_before(t, date(2025, 6, 1), max_age=60) for t in tl[lg]) if e is not None]
    if v:
        result["leagueEloMean_2025_05"][lg] = round(sum(v) / len(v), 1)
json.dump(result, open(os.path.join(ROOT, "research", "p2_candidates.json"), "w"), indent=1)
print(json.dumps(result, indent=1))
