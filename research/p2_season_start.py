"""Experiment S (season-start translation) through the LIVE code path: model12.goal_rates(variant=...).

Same data, fits and movers as p2_candidates.py.

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


M.PARAMS.update({"alpha_elo": alpha, "delta_elo": delta})


def team_p2(lg, s, t):
    """The live p2 team block: prev carries its division and that division's season-start mean Elo."""
    mv = movers.get((lg, s, t))
    if mv is not None:
        a_old, d_old = idx[(mv["from"], s - 1, t)]
        return {"league": lg, "promoted": mv["up"],   # relegated = changed division and not promoted
                "prev": {"attack": a_old, "defence": d_old, "league": mv["from"], "leagueEloMean": emean[(mv["from"], s - 1)]}}
    p = idx.get((lg, s - 1, t))
    if p:
        return {"league": lg, "promoted": False, "prev": {"attack": p[0], "defence": p[1], "league": lg}}
    return {"league": lg, "promoted": (DIV2C[lg], s - 1, t) in prevdiv, "prev": None}


VARIANTS = ("base", "B", "S", "H")
hist = defaultdict(list)
roll = defaultdict(list)
count = defaultdict(int)
rows = []
ident = []
for m in matches:
    lg, d, s = m["lg"], m["d"], m["s"]
    kh, ka = (lg, s, m["h"]), (lg, s, m["a"])
    inv_ = [k for k in (kh, ka) if k in movers and count[k] < 10]
    recent = [(x, y) for (dd, x, y) in roll[lg] if 0 < (d - dd).days <= 365]
    if inv_ and len(recent) >= 100:
        mu_h = sum(x for x, _ in recent) / len(recent); mu_a = sum(y for _, y in recent) / len(recent)
        p2 = {"date": d.isoformat(),
              "leagues": {lg: {"mu_home": mu_h, "mu_away": mu_a, "eloMeanSeasonStart": emean[(lg, s)]}},
              "home": dict(team_p2(lg, s, m["h"]), history=hist[kh]), "away": dict(team_p2(lg, s, m["a"]), history=hist[ka])}
        r = 0 if m["gh"] > m["ga"] else 1 if m["gh"] == m["ga"] else 2
        out = {"season": s, "up": any(movers[k]["up"] for k in inv_), "gap": max(abs(movers[k]["gap"]) for k in inv_)}
        for v in VARIANTS:
            lh, la, _ = M.goal_rates(p2, {"variant": v, "half_life": 120, "k": 10})
            out[v] = -math.log(max(M._p1x2(lh, la)[r], 1e-9))
        if m["odds"]:
            iv = [1 / o for o in m["odds"]]
            out["market"] = -math.log(iv[r] / sum(iv))
        rows.append(out)
    if not any(k in movers for k in (kh, ka)) and s >= EVAL_FROM and len(recent) >= 100 and len(ident) < 20000:
        mu_h = sum(x for x, _ in recent) / len(recent); mu_a = sum(y for _, y in recent) / len(recent)
        p2 = {"date": d.isoformat(),
              "leagues": {lg: {"mu_home": mu_h, "mu_away": mu_a, "eloMeanSeasonStart": emean.get((lg, s))}},
              "home": dict(team_p2(lg, s, m["h"]), history=hist[kh]), "away": dict(team_p2(lg, s, m["a"]), history=hist[ka])}
        ident.append(M.goal_rates(p2, {"variant": "H"})[:2] == M.goal_rates(p2, {"variant": "base"})[:2])
    for k in inv_:
        count[k] += 1
    hist[kh].append({"date": d.isoformat(), "venue": "H", "xgf": m["gh"], "xga": m["ga"]})
    hist[ka].append({"date": d.isoformat(), "venue": "A", "xgf": m["ga"], "xga": m["gh"]})
    roll[lg].append((d, m["gh"], m["ga"]))


def ci(dd):
    n = len(dd); mu = sum(dd) / n; sd = math.sqrt(sum((x - mu) ** 2 for x in dd) / (n - 1))
    return {"mean": round(mu, 4), "lo95": round(mu - 1.96 * sd / math.sqrt(n), 4), "hi95": round(mu + 1.96 * sd / math.sqrt(n), 4), "n": n}


def block(rs):
    mk = [r for r in rs if "market" in r]
    return {"logloss": {v: round(sum(r[v] for r in rs) / len(rs), 4) for v in VARIANTS},
            "S_minus_base": ci([r["S"] - r["base"] for r in rs]), "S_minus_B": ci([r["S"] - r["B"] for r in rs]),
            "B_minus_base": ci([r["B"] - r["base"] for r in rs]),
            "S_minus_market": ci([r["S"] - r["market"] for r in mk]), "base_minus_market": ci([r["base"] - r["market"] for r in mk])}


prom = [r for r in rows if r["up"]]; rel = [r for r in rows if not r["up"]]


def hblock(rs):
    mk = [r for r in rs if "market" in r]
    return {"n": len(rs), "logloss": {v: round(sum(r[v] for r in rs) / len(rs), 4) for v in ("base", "S", "H")},
            "H_minus_base": ci([r["H"] - r["base"] for r in rs]), "H_minus_S": ci([r["H"] - r["S"] for r in rs]),
            "H_minus_market": ci([r["H"] - r["market"] for r in mk]), "base_minus_market": ci([r["base"] - r["market"] for r in mk])}


def split(rs, key):
    out = {}
    for kv in sorted({key(r) for r in rs}):
        sub = [r for r in rs if key(r) == kv]
        if len(sub) >= 20:
            out[str(kv)] = ci([r["H"] - r["base"] for r in sub])
    return out


gapband = lambda r: "100-200" if r["gap"] < 200 else "200-300" if r["gap"] < 300 else "300+"
hres = {"relegated": hblock(rel), "promoted": hblock(prom), "allMovers": hblock(rows),
        "relegatedBySeason": split(rel, lambda r: r["season"]), "promotedBySeason": split(prom, lambda r: r["season"]),
        "allBySeason": split(rows, lambda r: r["season"]),
        "relegatedByGap": split(rel, gapband), "promotedByGap": split(prom, gapband),
        "identityNonMovers": {"matchesChecked": len(ident), "identical": sum(ident)}}
json.dump(hres, open(os.path.join(ROOT, "research", "p2_hybrid.json"), "w"), indent=1)
result = {"alpha": alpha, "delta": delta, "movers": len(movers), "promotedTeams": sum(1 for v in movers.values() if v["up"]),
          "relegatedTeams": sum(1 for v in movers.values() if not v["up"]), "matches": len(rows),
          "all": block(rows), "promoted": block(prom), "relegated": block(rel),
          "relegatedBySeason": {str(s): ci([r["S"] - r["base"] for r in rel if r["season"] == s]) for s in sorted({r["season"] for r in rel})},
          "promotedBySeason": {str(s): ci([r["S"] - r["base"] for r in prom if r["season"] == s]) for s in sorted({r["season"] for r in prom})},
          "byGap": {}}
for lo, hi in ((0, 100), (100, 200), (200, 1000)):
    for name, grp in (("promoted", prom), ("relegated", rel)):
        g = [r for r in grp if lo <= r["gap"] < hi]
        if len(g) > 30:
            result["byGap"][f"{name} {lo}-{hi}"] = ci([r["S"] - r["base"] for r in g])
json.dump(result, open(os.path.join(ROOT, "research", "p2_season_start.json"), "w"), indent=1)
print(json.dumps({k: result[k] for k in ("movers", "promotedTeams", "relegatedTeams", "matches", "promoted", "relegated", "byGap")}, indent=1))
print(json.dumps({"relegatedBySeason": result["relegatedBySeason"], "promotedBySeason": result["promotedBySeason"]}))
