"""J.A.R.V.I.S. shadow model 1.2 (P2: team strength), stdlib only.

SHADOW ONLY. Production stays model.py 1.1. This module receives the same pre-match inputs
plus the extra P2 fields below, and its probabilities are stored next to 1.1's for evaluation.
It never decides a posted bet.

Football (hockey falls through to 1.1 unchanged):

1. League-normalised indices with time decay and shrinkage, per team, from its own league's
   match log (date, venue, xgf, xga):
       w_i      = 0.5 ** (age_i / half_life)
       a_raw    = sum w_i * xgf_i / sum w_i * mu_v(i)           (mu_v = league mean xG at that venue)
       d_raw    = sum w_i * xga_i / sum w_i * mu_v'(i)          (v' = the opponent's venue)
       n_eff    = (sum w_i)^2 / sum w_i^2                        (Kish effective sample size)
       weight   = n_eff / (n_eff + k)
       a        = weight * a_raw + (1 - weight) * prior_a       (prior: see 2)
2. Priors: last season's index regressed one third to the league mean,
       prior = 1 + (1 - regress) * (prev_index - 1);
   promoted teams (or no history) use the league prior 1.0, or for promoted teams the
   conservative promoted prior fitted on history (params promoted_attack / promoted_defence).
3. Expected goals (xG basis):
       lam_h = mu_home * a_h * d_a,   lam_a = mu_away * a_a * d_h,   T = lam_h + lam_a
   neutral supremacy  S_xg = mu_n * (a_h d_a - a_a d_h),  mu_n = (mu_home + mu_away) / 2
   home component     HA   = (lam_h - lam_a) - S_xg            (home advantage, counted once)
   For a cross-league match the two leagues' means are averaged.
4. Elo supremacy, fitted on history with pre-match snapshots only:
       E[GD] = c + b * (elo_h - elo_a)      ->  S_elo = b * (elo_h - elo_a)   (neutral part)
5. Combination (cross-league only in this version; domestic weight 0):
       GD    = (1 - w) * S_xg + w * S_elo + HA
       lam_h = (T + GD) / 2,  lam_a = (T - GD) / 2   (total T preserved; floor 0.05)
6. Prices: the 1.1 pricing code (Dixon-Coles grid, NB corners/cards, Poisson props) is reused
   unchanged, fed the 1.2 goal rates.

Extra inputs per match (football):  "p2": {
   "home": {"league", "history": [{"date","venue":"H"|"A","xgf","xga"}],
            "prev": {"attack","defence"} or {"xgf_pg","xga_pg","league_xg_pg"}, plus for experiment S
                    {"league": last season's division, "leagueEloMean": its mean Elo at the start of last season} | null,
            "promoted": bool, "elo": float | null, "eloDate": "YYYY-MM-DD" (strictly before kick-off)},
   "away": {...}, "leagues": {"<league>": {"mu_home","mu_away", "eloMean" (candidate B, pre-match snapshot),
                                          "eloMeanSeasonStart" (experiment S)}}, "date": "YYYY-MM-DD" (kick-off date) }
"""
import json, math, os, sys
from datetime import date as _date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402  (1.1 pricing, unchanged)

VERSION = "1.2.3"   # 1.2.1: candidates A, B; 1.2.2: experiment S; 1.2.3: hybrid H. Base unchanged

PARAMS = {
    "half_life": 120.0,      # days (owner's starting value; grid 90/120/180/240)
    "k": 10.0,               # shrinkage constant (owner's starting value; grid 5/10/15/20)
    "regress": 1 / 3,        # share of last season's deviation removed between seasons
    "w_cross": 0.25,         # Elo weight on supremacy, cross-league matches (grid 0..0.5)
    "w_domestic": 0.0,       # Elo weight, domestic matches: zero until data supports it
    # Fitted 25 Nov 2026 on 11,852 domestic matches (seasons 2013/14-2017/18, six leagues),
    # pre-match ClubElo snapshots only: GD = 0.3608 + 0.005286 * dElo (se_b 0.0001).
    "elo_b": 0.005286,       # goals of supremacy per Elo point
    "elo_c": 0.3608,         # home intercept of the fit (not used in pricing: HA comes from xG)
    # Promoted teams' first-season indices, mean of 80 cases in the same training seasons.
    "promoted_attack": 0.838,
    "promoted_defence": 1.108,
    # Shadow candidates (owner, 25 Nov 2026). variant "base" = model 1.2 as specified.
    "variant": "base",       # "base" | "A" | "B" | "S" | "H" (S: season-start translation; H: S for relegated only)
    "w_cross_A": 0.75,       # candidate A working weight (provisional; grid 0..1.0 scored retrospectively)
    # Candidate B elasticities, fitted 25 Nov 2026 within division-seasons (1,283 team-seasons,
    # 2012/13-2017/18, England tiers 1-4, Spain/Italy/Germany/France tiers 1-2, Scotland tiers 1-4).
    "alpha_elo": 0.001934,   # d ln(attack) / d Elo (se 0.000066)
    "delta_elo": 0.001599,   # -d ln(defence) / d Elo (se 0.000066)
    "max_history_days": 730,
    "lam_floor": 0.05,
}
_pp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "params12.json")
if os.path.exists(_pp):
    try:
        PARAMS.update({k: v for k, v in json.load(open(_pp)).items() if k in PARAMS})
    except Exception as e:  # a bad params file must not stop a run
        print(f"params12.json ignored: {e}", file=sys.stderr)


def _d(s):
    return _date.fromisoformat(str(s)[:10])


def decay_weights(ages_days, half_life):
    return [0.5 ** (max(a, 0) / half_life) for a in ages_days]


def kish(w):
    s, s2 = sum(w), sum(x * x for x in w)
    return (s * s / s2) if s2 > 0 else 0.0


def team_indices(history, mu, asof, prior_a=1.0, prior_d=1.0, half_life=None, k=None, max_days=None):
    """history: [{date, venue 'H'|'A', xgf, xga}] strictly before `asof`. mu: {'H': mu_home, 'A': mu_away}.
    Returns (attack, defence, n_eff). Matches on or after `asof` are ignored (no look-ahead)."""
    half_life = PARAMS["half_life"] if half_life is None else half_life
    k = PARAMS["k"] if k is None else k
    max_days = PARAMS["max_history_days"] if max_days is None else max_days
    t = _d(asof)
    rows = [h for h in history if 0 < (t - _d(h["date"])).days <= max_days]
    if not rows:
        return prior_a, prior_d, 0.0
    ages = [(t - _d(h["date"])).days for h in rows]
    w = decay_weights(ages, half_life)
    num_a = sum(wi * h["xgf"] for wi, h in zip(w, rows))
    den_a = sum(wi * mu[h["venue"]] for wi, h in zip(w, rows))
    num_d = sum(wi * h["xga"] for wi, h in zip(w, rows))
    den_d = sum(wi * mu["A" if h["venue"] == "H" else "H"] for wi, h in zip(w, rows))
    n = kish(w)
    s = n / (n + k)
    return s * (num_a / den_a) + (1 - s) * prior_a, s * (num_d / den_d) + (1 - s) * prior_d, n


def season_prior(prev, regress=None):
    """prev: {"attack","defence"} indices, or {"xgf_pg","xga_pg","league_xg_pg"} raw figures from
    last season (league_xg_pg = the league's mean xG per team per game)."""
    regress = PARAMS["regress"] if regress is None else regress
    if not prev:
        return 1.0, 1.0
    if "attack" not in prev:
        mu = prev["league_xg_pg"]
        prev = {"attack": prev["xgf_pg"] / mu, "defence": prev["xga_pg"] / mu}
    return (1 + (1 - regress) * (prev["attack"] - 1), 1 + (1 - regress) * (prev["defence"] - 1))


def translated_prev(t, leagues, P):
    """Experiment S: a team whose previous season was in ANOTHER division gets its previous-season
    indices translated to the new division before the usual one-third regression:
        a' = a * exp(alpha * g),  d' = d * exp(-delta * g),  g = Elo_mean(old div) - Elo_mean(new div)
    (old division's mean Elo at the start of last season; new division's at the start of this season).
    Returns translated {"attack","defence"} or None when the inputs are missing."""
    prev = t.get("prev") or {}
    old, cur = prev.get("league"), t.get("league")
    e_old = prev.get("leagueEloMean")
    e_new = (leagues.get(cur) or {}).get("eloMeanSeasonStart")
    if not old or old == cur or e_old is None or e_new is None or P["alpha_elo"] is None:
        return None
    if "attack" in prev:
        a, d = prev["attack"], prev["defence"]
    elif "xgf_pg" in prev and prev.get("league_xg_pg"):
        a, d = prev["xgf_pg"] / prev["league_xg_pg"], prev["xga_pg"] / prev["league_xg_pg"]
    else:
        return None
    g = e_old - e_new
    return {"attack": a * math.exp(P["alpha_elo"] * g), "defence": d * math.exp(-P["delta_elo"] * g)}


def is_relegated(t):
    """Changed division since last season and not promoted (the scan sets promoted for promotions)."""
    prev = t.get("prev") or {}
    return bool(prev.get("league")) and prev.get("league") != t.get("league") and not t.get("promoted")


def team_prior(t, leagues=None, P=None):
    P = P or PARAMS
    if P.get("variant") == "H" and leagues is not None and is_relegated(t):
        tr = translated_prev(t, leagues, P)     # hybrid H: S's translation for relegated teams only;
        if tr is not None:                      # promoted and unchanged teams fall through to 1.2 base
            return season_prior(tr, P["regress"])
    if P.get("variant") == "S" and leagues is not None:
        tr = translated_prev(t, leagues, P)
        if tr is not None:                      # replaces both the promoted constant and the raw prev
            return season_prior(tr, P["regress"])
    if t.get("promoted"):
        return P["promoted_attack"], P["promoted_defence"]
    return season_prior(t.get("prev"), P["regress"])


def goal_rates(p2, params=None):
    """Returns (lam_h, lam_a, details). Implements steps 1-5 of the module docstring."""
    P = dict(PARAMS, **(params or {}))
    h, a = p2["home"], p2["away"]
    lh_, la_ = p2["leagues"][h["league"]], p2["leagues"][a["league"]]
    cross = h["league"] != a["league"]
    mu_home = (lh_["mu_home"] + la_["mu_home"]) / 2 if cross else lh_["mu_home"]
    mu_away = (lh_["mu_away"] + la_["mu_away"]) / 2 if cross else lh_["mu_away"]
    ah, dh, nh = team_indices(h.get("history", []), {"H": lh_["mu_home"], "A": lh_["mu_away"]}, p2["date"],
                              *team_prior(h, p2["leagues"], P), half_life=P["half_life"], k=P["k"])
    aa, da, na = team_indices(a.get("history", []), {"H": la_["mu_home"], "A": la_["mu_away"]}, p2["date"],
                              *team_prior(a, p2["leagues"], P), half_life=P["half_life"], k=P["k"])
    variant = P.get("variant", "base")
    shift = None
    if variant == "B" and cross:
        eh, ea = lh_.get("eloMean"), la_.get("eloMean")
        if eh is not None and ea is not None and P["alpha_elo"] is not None and P["delta_elo"] is not None:
            shift = eh - ea   # Delta: league mean Elo gap, split symmetrically between the two teams
            ah *= math.exp(P["alpha_elo"] * shift / 2); dh *= math.exp(-P["delta_elo"] * shift / 2)
            aa *= math.exp(-P["alpha_elo"] * shift / 2); da *= math.exp(P["delta_elo"] * shift / 2)
    lam_h, lam_a = mu_home * ah * da, mu_away * aa * dh
    T = lam_h + lam_a
    mu_n = (mu_home + mu_away) / 2
    s_xg = mu_n * (ah * da - aa * dh)
    ha = (lam_h - lam_a) - s_xg
    w = (P["w_cross_A"] if variant == "A" else P["w_cross"]) if cross else P["w_domestic"]
    s_elo = None
    if w > 0 and P["elo_b"] is not None and h.get("elo") is not None and a.get("elo") is not None:
        s_elo = P["elo_b"] * (h["elo"] - a["elo"])
    if s_elo is None:
        w = 0.0
    gd = (1 - w) * s_xg + w * (s_elo or 0.0) + ha
    fl = P["lam_floor"]
    lam_h2, lam_a2 = max((T + gd) / 2, fl), max((T - gd) / 2, fl)
    det = {"variant": variant, "leagueShift": shift, "cross": cross, "attack_home": round(ah, 4), "defence_home": round(dh, 4), "neff_home": round(nh, 2),
           "attack_away": round(aa, 4), "defence_away": round(da, 4), "neff_away": round(na, 2),
           "total": round(T, 4), "s_xg": round(s_xg, 4), "s_elo": None if s_elo is None else round(s_elo, 4),
           "home_adv": round(ha, 4), "w_elo": w, "lam_home": round(lam_h2, 4), "lam_away": round(lam_a2, 4)}
    return lam_h2, lam_a2, det


def run(m, params=None):
    """Price one match with 1.2. Same output shape as model.run, plus 'p2detail'."""
    sport = m.get("sport", "football")
    if sport != "football" or not m.get("p2"):
        out = model.run(m)
        out["modelVersion"] = VERSION
        out["p2detail"] = {"fallback": "1.1 (no P2 inputs or not football)"}
        return out
    lh, la, det = goal_rates(m["p2"], params)
    gs = model.P["goal_scale"]
    inp = json.loads(json.dumps(m))
    inp.pop("p2", None)
    inp["home_adv"] = 1.0  # home advantage is already inside lh/la (counted once)
    inp["home"]["xgf"] = inp["away"]["xga"] = lh / gs
    inp["away"]["xgf"] = inp["home"]["xga"] = la / gs
    out = model.run(inp)
    out["modelVersion"] = VERSION
    out["p2detail"] = det
    return out


def run_candidates(m):
    """1.2 base, candidates A and B, experiments S and H on identical inputs: {"base": out, "A": ..., "H": ...}."""
    return {v: run(m, {"variant": v}) for v in ("base", "A", "B", "S", "H")}


def fit_elo_gd(rows):
    """Ordinary least squares of goal difference on pre-match Elo difference.
    rows: [(elo_diff, goal_diff)]. Returns {b, c, se_b, n}."""
    n = len(rows)
    mx = sum(x for x, _ in rows) / n
    my = sum(y for _, y in rows) / n
    sxx = sum((x - mx) ** 2 for x, _ in rows)
    sxy = sum((x - mx) * (y - my) for x, y in rows)
    b = sxy / sxx
    c = my - b * mx
    resid = sum((y - c - b * x) ** 2 for x, y in rows)
    se_b = math.sqrt(resid / (n - 2) / sxx)
    return {"b": b, "c": c, "se_b": se_b, "n": n}


if __name__ == "__main__":
    data = json.load(open(sys.argv[1]))
    res = [run(x) for x in data] if isinstance(data, list) else run(data)
    print(json.dumps(res, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- live walk-forward tuning
def _p1x2(lh, la, rho=None):
    g = model.score_grid(lh, la, rho)
    n = len(g)
    H = sum(g[i][j] for i in range(n) for j in range(n) if i > j)
    D = sum(g[i][i] for i in range(n))
    return H, D, 1 - H - D


def walk_forward(records, grid, min_train=20):
    """records: [{date, p2, result: 'H'|'D'|'A', cross: bool}] in any order. For each record after the
    first `min_train`, the configuration with the best 1X2 log-loss on EARLIER records only is chosen
    and scored on this record. grid: list of param dicts. Returns per-config and walk-forward results."""
    recs = sorted(records, key=lambda r: r["date"])
    idx = {"H": 0, "D": 1, "A": 2}
    lls = []
    for r in recs:
        row = []
        for g in grid:
            lh, la, _ = goal_rates(r["p2"], g)
            row.append(-math.log(max(_p1x2(lh, la)[idx[r["result"]]], 1e-9)))
        lls.append(row)
    wf = []
    for i in range(min_train, len(recs)):
        best = min(range(len(grid)), key=lambda c: sum(lls[j][c] for j in range(i)))
        wf.append(lls[i][best])
    return {"n": len(recs), "perConfig": [{"params": g, "logloss": sum(r[c] for r in lls) / len(lls)}
                                          for c, g in enumerate(grid)] if recs else [],
            "walkForward": (sum(wf) / len(wf)) if wf else None, "scored": len(wf)}
