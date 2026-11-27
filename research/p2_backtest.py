"""P2 historical fit and walk-forward test (no look-ahead).

Data: xgabora/Club-Football-Match-Data-2000-2025 (MIT): football-data.co.uk results and odds,
ClubElo snapshots (1st and 15th of each month). Only ClubElo snapshots before 2025-06-01 are used
(later rows are the dataset author's provisional continuation, not ClubElo).

Pre-match Elo is rebuilt from the snapshot file: the latest snapshot dated STRICTLY BEFORE the match.
The dataset has no xG, so goals stand in for xG here (a limitation: goals are noisier than xG).

Outputs research/p2_results.json:
  - elo_fit: GD = c + b * dElo, fitted on training seasons only
  - promoted priors fitted on training seasons only
  - per-season log-loss (1X2) for every (half_life, k), the market, and the walk-forward choice
  - domestic Elo-weight comparison (diagnostic only; production weight stays 0)
"""
import bisect, csv, json, math, os, sys
from collections import defaultdict
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import model12 as M  # noqa: E402

DATA = sys.argv[1]
LEAGUES = ["E0", "E1", "SP1", "I1", "D1", "F1"]
FIRST_SEASON, LAST_SEASON = 2014, 2024          # season = starting year
ELO_CUTOFF = date(2025, 6, 1)
HALF_LIVES, KS = [90, 120, 180, 240], [5, 10, 15, 20]
W_GRID = [0.0, 0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.75, 1.0]
RHO = -0.06


def season_of(d):
    return d.year if d.month >= 7 else d.year - 1


# ---------------------------------------------------------------- Elo snapshots
snap = defaultdict(list)
with open(os.path.join(DATA, "EloRatings.csv")) as f:
    for r in csv.DictReader(f):
        d = date.fromisoformat(r["date"])
        if d < ELO_CUTOFF and r["elo"]:
            snap[r["club"]].append((d, float(r["elo"])))
for v in snap.values():
    v.sort()
snap_dates = {k: [d for d, _ in v] for k, v in snap.items()}


def elo_before(club, d):
    ds = snap_dates.get(club)
    if not ds:
        return None
    i = bisect.bisect_left(ds, d) - 1          # strictly before d
    if i < 0 or (d - ds[i]).days > 31:
        return None
    return snap[club][i][1]


# ---------------------------------------------------------------- matches
matches = []
with open(os.path.join(DATA, "Matches.csv")) as f:
    for r in csv.DictReader(f):
        if r["Division"] not in LEAGUES or not r["FTHome"] or not r["FTAway"]:
            continue
        d = date.fromisoformat(r["MatchDate"])
        s = season_of(d)
        if s < FIRST_SEASON - 1 or s > LAST_SEASON or d >= ELO_CUTOFF:
            continue
        try:
            odds = [float(r["OddHome"]), float(r["OddDraw"]), float(r["OddAway"])]
            if min(odds) <= 1.0:
                odds = None
        except ValueError:
            odds = None
        matches.append({"lg": r["Division"], "d": d, "s": s, "h": r["HomeTeam"], "a": r["AwayTeam"],
                        "gh": int(float(r["FTHome"])), "ga": int(float(r["FTAway"])), "odds": odds,
                        "eh": elo_before(r["HomeTeam"], d), "ea": elo_before(r["AwayTeam"], d)})
matches.sort(key=lambda m: m["d"])
print("matches", len(matches), file=sys.stderr)

# season aggregates per (league, season)
agg = defaultdict(lambda: {"gh": 0, "ga": 0, "n": 0})
team_season = defaultdict(lambda: {"gf": 0.0, "gaa": 0.0, "muf": 0.0, "mua": 0.0, "n": 0})
for m in matches:
    a = agg[(m["lg"], m["s"])]
    a["gh"] += m["gh"]; a["ga"] += m["ga"]; a["n"] += 1
for m in matches:
    a = agg[(m["lg"], m["s"])]
    mh, ma = a["gh"] / a["n"], a["ga"] / a["n"]
    for team, gf, gaa, muf, mua in ((m["h"], m["gh"], m["ga"], mh, ma), (m["a"], m["ga"], m["gh"], ma, mh)):
        t = team_season[(m["lg"], m["s"], team)]
        t["gf"] += gf; t["gaa"] += gaa; t["muf"] += muf; t["mua"] += mua; t["n"] += 1


def prev_index(lg, s, team):
    t = team_season.get((lg, s - 1, team))
    if not t or t["n"] < 10:
        return None
    return {"attack": t["gf"] / t["muf"], "defence": t["gaa"] / t["mua"]}


# ---------------------------------------------------------------- fits on TRAINING seasons only
TRAIN_END = 2017      # seasons <= 2017/18 fit the Elo mapping and promoted priors
elo_rows = [(m["eh"] - m["ea"], m["gh"] - m["ga"]) for m in matches
            if m["s"] <= TRAIN_END and m["eh"] is not None and m["ea"] is not None]
elo_fit = M.fit_elo_gd(elo_rows)
prom_a, prom_d = [], []
for (lg, s, team), t in team_season.items():
    if s <= TRAIN_END and s - 1 >= FIRST_SEASON - 1 and (lg, s - 1) in agg and (lg, s - 1, team) not in team_season and t["n"] >= 10:
        prom_a.append(t["gf"] / t["muf"]); prom_d.append(t["gaa"] / t["mua"])
promoted = {"attack": sum(prom_a) / len(prom_a), "defence": sum(prom_d) / len(prom_d), "n": len(prom_a)}
print("elo", elo_fit, "promoted", promoted, file=sys.stderr)


# ---------------------------------------------------------------- fast 1X2 from the DC grid
def pmf(lam, n=11):
    out, p = [], math.exp(-lam)
    for k in range(n):
        out.append(p)
        p *= lam / (k + 1)
    return out


def probs_1x2(lh, la):
    ph_, pa_ = pmf(lh), pmf(la)
    g = [[ph_[i] * pa_[j] for j in range(11)] for i in range(11)]
    g[0][0] *= 1 - lh * la * RHO; g[0][1] *= 1 + lh * RHO; g[1][0] *= 1 + la * RHO; g[1][1] *= 1 - RHO
    s = sum(map(sum, g))
    H = sum(g[i][j] for i in range(11) for j in range(11) if i > j) / s
    D = sum(g[i][i] for i in range(11)) / s
    return H, D, 1 - H - D


def ll(p3, res):
    return -math.log(max(p3[{"H": 0, "D": 1, "A": 2}[res]], 1e-9))


# ---------------------------------------------------------------- walk-forward evaluation
hist = defaultdict(list)          # (league, season, team) -> [{date, venue, xgf, xga}]
roll = defaultdict(list)          # league -> [(date, gh, ga)] for rolling league means
EVAL_FROM = TRAIN_END + 1        # scored seasons never overlap the fitting seasons
per = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))   # cfg -> season -> [sum ll, n]
mkt = defaultdict(lambda: [0.0, 0])
base11 = defaultdict(lambda: [0.0, 0])
paired = []   # per match: (ll 1.2 at 120/10, ll 1.1, ll market)
w_dom = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
for m in matches:
    lg, d, s = m["lg"], m["d"], m["s"]
    res = "H" if m["gh"] > m["ga"] else "D" if m["gh"] == m["ga"] else "A"
    recent = [(x, y) for (dd, x, y) in roll[lg] if 0 < (d - dd).days <= 365]
    ready = s >= EVAL_FROM and len(recent) >= 150 and m["odds"]
    if ready:
        mu_h = sum(x for x, _ in recent) / len(recent)
        mu_a = sum(y for _, y in recent) / len(recent)
        p2 = {"date": d.isoformat(), "leagues": {lg: {"mu_home": mu_h, "mu_away": mu_a}}}
        for side, team in (("home", m["h"]), ("away", m["a"])):
            prev = prev_index(lg, s, team)
            promoted_flag = prev is None and (lg, s - 1) in agg
            p2[side] = {"league": lg, "history": hist[(lg, s, team)], "prev": prev, "promoted": promoted_flag,
                        "elo": m["eh"] if side == "home" else m["ea"]}
        M.PARAMS.update({"promoted_attack": promoted["attack"], "promoted_defence": promoted["defence"],
                         "elo_b": elo_fit["b"], "elo_c": elo_fit["c"]})
        for hl in HALF_LIVES:
            for k in KS:
                lh, la, _ = M.goal_rates(p2, {"half_life": hl, "k": k, "w_domestic": 0.0})
                v = ll(probs_1x2(lh, la), res)
                c = per[(hl, k)][s]; c[0] += v; c[1] += 1
        if m["eh"] is not None and m["ea"] is not None:
            for w in W_GRID:
                lh, la, _ = M.goal_rates(p2, {"half_life": 120, "k": 10, "w_domestic": w})
                c = w_dom[w][s]; c[0] += ll(probs_1x2(lh, la), res); c[1] += 1
        # model 1.1 formula on the same information: season-to-date goals per game, sqrt(gf*ga) * home_adv
        def std(team):
            h_ = hist[(lg, s, team)]
            if not h_:
                return (mu_h + mu_a) / 2, (mu_h + mu_a) / 2
            return sum(x["xgf"] for x in h_) / len(h_), sum(x["xga"] for x in h_) / len(h_)
        (gfh, gah), (gfa, gaa) = std(m["h"]), std(m["a"])
        lh11 = math.sqrt(max(gfh, 0.05) * max(gaa, 0.05)) * 1.05
        la11 = math.sqrt(max(gfa, 0.05) * max(gah, 0.05)) / 1.05
        v11 = ll(probs_1x2(lh11, la11), res)
        c = base11[s]; c[0] += v11; c[1] += 1
        lh12, la12, _ = M.goal_rates(p2, {"half_life": 120, "k": 10, "w_domestic": 0.0})
        inv_ = [1 / o for o in m["odds"]]
        vm = ll([x / sum(inv_) for x in inv_], res)
        if s > EVAL_FROM:
            paired.append((ll(probs_1x2(lh12, la12), res), v11, vm))
        inv = [1 / o for o in m["odds"]]
        pm = [x / sum(inv) for x in inv]
        c = mkt[s]; c[0] += ll(pm, res); c[1] += 1
    # only after predicting: add the match to the histories (no look-ahead)
    hist[(lg, s, m["h"])].append({"date": d.isoformat(), "venue": "H", "xgf": m["gh"], "xga": m["ga"]})
    hist[(lg, s, m["a"])].append({"date": d.isoformat(), "venue": "A", "xgf": m["ga"], "xga": m["gh"]})
    roll[lg].append((d, m["gh"], m["ga"]))

seasons = sorted({s for c in per.values() for s in c})
table = {f"{hl}/{k}": {str(s): per[(hl, k)][s][0] / per[(hl, k)][s][1] for s in seasons} for hl, k in per}
market = {str(s): mkt[s][0] / mkt[s][1] for s in seasons}
n_per_season = {str(s): mkt[s][1] for s in seasons}
# walk-forward: choose on all earlier evaluated seasons, score the next one
wf = []
for i, s in enumerate(seasons[1:], start=1):
    past = seasons[:i]
    best = min(per, key=lambda c: sum(per[c][p][0] for p in past) / sum(per[c][p][1] for p in past))
    wf.append({"season": s, "chosen": f"{best[0]}/{best[1]}", "model": per[best][s][0] / per[best][s][1],
               "default_120_10": per[(120, 10)][s][0] / per[(120, 10)][s][1],
               "model11": base11[s][0] / base11[s][1], "market": market[str(s)]})
wdom = {str(w): sum(w_dom[w][s][0] for s in seasons) / sum(w_dom[w][s][1] for s in seasons) for w in W_GRID}
def ci(diffs):
    n = len(diffs); mu = sum(diffs) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in diffs) / (n - 1))
    return {"mean": mu, "lo95": mu - 1.96 * sd / math.sqrt(n), "hi95": mu + 1.96 * sd / math.sqrt(n), "n": n}
diff_ci = {"m12_minus_m11": ci([a - b for a, b, _ in paired]), "m12_minus_market": ci([a - c for a, _, c in paired]),
           "m11_minus_market": ci([b - c for _, b, c in paired])}
out = {"paired_ci_seasons_after_selection_start": diff_ci, "data": "xgabora Club-Football-Match-Data (football-data.co.uk + ClubElo), goals as xG proxy",
       "leagues": LEAGUES, "train_end_season": TRAIN_END, "elo_fit": elo_fit, "promoted_prior": promoted,
       "matches_per_season": n_per_season, "logloss_by_config": table, "market": market,
       "walk_forward": wf, "domestic_elo_weight_diagnostic": wdom}
json.dump(out, open(os.path.join(ROOT, "research", "p2_results.json"), "w"), indent=1)
print(json.dumps({k: out[k] for k in ("elo_fit", "promoted_prior", "walk_forward", "domestic_elo_weight_diagnostic")}, indent=1))
