"""J.A.R.V.I.S. decision pipeline v2.0 (deterministic, stdlib only).

model.py v1.1 prices a match. This module turns those prices into decisions.
Neither file lets the LLM set or move a probability: the LLM supplies
structured inputs, data flags and market prices, then writes the narrative.

Pipeline per line:  model p -> interval (logit-normal) -> min odds -> gates -> products

Two rule sets live side by side so they can be compared on the same lines:
  v1  the old rules, now in code: edge >= 3% at p, EV at the +-3/5/8 pp band >= -2%
  v2  one rule: minOdds = max((1+edge)/p, 1/p_q), p_q = the 20th percentile of p
The rule set in config["mode"] decides what is posted and which minOdds is stored:
  "shadow" -> v1 posts and v1 minOdds is stored; v2 is stored alongside for comparison
  "active" -> v2 posts and v2 minOdds is stored

Caution: until the interval is shown to be calibrated (config["intervalCalibrated"]),
p_q is a cautious bound, not a literal "80% probability of positive EV".

CLI
  python3 decision.py run input.json      -> JSON: match fields to merge, candidates, products
  python3 decision.py verify input.json   -> re-runs stored inputs, reports any mismatch
  python3 decision.py slip slip.json      -> joint price and minOdds for a slip
  python3 decision.py evaluate graded.json-> blend shadow test, calibration, sample sizes
"""
import hashlib, json, math, os, re, sys
from datetime import datetime
from statistics import NormalDist

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402  (v1.1 baseline, unchanged)
try:  # shadow model 1.2 (P2): optional, never used for decisions
    import model12
except Exception:  # missing or broken shadow model must never stop production
    model12 = None
try:  # hockey model 1.2 (production for hockey from 26 Nov 2026 when its inputs are present; 1.1 is the fallback)
    import hockey12
except Exception:  # a missing or broken hockey12.py falls back to 1.1, recorded per match
    hockey12 = None

VERSION = "3.3"

DEFAULT_CONFIG = {
    "mode": "shadow",                 # "shadow" | "active"
    "edge": 0.03,                     # required edge at the point estimate
    "quantile": 0.20,                 # cautious quantile for the v2 rule
    "intervalCalibrated": False,      # set True only after coverage has been checked
    # Effective sample size per data tier. Chosen so the q10-q90 range at p = 0.5 equals the
    # old +-3/5/8 pp bands: n = p(1-p) * (z90 / half-width)^2. Unlike the bands, it scales with p.
    "nEff": {"A": 456, "B": 164, "C": 64},
    "gapMax": 0.08,                   # max |model - no-vig market| before a line is blocked
    "qualityMin": 55,
    "trial": {"edge": 0.05, "qualityMin": 60},
    "minStartMinutes": 30,
    "maxPerEvent": None,              # owner, 26 Nov 2026: no cap per match; products sharing a match are flagged
    "maxNewPerRun": None,             # no cap on how many bets a run may post (owner's decision)
    "sports": {"football": "on", "hockey": "on", "basketball": "off"},
    "hockeyModel": "1.2",             # owner, 26 Nov 2026; "1.1" switches hockey back to the old model
    "goalFamilies": ["Match result", "Double chance", "Total goals", "Both teams to score", "Team goals"],
    "v1": {"band": {"A": 0.03, "B": 0.05, "C": 0.08}, "evLowMin": -0.02, "legEv": 0.02},
    "acca": {"legOdds": [1.20, 1.60], "total": [15, 30], "legs": [4, 10], "legEv": 0.02},
    "builder": {"odds": [2.5, 8.0], "maxLegs": 3},
    "tiers": {"safe": [1.30, 1.70, 0.70], "value": [1.70, 3.50], "long": [4.0, 10.0, 0.05]},
    "closeMinutes": 60,
    # Product profile (owner, 26 Nov 2026). "value": every single that passes the value rules, any tier, plus the
    # acca and builders (the original behaviour). "hitrate": only bets that are LIKELY to win AND pass the same
    # value rules: singles with model probability >= minProb in the safe/value tiers, builders only when their
    # joint probability >= builderMinProb, no long shots, no acca. Probabilities, minimum prices and the value
    # rules themselves are identical in both profiles; the profile only chooses which passing lines are shown.
    "profile": "value",
    "hitrate": {"minProb": 0.60, "tiers": ["safe", "value"], "builderMinProb": 0.30, "acca": False,
                # 3.2 (owner, 26 Nov 2026): likely bets with no public price to compare with (shots, corners, cards,
                # team goals...): posted as "check at BetMGM" with Jarvis' minimum price; likely multi-match combos
                # and same-match builders with a player leg. Never added when the model disagrees with the market
                # by more than gapMax on another line of the same family in that match.
                "extras": True,
                # no quotas (owner, 26 Nov 2026): every line that meets the bar is shown; only near-duplicates are
                # merged (one line per market group per match, since e.g. over 0.5 and over 1.5 team goals are the
                # same bet twice), and a match is used in one combo at most
                "check": {"minProb": 0.65, "minOdds": [1.25, 1.65]},
                "combo": {"legMinProb": 0.75, "legMinOdds": 1.12, "minJoint": 0.50, "maxLegs": 3},
                "playerBuilder": {"goalMinProb": 0.75, "playerMinProb": 0.65, "minJoint": 0.45, "odds": [1.6, 3.5]}},
}

SPORT_KEY = {"football": "football", "ice hockey": "hockey", "hockey": "hockey",
             "basketball": "basketball"}


def load_config(override=None):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decision_config.json")
    if os.path.exists(path):
        try:
            _merge(cfg, json.load(open(path)))
        except Exception as e:  # a bad config must not stop a run; defaults are safe
            print(f"decision_config.json ignored: {e}", file=sys.stderr)
    if override:
        _merge(cfg, override)
    return cfg


def _merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _merge(a[k], v)
        else:
            a[k] = v


# ---------------------------------------------------------------- basic maths
def ceil2(x):
    return math.ceil(round(x * 100, 9)) / 100


def ev(p, odds):
    """Expected value per unit stake."""
    return p * odds - 1


def logit(p):
    return math.log(p / (1 - p))


def sigmoid(x):
    return 1 / (1 + math.exp(-x))


def line_key(group, selection):
    return re.sub(r"[^A-Za-z0-9]+", "_", f"{group}__{selection}")[:120]


# ---------------------------------------------------------------- data quality
LINEUP_PENALTY = {"confirmed": 0, "probable": 5, "unknown": 12, "n/a": 0}


def data_quality(flags):
    """0-100 from structured facts the LLM reports (it never sets the number itself).
    flags: defaulted (inputs filled with defaults), stale (inputs older than 48 h),
    lineup (confirmed/probable/unknown/n/a), refereeKnown (True/False/None),
    priceSources (independent public prices found), primarySource (stats from a primary site)."""
    f = flags or {}
    s = 100
    s -= 8 * int(f.get("defaulted", 0))
    s -= 6 * int(f.get("stale", 0))
    s -= LINEUP_PENALTY.get(f.get("lineup", "unknown"), 12)
    if f.get("refereeKnown") is False:
        s -= 6
    if int(f.get("priceSources", 0)) < 2:
        s -= 10
    if not f.get("primarySource", True):
        s -= 10
    return max(0, min(100, s))


def data_tier(dq):
    return "A" if dq >= 80 else "B" if dq >= 60 else "C"


# ---------------------------------------------------------------- uncertainty
def quantile(p, n_eff, q):
    """q-quantile of p under a logit-normal with delta-method spread 1/sqrt(n p (1-p))."""
    p = min(max(p, 1e-4), 1 - 1e-4)
    se = 1 / math.sqrt(n_eff * p * (1 - p))
    return sigmoid(logit(p) + NormalDist().inv_cdf(q) * se)


def prob_summary(p, tier, cfg):
    n = cfg["nEff"][tier]
    return {"pMean": p, "pLow": quantile(p, n, 0.10), "pQ": quantile(p, n, cfg["quantile"]),
            "pHigh": quantile(p, n, 0.90), "nEff": n}


def v1_band(p, tier, cfg):
    b = cfg["v1"]["band"][tier]
    return max(p - b, 0.001), min(p + b, 0.999)


# ---------------------------------------------------------------- minimum odds
def min_odds_v1(p, tier, cfg):
    """The old rules' real threshold: edge at p AND EV >= -2% at the band's low end.
    (The HUD used to show only the first part, so it could disagree with the server.)"""
    lo, _ = v1_band(p, tier, cfg)
    return ceil2(max((1 + cfg["edge"]) / p, (1 + cfg["v1"]["evLowMin"]) / lo))


def min_odds_v2(p, p_q, cfg):
    return ceil2(max((1 + cfg["edge"]) / p, 1 / p_q))


def verdict(p, min_odds, odds):
    """Same rule as the HUD: ok at or above minOdds, thin if EV at p >= 0, else no."""
    if odds is None or not odds >= 1.01:
        return "check"
    if odds >= min_odds:
        return "ok"
    return "thin" if ev(p, odds) >= 0 else "no"


# ---------------------------------------------------------------- market reference
def novig(prices):
    inv = [1 / o for o in prices]
    s = sum(inv)
    return [x / s for x in inv]


def _partitions(markets):
    """Complete outcome sets whose prices can be de-margined together."""
    by_group, parts = {}, []
    for x in markets:
        by_group.setdefault(x["group"], []).append(x["selection"])
    whole = ("Match result", "60-minute result", "Moneyline (incl. OT)", "Moneyline",
             "Both teams to score", "Half with most goals", "Puck line")
    for g, sels in by_group.items():
        if g in whole:
            parts.append([(g, s) for s in sels])
        elif g in ("Total goals", "Total goals (60 min)", "Corners", "Cards", "Total points"):
            lines = {}
            for s in sels:
                m = re.match(r"(Over|Under) (.+)$", s)
                if m:
                    lines.setdefault(m.group(2), []).append((g, s))
            parts += [v for v in lines.values() if len(v) == 2]
    return parts


def market_probs(markets, market_odds):
    """No-vig market probability per line key, where a complete set of prices exists.
    Double chance and draw no bet are derived from the de-margined match result."""
    out = {}
    for part in _partitions(markets):
        keys = [line_key(g, s) for g, s in part]
        prices = [market_odds.get(k) for k in keys]
        if all(isinstance(o, (int, float)) and o > 1 for o in prices):
            for k, p in zip(keys, novig(prices)):
                out[k] = p
    mr = [x for x in markets if x["group"] == "Match result"]
    if len(mr) == 3 and all(line_key("Match result", x["selection"]) in out for x in mr):
        h, d, a = (out[line_key("Match result", x["selection"])] for x in mr)
        hn, an = mr[0]["selection"], mr[2]["selection"]
        out[line_key("Double chance", f"{hn} or draw")] = h + d
        out[line_key("Double chance", f"{an} or draw")] = a + d
        out[line_key("Double chance", f"{hn} or {an}")] = h + a
        out[line_key("Draw no bet", hn)] = h / (h + a)
        out[line_key("Draw no bet", an)] = a / (h + a)
    return out


# ---------------------------------------------------------------- quality score
def quality_score(ev_at_price, status, tier, price_sources, gap, lineup, dq):
    parts = {
        "ev": 0 if ev_at_price is None else round(max(0.0, min(30.0, ev_at_price / 0.10 * 30)), 1),
        "model": {"active": 20, "trial": 10}.get(status, 0),
        "evidence": {"A": 15, "B": 9, "C": 3}[tier],
        "market": (10 if price_sources >= 2 else 5 if price_sources == 1 else 0)
                  + (5 if gap is not None and abs(gap) <= 0.04 else 0),
        "info": {"confirmed": 10, "probable": 6, "n/a": 6}.get(lineup, 2),
        "data": round(dq / 10),
    }
    return round(sum(parts.values())), parts


# ---------------------------------------------------------------- gates
def evaluate_line(p, *, rules, cfg, status, tier, odds, gap, quality, minutes_to_start, sport_on):
    """Returns (postable, reasons, min_odds). odds = best known price (BetMGM if entered, else
    the public reference); None means the price must be checked at BetMGM."""
    reasons = []
    if not sport_on:
        reasons.append("sport off")
    if status not in ("active", "trial"):
        reasons.append(f"market {status}")
    if minutes_to_start is not None and minutes_to_start < cfg["minStartMinutes"]:
        reasons.append("starts too soon")
    if gap is not None and abs(gap) > cfg["gapMax"]:
        reasons.append(f"model-market gap {gap * 100:+.1f} pp")
    if rules == "v2":
        s = prob_summary(p, tier, cfg)
        mo = min_odds_v2(p, s["pQ"], cfg)
        if odds is not None and odds < mo:
            reasons.append(f"price {odds:.2f} below min {mo:.2f}")
    else:
        mo = min_odds_v1(p, tier, cfg)
        if odds is not None and odds < mo:
            why = "edge" if ev(p, odds) < cfg["edge"] else "EV at band low"
            reasons.append(f"price {odds:.2f} below min {mo:.2f} ({why})")
    if status == "trial" and odds is not None and ev(p, odds) < cfg["trial"]["edge"]:
        reasons.append("trial market needs 5% edge")
    qmin = cfg["trial"]["qualityMin"] if status == "trial" else cfg["qualityMin"]
    if quality < qmin:
        reasons.append(f"quality {quality} < {qmin}")
    return not reasons, reasons, mo


# ---------------------------------------------------------------- score grid (exact goal combos)
def football_lambdas(inp):
    """Mirror of model.football's expected goals; a unit test keeps the two in step."""
    h, a = inp["home"], inp["away"]
    ha = inp.get("home_adv", model.P["home_adv"])
    gs = model.P["goal_scale"]
    return (math.sqrt(h["xgf"] * a["xga"]) * ha * gs, math.sqrt(a["xgf"] * h["xga"]) / ha * gs)


def leg_predicate(group, selection, hn, an):
    """(i, j) -> bool for goal-market legs, or None if the leg is not on the score grid."""
    s = selection
    if group == "Match result":
        return {hn: lambda i, j: i > j, "Draw": lambda i, j: i == j, an: lambda i, j: j > i}.get(s)
    if group == "Double chance":
        return {f"{hn} or draw": lambda i, j: i >= j, f"{an} or draw": lambda i, j: j >= i,
                f"{hn} or {an}": lambda i, j: i != j}.get(s)
    if group == "Both teams to score":
        return {"Yes": lambda i, j: i > 0 and j > 0, "No": lambda i, j: i == 0 or j == 0}.get(s)
    if group == "Total goals":
        m = re.match(r"(Over|Under) ([0-9.]+)$", s)
        if m:
            L = float(m.group(2))
            return (lambda i, j: i + j > L) if m.group(1) == "Over" else (lambda i, j: i + j < L)
    if group == "Team goals":
        for name, side in ((hn, 0), (an, 1)):
            m = re.match(re.escape(name) + r" over ([0-9.]+)$", s)
            if m:
                L = float(m.group(1))
                return (lambda i, j: i > L) if side == 0 else (lambda i, j: j > L)
    return None


def joint_grid_prob(lh, la, legs, hn, an, rho=None):
    """Exact probability that every goal leg wins, from the Dixon-Coles grid. None if a leg
    is not a goal market (those combinations stay in shadow until a joint model exists)."""
    preds = [leg_predicate(g, s, hn, an) for g, s in legs]
    if any(p is None for p in preds):
        return None
    g = model.score_grid(lh, la, rho)
    n = len(g)
    return sum(g[i][j] for i in range(n) for j in range(n) if all(f(i, j) for f in preds))


def combo_quantile(legs_pq, q):
    """legs_pq: [(p, n_eff), ...] for independent legs. Delta method on log P."""
    P = math.prod(p for p, _ in legs_pq)
    var = sum((1 - p) / (n * p) for p, n in legs_pq)
    return P, math.exp(math.log(P) + NormalDist().inv_cdf(q) * math.sqrt(var))


def combo_min_odds(legs_pq, cfg, rules):
    P, Pq = combo_quantile(legs_pq, cfg["quantile"])
    return P, (ceil2((1 + cfg["edge"]) / P) if rules == "v1" else ceil2(max((1 + cfg["edge"]) / P, 1 / Pq)))


# ---------------------------------------------------------------- per-match pipeline
def _minutes_to(start, now):
    from datetime import datetime
    try:
        a = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
        return (a - b).total_seconds() / 60
    except Exception:
        return None


def input_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]



# ---------------------------------------------------------------- data requirements per market (2.6)
# Which collected facts each market family needs. "required": the line is not posted while that item
# is missing or marked estimated. "supporting": counts in coverage only. Coverage is reported, never
# used in a probability, a minimum price or the quality score.
GOAL_FAMILIES_ALL = ("Match result", "Double chance", "Draw no bet", "Total goals", "Both teams to score",
                     "Team goals", "Half with most goals")
HOCKEY_TEAM = ("60-minute result", "Moneyline (incl. OT)", "Puck line", "Total goals (60 min)")
DATA_REQUIREMENTS = {
    **{g: {"required": ["F1"], "supporting": ["F1b", "F1c", "price"]} for g in GOAL_FAMILIES_ALL},
    "Corners": {"required": ["F2"], "supporting": ["price"]},
    "Team corners": {"required": ["F2"], "supporting": ["price"]},
    "Cards": {"required": ["F3"], "supporting": ["F4", "price"]},
    "Red card": {"required": ["F3"], "supporting": ["F4"]},
    "Player shots": {"required": ["F5"], "supporting": ["price"]},
    "Player shots on target": {"required": ["F5"], "supporting": ["price"]},
    **{g: {"required": ["H1"], "supporting": ["H1b", "H2", "H3", "price"]} for g in HOCKEY_TEAM},
    "Player shots on goal": {"required": ["H4"], "supporting": ["price"]},
    "Player points": {"required": ["H4"], "supporting": ["price"]},
}
# Five statuses per data point (owner, 25 Nov 2026). Only "verified" counts as data; "unverified"
# (could not be verified, or estimated) is never treated as verified.
STATUSES5 = ("verified", "not_published", "not_available", "unreachable", "unverified")
_LEGACY = {"ok": "verified", "estimated": "unverified"}
_LEGACY_CLASS = {"not_published": "not_published", "unavailable": "not_available", "fetch_failed": "unreachable"}


REPO_MAX_AGE_H = 18   # owner, 26 Nov 2026: a jarvis-data file older than this is never "verified"


def _hours_between(a, b):
    try:
        x = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        y = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
        return (y - x).total_seconds() / 3600
    except Exception:
        return None


def _norm_status(e, now=None):
    """Provenance entry -> one of STATUSES5. A 'verified' entry without a source is 'unverified', and so is
    one taken from the data repo (repoFetchedAt) more than REPO_MAX_AGE_H hours before `now` or without a
    readable fetch time."""
    st = e.get("status", "verified")
    st = _LEGACY.get(st, st)
    if st == "missing":
        st = _LEGACY_CLASS.get(e.get("class"), "not_available")
    if st not in STATUSES5:
        st = "unverified"
    if st == "verified" and not str(e.get("source") or "").strip():
        st = "unverified"
    if st == "verified" and str(e.get("source") or "").startswith("jarvis-data") and now is not None:
        age = _hours_between(e.get("repoFetchedAt"), now)
        if age is None or age > REPO_MAX_AGE_H or age < -1:
            st = "unverified"
    return st


def _item_state(item, prov, priced_with_set, now=None):
    """-> (status, note). status: one of STATUSES5, or 'unrecorded' when the scan wrote no entry."""
    if item == "price":
        if priced_with_set:
            return "verified", None
        e = prov.get("F6") or prov.get("H5")
        if not isinstance(e, dict):
            return "unrecorded", None
        st = _norm_status(e, now)
        return ("not_available" if st == "verified" else st), e.get("note")   # no complete set on this market
    e = prov.get(item)
    if not isinstance(e, dict):
        return "unrecorded", None
    st = _norm_status(e, now)
    if st == "unverified" and str(e.get("source") or "").startswith("jarvis-data") and e.get("status", "verified") in ("verified", "ok"):
        return st, f"repo file older than {REPO_MAX_AGE_H} h or without fetch time"
    return st, (e.get("basis") or e.get("note")) if st != "verified" else None


H12_REQUIREMENTS = {"required": ["H6"], "supporting": ["H2", "H3", "price"]}


def requirements(group, h12_used=False):
    """Data a market family needs. Hockey team markets priced by 1.2 need H6 (the 1.2 state from the data
    repo) instead of H1, whose goals per game 1.2 does not use."""
    if h12_used and group in HOCKEY_TEAM:
        return H12_REQUIREMENTS
    return DATA_REQUIREMENTS.get(group)


def data_check(group, selection, prov, priced_with_set, players, now=None, h12_used=False):
    """Per line: block reason or None. A REQUIRED item that is not verified blocks this market only.
    Player lines are also blocked when that player's figures are marked estimated/unverified."""
    req = requirements(group, h12_used)
    if not req or not prov:
        return None            # scans without provenance (before 2.6) are never blocked by this check
    for it in req["required"]:
        st, note = _item_state(it, prov, priced_with_set, now)
        if st != "verified":
            st = "unverified" if st == "unrecorded" else st
            return f"data: {it} {st}" + (f" - {note}" if note else "")
    if group.startswith("Player"):
        for pl in players or []:
            if (pl.get("estimated") or pl.get("status") == "unverified") and selection.startswith(pl.get("name", "\0") + " "):
                return f"data: player figures unverified - {pl.get('estimateBasis', 'estimate')}"
    return None


def coverage_report(groups_priced, prov, sets_by_group, sport="football", now=None, h12_used=False):
    """Coverage per market family from the items THAT family needs, plus an overall share.
    Families the model could not price because a required item is recorded missing are listed too
    (priced: false), so an absent market is visible rather than silently gone."""
    by, f_all, t_all = {}, 0, 0
    pref = "F" if sport == "football" else "H"
    unpriced = {g for g, r in DATA_REQUIREMENTS.items() if g not in groups_priced and r["required"][0].startswith(pref)
                and prov and any(_item_state(i, prov, False, now)[0] not in ("verified", "unrecorded") for i in r["required"])}
    for g in sorted(set(groups_priced) | unpriced):
        req = requirements(g, h12_used)
        if not req:
            continue
        items, found, missing = req["required"] + req["supporting"], 0, []
        for it in items:
            st, note = _item_state(it, prov, sets_by_group.get(g, False), now)
            if st == "verified":
                found += 1
            else:
                missing.append({"item": it, "status": st, "required": it in req["required"]})
        by[g] = {"priced": g in groups_priced, "required": req["required"], "supporting": req["supporting"], "found": found,
                 "total": len(items), "pct": round(100 * found / len(items)), "missing": missing}
        f_all += found
        t_all += len(items)
    items = {}
    for it in sorted({i for r in DATA_REQUIREMENTS.values() for i in r["required"] + r["supporting"]
                      if i != "price" and i[0] == pref} | {"F6" if pref == "F" else "H5"}
                     | ({"H6"} if h12_used else set())):
        items[it] = _norm_status(prov[it], now) if isinstance(prov.get(it), dict) else "unrecorded"
    return {"pct": round(100 * f_all / t_all) if t_all else None, "byMarket": by,
            "items": items, "recorded": bool(prov)}


def odds_age_minutes(odds_at, now):
    try:
        a = datetime.fromisoformat(str(odds_at).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
        return round((b - a).total_seconds() / 60)
    except Exception:
        return None


def hockey12_status(m, cfg, now):
    """(use 1.2?, reason when not). 1.2 is used only with its own inputs: an "h12" block from the data repo
    with the params this hockey12.py was chosen with, and provenance H6 that is verified and fresh."""
    if cfg.get("hockeyModel", "1.2") != "1.2":
        return False, "hockeyModel set to 1.1"
    if hockey12 is None:
        return False, "hockey12.py not available"
    b = m.get("h12")
    if not isinstance(b, dict):
        return False, "no h12 inputs"
    try:
        for side in ("home", "away"):
            for f in ("sf", "sa", "gf", "ga", "n"):
                float(b[side][f])
        if b.get("leagueCode", "nhl") not in hockey12.PARAMS_BY_LEAGUE:
            return False, f"h12 league {b.get('leagueCode')} has no 1.2 parameters"
        if any(abs(float((b.get("params") or {}).get(k, -1)) - float(v)) > 1e-9 for k, v in hockey12.params_for(b).items()):
            return False, "h12 params differ from hockey12.py"
        if b.get("version") != hockey12.VERSION:
            return False, "h12 version differs from hockey12.py"
    except Exception:
        return False, "h12 inputs incomplete"
    e = (m.get("provenance") or {}).get("H6")
    if not isinstance(e, dict):
        return False, "H6 provenance missing"
    st = _norm_status(e, now)
    if st != "verified":
        return False, f"H6 {st}"
    return True, None


def process_match(m, statuses, now, cfg):
    sport = SPORT_KEY.get(str(m.get("sport", "football")).lower(), "football")
    inp = dict(m["inputs"])
    inp["sport"] = sport
    priced = model.run(inp)
    h12_used, h12_info = False, None
    if sport == "hockey":
        h12_used, why_not = hockey12_status(m, cfg, now)
        if h12_used:
            lh, la = hockey12.rates_from_block(m["h12"])
            p12 = hockey12.price(lh, la, (inp["home"].get("name", "Home"), inp["away"].get("name", "Away")),
                                 dict(hockey12.params_for(m["h12"])), players=inp.get("players"))
            p12["markets"] = [x for x in p12["markets"] if 3 <= x["prob"] <= 97]
            p11 = priced                                  # 1.1 kept as a shadow on the same lines
            priced = {**p12, "modelVersion": "1.2-hockey", "expected": {"home_goals": round(lh, 2), "away_goals": round(la, 2)}}
            h12_info = {"used": "1.2", "league": m["h12"].get("leagueCode", "nhl"), "lam": [round(lh, 6), round(la, 6)], "expected11": p11["expected"],
                        "prob11": {line_key(x["group"], x["selection"]): x["prob"] for x in p11["markets"]},
                        "b2b": [bool(m["h12"].get("b2bHome")), bool(m["h12"].get("b2bAway"))],
                        "goalies": [(m["h12"].get("goalieHome") or {}).get("name"), (m["h12"].get("goalieAway") or {}).get("name")],
                        "stateBehind": bool(m["h12"].get("stateBehind"))}
        else:
            h12_info = {"used": "1.1", "fallbackReason": why_not}
    dq = data_quality(m.get("flags"))
    tier = data_tier(dq)
    mo_ref = m.get("marketOdds") or {}
    mgm = m.get("mgmLines") or {}
    pm = market_probs(priced["markets"], mo_ref)
    sources = m.get("flags", {}).get("priceSources", 0)
    lineup = m.get("flags", {}).get("lineup", "unknown")
    mins = _minutes_to(m.get("start"), now)
    sport_on = cfg["sports"].get(sport, "off") == "on"
    prov = m.get("provenance") or {}
    odds_at = m.get("oddsAt") or {}
    sets_by_group, key_group = {}, {}
    for x in priced["markets"]:
        kk = line_key(x["group"], x["selection"])
        key_group[kk] = x["group"]
        if kk in pm:
            sets_by_group[x["group"]] = True
    stale_min = cfg.get("staleOddsMin", 180)
    stale_keys = [kk for kk, t in odds_at.items() if (odds_age_minutes(t, now) or 0) > stale_min]
    rows = []
    for x in priced["markets"]:
        k = line_key(x["group"], x["selection"])
        p = x["prob"] / 100
        s = prob_summary(p, tier, cfg)
        lo1, hi1 = v1_band(p, tier, cfg)
        status = statuses.get(x["group"], {}).get("status", "shadow")
        odds = mgm.get(k) if isinstance(mgm.get(k), (int, float)) else mo_ref.get(k)
        odds = odds if isinstance(odds, (int, float)) and odds > 1 else None
        # Gap check: de-margined set when complete; otherwise a single public price, de-margined
        # with an assumed 5% overround, so a one-sided price can no longer skip the check.
        ref1 = mo_ref.get(k)
        if k in pm:
            p_ref, ref_kind = pm[k], "novig"
        elif isinstance(ref1, (int, float)) and ref1 > 1:
            p_ref, ref_kind = 1 / ref1 / 1.05, "single"
        else:
            p_ref, ref_kind = None, None
        gap = (p - p_ref) if p_ref is not None else None
        q, qb = quality_score(None if odds is None else ev(p, odds), status, tier, sources, gap, lineup, dq)
        common = dict(cfg=cfg, status=status, tier=tier, odds=odds, gap=gap, quality=q,
                      minutes_to_start=mins, sport_on=sport_on)
        ok1, why1, mo1 = evaluate_line(p, rules="v1", **common)
        ok2, why2, mo2 = evaluate_line(p, rules="v2", **common)
        block = data_check(x["group"], x["selection"], prov, sets_by_group.get(x["group"], False),
                           inp.get("players"), now, h12_used)
        if block:   # a market without its required facts is not posted; other markets are unaffected
            ok1, ok2, why1, why2 = False, False, why1 + [block], why2 + [block]
        age = odds_age_minutes(odds_at.get(k), now) if k in odds_at else None
        stale_set = [kk for kk in stale_keys if key_group.get(kk) == x["group"] and kk in pm] if k in pm else []
        if (age is not None and age > stale_min) or stale_set:
            # a reference price older than staleOddsMin (own price or one in the set it is de-margined with)
            # never leads to a posted bet: the line is blocked; other lines are unaffected
            msg = (f"data: reference price stale ({age} min > {stale_min})" if age is not None and age > stale_min
                   else f"data: reference price set stale (another price in the set is older than {stale_min} min)")
            ok1, ok2, why1, why2 = False, False, why1 + [msg], why2 + [msg]
        active = cfg["mode"] == "active"
        rows.append({
            "group": x["group"], "selection": x["selection"], "key": k,
            "prob": x["prob"], "fair": x["fair"],
            "probLow": round((s["pLow"] if active else lo1) * 100, 1),
            "probHigh": round((s["pHigh"] if active else hi1) * 100, 1),
            "pQ": round(s["pQ"] * 100, 1), "nEff": s["nEff"],
            "minOdds": mo2 if active else mo1, "minOddsV1": mo1, "minOddsV2": mo2,
            "pMarket": None if gap is None else round(p_ref * 100, 1), "pMarketKind": ref_kind,
            "gap": None if gap is None else round(gap * 100, 1),
            "odds": odds, "ev": None if odds is None else round(ev(p, odds) * 100, 1),
            "status": status, "qualityScore": q, "qualityBreakdown": qb,
            "v1": {"post": ok1, "why": why1}, "v2": {"post": ok2, "why": why2},
            "oddsAgeMin": age,
            "oddsStale": None if age is None else age > stale_min,
        })
        if h12_used:
            rows[-1]["prob11"] = h12_info["prob11"].get(k)
    # Shadow model 1.2: same inputs plus the P2 fields; stored next to 1.1, never used to decide.
    shadow12 = None
    if model12 is not None and sport == "football" and m.get("p2"):
        try:
            cands = model12.run_candidates(dict(inp, p2=m["p2"]))   # base, A, B on identical inputs
            for v, field in (("base", "prob12"), ("A", "prob12a"), ("B", "prob12b"), ("S", "prob12s"), ("H", "prob12h")):
                probs = {line_key(x["group"], x["selection"]): x["prob"] for x in cands[v]["markets"]}
                for r in rows:
                    if r["key"] in probs:
                        r[field] = probs[r["key"]]
            p12 = cands["base"]
            shadow12 = {"version": p12["modelVersion"], "detail": p12["p2detail"], "expected": p12["expected"],
                        "candidates": {v: {"detail": cands[v]["p2detail"], "expected": cands[v]["expected"]} for v in ("A", "B", "S", "H")}}
        except Exception as e:
            shadow12 = {"error": str(e)[:200]}
    out = {"id": m.get("id"), "event": m.get("event"), "sport": sport, "start": m.get("start"),
           "markets": rows, "expected": priced["expected"], "dataQuality": dq, "dataTier": tier,
           "modelVersion": priced["modelVersion"],
           "coverage": coverage_report({r["group"] for r in rows}, prov, sets_by_group, sport, now, h12_used),
           "decision": {"version": VERSION, "mode": cfg["mode"], "at": now,
                        "inputHash": input_hash({"inputs": m["inputs"], "flags": m.get("flags"),
                                                 "marketOdds": mo_ref, "mgmLines": mgm,
                                                 **({"h12": m["h12"]} if m.get("h12") else {})})}}
    if shadow12 is not None:
        out["model12"] = shadow12
    if h12_info is not None:
        out["hockeyModel"] = {k: v for k, v in h12_info.items() if k != "prob11"}
    if sport == "football":
        lh, la = football_lambdas(inp)
        out["grid"] = {"lh": round(lh, 6), "la": round(la, 6), "rho": model.P["dc_rho"],
                       "home": inp["home"].get("name", "Home"), "away": inp["away"].get("name", "Away")}
    return out


# ---------------------------------------------------------------- products
def _tier_for(odds, p, e, cfg):
    """A label only, not a cap: every price that passes the value rules gets a tier.
    Long shots (odds >= 4) keep their extra value rule: edge >= 5%."""
    t = cfg["tiers"]
    if odds >= t["long"][0]:
        return "long" if e >= t["long"][2] else None
    if odds <= t["safe"][1] and p >= t["safe"][2]:
        return "safe"
    return "value"


FAMILY_GROUP = {**{g: "goals" for g in GOAL_FAMILIES_ALL}, "Corners": "corners", "Team corners": "corners",
                "Cards": "cards", "Red card": "cards", **{g: "hockey" for g in HOCKEY_TEAM}}
# Corners and cards are not offered without a market price: on 24,000 matches (football-data.co.uk 2021-2026,
# research/corners_cards_backtest.json) team statistics predicted total corners and total cards no better than
# the league average, so a "likely" corners or cards line here would be the league average dressed up.
CHECK_FAMILIES = ("Team goals", "Both teams to score", "Total goals",
                  "Player shots", "Player shots on target", "Player shots on goal", "Player points")
PLAYER_FAMILIES = ("Player shots", "Player shots on target", "Player shots on goal")
COMBO_UNPRICED = ("Team goals", "Both teams to score", "Total goals", "Player shots", "Player shots on target",
                  "Player shots on goal", "Player points")
_rank = lambda r: 0 if FAMILY_GROUP.get(r["group"]) in ("goals", "hockey") else 1 if r["group"].startswith("Player") else 2
GOAL_OVER = re.compile(r"(^Over )|( over )|(^Yes$)")


def _distrusted(m, cfg):
    """Family groups in which the model sits more than gapMax away from the market on some priced line:
    the model is not trusted for unpriced lines of that group in this match."""
    return {FAMILY_GROUP.get(r["group"], r["group"]) for r in m["markets"]
            if r.get("gap") is not None and abs(r["gap"]) / 100 > cfg["gapMax"]}


def _checkable(r, rules):
    """An unpriced line that passes everything except the price check (and the quality score, which an unpriced
    line cannot reach because quality rewards a known price): market active/trial, sport on, starts late
    enough, its required data verified."""
    if r["odds"] is not None or r["status"] not in ("active", "trial"):
        return False
    return not [w for w in r[rules]["why"] if not w.startswith("quality")]


def _pick(r):
    return f'{r["selection"]} ({r["group"]})'


def _leg(m, r):
    return {"matchId": m["id"], "event": m["event"], "start": m["start"], "pick": _pick(r), "line": r}


def extra_products(processed, cfg, rules, room, add, taken):
    """3.2 hit-rate extras: check-at-BetMGM singles, multi-match combos, same-match builders with a player leg."""
    hr = cfg["hitrate"]
    ck, cb, pb = hr["check"], hr["combo"], hr["playerBuilder"]
    trust = {m["id"]: _distrusted(m, cfg) for m in processed}
    ok_group = lambda m, r: FAMILY_GROUP.get(r["group"], r["group"]) not in trust[m["id"]]
    # multi-match combos first: one leg per match, the most likely legs
    cands = []
    for m in processed:
        best = None
        for r in m["markets"]:
            p = r["prob"] / 100
            if p < cb["legMinProb"] or r["minOdds"] < cb["legMinOdds"] or not ok_group(m, r) or (m["id"], r["key"]) in taken:
                continue
            # unpriced corners/cards are not calibrated yet: they may be single checks, never legs of a combo
            if not (r[rules]["post"] or (_checkable(r, rules) and r["group"] in COMBO_UNPRICED)):
                continue
            if best is None or (_rank(r), -r["prob"]) < (_rank(best), -best["prob"]):
                best = r
        if best:
            cands.append((m, best))
    cands.sort(key=lambda x: (_rank(x[1]), -x[1]["prob"]))
    used = set()
    while True:
        legs, P = [], 1.0
        for m, r in cands:
            if m["id"] in used or not room(m["id"]) or len(legs) >= cb["maxLegs"]:
                continue
            if P * r["prob"] / 100 < cb["minJoint"]:
                continue
            legs.append((m, r))
            P *= r["prob"] / 100
        if len(legs) < 2:
            break
        Pj, mo = combo_min_odds([(r["prob"] / 100, r["nEff"]) for _, r in legs], cfg, rules)
        odds = math.prod(r["odds"] for _, r in legs) if all(r["odds"] for _, r in legs) else None
        bet = {"tier": "combo", "kind": "combo", "pick": " + ".join(_pick(r) for _, r in legs),
               "confidence": round(Pj * 100, 1), "minOdds": mo,
               "odds": None if odds is None else round(odds, 2), "needsPrice": odds is None,
               "start": min(m["start"] for m, _ in legs), "legs": [_leg(m, r) for m, r in legs]}
        used |= {m["id"] for m, _ in legs}        # each match in one combo at most, whatever happens below
        if odds is not None and odds < mo:
            continue
        add(bet, [m["id"] for m, _ in legs])
    # same-match builder: a likely goal leg (over/yes) + a likely player-shots leg. Priced as independent legs:
    # both lean the same way (more attacking play), so the real joint chance is at least the product - cautious.
    for m in processed:
        if not room(m["id"]):
            continue
        goal = [r for r in m["markets"] if r["group"] in ("Total goals", "Team goals", "Both teams to score")
                and GOAL_OVER.search(r["selection"]) and r["prob"] / 100 >= pb["goalMinProb"] and ok_group(m, r)
                and (r[rules]["post"] or _checkable(r, rules) or (r.get("gap") is not None and abs(r["gap"]) / 100 <= cfg["gapMax"]
                                                               and not [w for w in r[rules]["why"] if w.startswith(("data", "sport", "market", "starts"))]))]
        plays = [r for r in m["markets"] if r["group"] in PLAYER_FAMILIES and r["prob"] / 100 >= pb["playerMinProb"]
                 and _checkable(r, rules)]
        best = None
        for g in goal:
            for pl in plays:
                pj = g["prob"] / 100 * pl["prob"] / 100
                if pj < pb["minJoint"]:
                    continue
                Pj, mo = combo_min_odds([(g["prob"] / 100, g["nEff"]), (pl["prob"] / 100, pl["nEff"])], cfg, rules)
                if not (pb["odds"][0] <= mo <= pb["odds"][1]):
                    continue
                if best is None or Pj > best["confidence"] / 100:
                    best = {"tier": "builder", "kind": "playerBuilder", "matchId": m["id"], "event": m["event"], "start": m["start"],
                            "pick": f"{_pick(g)} + {_pick(pl)}",
                            "confidence": round(Pj * 100, 1), "minOdds": mo, "needsPrice": True, "legs": [g, pl]}
        if best:
            add(best, [m["id"]])
    # check-at-BetMGM singles: the most likely unpriced line per match in a sensible price range
    for m in processed:
        if not room(m["id"]):
            continue
        lines = [r for r in m["markets"] if r["group"] in CHECK_FAMILIES and _checkable(r, rules) and ok_group(m, r)
                 and r["prob"] / 100 >= ck["minProb"] and ck["minOdds"][0] <= r["minOdds"] <= ck["minOdds"][1]
                 and (m["id"], r["key"]) not in taken]
        seen = set()
        for r in sorted(lines, key=lambda r: -r["prob"]):
            grp = FAMILY_GROUP.get(r["group"], r["group"])
            if grp in seen:
                continue
            seen.add(grp)
            add({"tier": "safe" if r["prob"] >= 70 else "value", "kind": "check", "pick": _pick(r), "matchId": m["id"], "event": m["event"],
                 "start": m["start"], "needsPrice": True, "line": r}, [m["id"]])


def build_products(processed, open_bets, cfg, rules):
    hr = (cfg.get("hitrate") or {}) if cfg.get("profile") == "hitrate" else None
    per_event = {}
    for b in open_bets:
        if b.get("status") in ("pending", None) and not b.get("withdrawn"):
            for mid in [b.get("matchId")] + [l.get("matchId") for l in b.get("legs") or []]:
                if mid:
                    per_event[mid] = per_event.get(mid, 0) + 1
    taken = {(b.get("matchId"), b.get("key")) for b in open_bets}
    new, singles = [], []

    def room(mid):
        return cfg.get("maxPerEvent") is None or per_event.get(mid, 0) < cfg["maxPerEvent"]

    def add(bet, mids):
        if (cfg.get("maxNewPerRun") and len(new) >= cfg["maxNewPerRun"]) or not all(room(m) for m in mids):
            return False
        for m in mids:
            per_event[m] = per_event.get(m, 0) + 1
        new.append(bet)
        return True

    # singles: the best postable priced line per market group of each event (near-duplicates merged, no quota)
    for m in processed:
        cands = [r for r in m["markets"] if r[rules]["post"] and r["odds"] is not None
                 and (m["id"], r["key"]) not in taken
                 and (hr is None or r["prob"] / 100 >= hr.get("minProb", 0.60))]
        seen = set()
        for r in sorted(cands, key=lambda r: -r["ev"]):
            grp = FAMILY_GROUP.get(r["group"], r["group"])
            if grp in seen:
                continue
            t = _tier_for(r["odds"], r["prob"] / 100, r["ev"] / 100, cfg)
            if t and (hr is None or t in hr.get("tiers", ["safe", "value"])):
                singles.append((t, r, m))
                seen.add(grp)
    singles.sort(key=lambda x: -x[1]["ev"])
    for t, r, m in singles:  # no per-tier quotas: every single that passes the value rules
        add({"tier": t, "matchId": m["id"], "event": m["event"], "start": m["start"], "line": r}, [m["id"]])

    # acca: one leg per event, every leg value on its own and break-even at its cautious bound
    a = cfg["acca"]
    legs = []
    for m in processed:
        best = None
        for r in m["markets"]:
            o = r["odds"]
            if o is None or not (a["legOdds"][0] <= o <= a["legOdds"][1]) or r["status"] not in ("active", "trial"):
                continue
            blocked = [w for w in r[rules]["why"] if not w.startswith(("price", "quality", "trial"))]
            if blocked or ev(r["prob"] / 100, o) < a["legEv"]:
                continue
            if rules == "v2" and o < 1 / (r["pQ"] / 100):
                continue
            if best is None or r["ev"] > best[1]["ev"]:
                best = (m, r)
        if best and room(best[0]["id"]):
            legs.append(best)
    legs.sort(key=lambda x: -x[1]["ev"])
    chosen, total = [], 1.0
    for m, r in legs:
        if len(chosen) >= a["legs"][1] or total >= a["total"][0]:
            break
        chosen.append((m, r))
        total *= r["odds"]
    acca = None
    if hr is not None and not hr.get("acca", False):
        chosen = []
    if chosen and a["legs"][0] <= len(chosen) and a["total"][0] <= total <= a["total"][1]:
        P, mo = combo_min_odds([(r["prob"] / 100, r["nEff"]) for _, r in chosen], cfg, rules)
        if total >= mo:
            acca = {"tier": "acca", "odds": round(total, 2), "confidence": round(P * 100, 1), "minOdds": mo,
                    "start": min(m["start"] for m, _ in chosen),
                    "legs": [{"matchId": m["id"], "event": m["event"], "start": m["start"], "line": r} for m, r in chosen]}
            add(acca, [m["id"] for m, _ in chosen])

    # builder: exact goal-only combos, the best one per match, price to be checked at BetMGM
    for m in processed:
        builder = None
        g = m.get("grid")
        if not g or not room(m["id"]):
            continue
        pool = [r for r in m["markets"] if r["group"] in cfg["goalFamilies"] and r["gap"] is not None
                and r["gap"] > 0 and r["status"] in ("active", "trial")
                and not [w for w in r[rules]["why"] if w.startswith(("sport", "market", "starts", "model-market"))]]
        pool.sort(key=lambda r: -r["gap"])
        for i in range(len(pool)):
            for j in range(i + 1, len(pool)):
                if pool[i]["group"] == pool[j]["group"]:
                    continue
                pj = joint_grid_prob(g["lh"], g["la"], [(pool[i]["group"], pool[i]["selection"]),
                                                       (pool[j]["group"], pool[j]["selection"])], g["home"], g["away"], g["rho"])
                if not pj or not (cfg["builder"]["odds"][0] <= 1 / pj <= cfg["builder"]["odds"][1]):
                    continue
                if hr is not None and pj < hr.get("builderMinProb", 0.30):
                    continue
                n = min(pool[i]["nEff"], pool[j]["nEff"])
                pq = quantile(pj, n, cfg["quantile"])
                mo = ceil2((1 + cfg["edge"]) / pj) if rules == "v1" else ceil2(max((1 + cfg["edge"]) / pj, 1 / pq))
                score = pool[i]["gap"] + pool[j]["gap"]
                if builder is None or score > builder["_score"]:
                    builder = {"tier": "builder", "matchId": m["id"], "event": m["event"], "start": m["start"],
                               "confidence": round(pj * 100, 1), "minOdds": mo, "needsPrice": True, "_score": score,
                               "legs": [pool[i], pool[j]]}
        if builder:
            builder.pop("_score")
            add(builder, [builder["matchId"]])
    if hr is not None and hr.get("extras"):
        extra_products(processed, cfg, rules, room, add, taken)
    flag_shared_matches(new)
    return new


def _mids(b):
    return {x for x in [b.get("matchId")] + [l.get("matchId") for l in b.get("legs") or [] if isinstance(l, dict)] if x}


def flag_shared_matches(products):
    """No cap per match (owner); instead every product says which other products of this run depend on the same
    match (sharedWith: indexes in the product list), so their outcomes are related."""
    ms = [_mids(b) for b in products]
    for i, b in enumerate(products):
        sh = [j for j in range(len(products)) if j != i and ms[i] & ms[j]]
        if sh:
            b["sharedWith"] = sh


# ---------------------------------------------------------------- run / verify
def run(payload, cfg=None):
    cfg = cfg or load_config(payload.get("config"))
    now = payload["now"]
    statuses = payload.get("markets", {})
    processed = [process_match(m, statuses, now, cfg) for m in payload["matches"]]
    open_bets = payload.get("openBets", [])
    active = "v2" if cfg["mode"] == "active" else "v1"
    shadow = "v1" if active == "v2" else "v2"
    return {"decisionVersion": VERSION, "mode": cfg["mode"], "rules": active, "now": now,
            "matches": [storable(m) for m in processed],
            "profile": cfg.get("profile", "value"),
            "products": build_products(processed, open_bets, cfg, active),
            "shadowProducts": build_products(processed, open_bets, cfg, shadow),
            # what the other product profile would have posted with the active rules (recorded, never posted)
            "otherProfileProducts": build_products(processed, open_bets,
                                                   dict(cfg, profile="value" if cfg.get("profile") == "hitrate" else "hitrate"), active),
            "summary": summarise(processed), "coverage": run_coverage(processed)}


def run_coverage(processed):
    """Run-level view for the log: mean coverage, matches at 100%, weakest matches, blocked lines."""
    rec = [m for m in processed if (m.get("coverage") or {}).get("recorded") and m["coverage"]["pct"] is not None]
    weak = sorted(rec, key=lambda m: m["coverage"]["pct"])[:5]
    return {"matchesRecorded": len(rec), "avgPct": round(sum(m["coverage"]["pct"] for m in rec) / len(rec)) if rec else None,
            "matchesFull": sum(m["coverage"]["pct"] == 100 for m in rec),
            "blockedLines": sum(any(w.startswith("data:") for w in r["v1"]["why"]) for m in processed for r in m["markets"]),
            "weakest": [f'{m.get("event")}: ' + ", ".join(sorted({f'{x["item"]} ({x["status"]})'
                         for g in m["coverage"]["byMarket"].values() for x in g["missing"]})) for m in weak if m["coverage"]["pct"] < 100],
            "itemSuccess": item_success(rec)}


def item_success(matches):
    """How often each data point was delivered verified: {item: {verified, total, pct, byStatus}}."""
    out = {}
    for m in matches:
        for it, st in (m["coverage"].get("items") or {}).items():
            d = out.setdefault(it, {"verified": 0, "total": 0, "byStatus": {}})
            d["total"] += 1
            d["verified"] += st == "verified"
            d["byStatus"][st] = d["byStatus"].get(st, 0) + 1
    for d in out.values():
        d["pct"] = round(100 * d["verified"] / d["total"]) if d["total"] else None
    return out


def summarise(processed):
    s = {"lines": 0, "v1Post": 0, "v2Post": 0, "both": 0, "v1Only": 0, "v2Only": 0, "priced": 0}
    for m in processed:
        for r in m["markets"]:
            s["lines"] += 1
            if r["odds"] is None:        # unpriced lines wait for the BetMGM check
                continue
            s["priced"] += 1
            a, b = r["v1"]["post"], r["v2"]["post"]
            s["v1Post"] += a
            s["v2Post"] += b
            s["both"] += a and b
            s["v1Only"] += a and not b
            s["v2Only"] += b and not a
    return s


def storable(match):
    """Compact copy of a processed match for the database (no per-line quality breakdown;
    reasons kept only for priced lines)."""
    m = dict(match)
    rows = []
    for r in match["markets"]:
        r = {k: v for k, v in r.items() if k not in ("qualityBreakdown", "fair") and v is not None}
        if "odds" not in r:
            r["v1"] = {"post": r["v1"]["post"]}
            r["v2"] = {"post": r["v2"]["post"]}
        rows.append(r)
    m["markets"] = rows
    return m


def verify(payload, stored, cfg=None, tol=0.051):
    """Re-run the stored inputs and compare the stored numbers. Any difference larger than
    rounding means a probability or minimum was changed outside the pipeline."""
    res = run(payload, cfg)
    issues = []
    fresh = {m["id"]: {r["key"]: r for r in m["markets"]} for m in res["matches"]}
    for mid, lines in stored.items():
        for k, r in lines.items():
            f = fresh.get(mid, {}).get(k)
            if f is None:
                issues.append(f"{mid} {k}: not produced by the pipeline")
                continue
            for fld in ("prob", "minOdds"):
                if fld in r and abs(float(r[fld]) - float(f[fld])) > tol:
                    issues.append(f"{mid} {k}: stored {fld} {r[fld]} vs pipeline {f[fld]}")
    return issues


# ---------------------------------------------------------------- closing line value
def clv(odds_taken, close_prices, minutes_before, cfg):
    """close_prices: the reference market's full outcome set at the snapshot, selection first.
    Returns (clv, label). Only snapshots within closeMinutes of kick-off count as 'close'."""
    if not close_prices or odds_taken is None:
        return None, "none"
    p_close = novig(close_prices)[0] if len(close_prices) > 1 else 1 / close_prices[0]
    label = "close" if minutes_before is not None and 0 <= minutes_before <= cfg["closeMinutes"] else "pre-close"
    if len(close_prices) == 1:
        label += "-with-margin"
    return round(odds_taken * p_close - 1, 4), label


# ---------------------------------------------------------------- shadow evaluation
def _ll(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def match_counts(graded):
    """Distinct matches per market family (lines in one match are correlated)."""
    fam = {}
    for g in graded:
        fam.setdefault(g["group"], set()).add(g["matchId"])
    return {k: len(v) for k, v in fam.items()}


def evaluate(graded, weights=(0.0, 0.25, 0.5, 0.75, 1.0)):
    """graded: [{matchId, at, group, pModel, pMarket (or None), y in {0,1}}].
    Model-only, market-only and blends p = w*model + (1-w)*market, scored per match.
    w is chosen on the earlier half of matches and tested on the later half."""
    both = [g for g in graded if g.get("pMarket") is not None and g.get("pMarketKind", "novig") == "novig"
            and g["y"] in (0, 1)]
    mids = sorted({g["matchId"] for g in both}, key=lambda m: min(x["at"] for x in both if x["matchId"] == m))
    half = set(mids[: len(mids) // 2])
    train = [g for g in both if g["matchId"] in half]
    test = [g for g in both if g["matchId"] not in half]

    def score(rows, w):
        if not rows:
            return None
        per = {}
        for g in rows:
            p = w * g["pModel"] + (1 - w) * g["pMarket"]
            per.setdefault(g["matchId"], []).append(_ll(p, g["y"]))
        return sum(sum(v) / len(v) for v in per.values()) / len(per)

    best_w = min(weights, key=lambda w: score(train, w) if train else 0) if train else None
    return {"matches": len(mids), "lines": len(both), "trainMatches": len(half), "testMatches": len(mids) - len(half),
            "logLossByWeight": {str(w): score(both, w) for w in weights},
            "chosenWeight": best_w,
            "test": {"model": score(test, 1.0), "market": score(test, 0.0),
                     "blend": score(test, best_w) if best_w is not None else None},
            "calibration": calibration([g for g in graded if g["y"] in (0, 1)]),
            "familyMatches": match_counts(graded),
            "enough": len(mids) >= 30}


def compare_all(graded, models=("p12", "p12a", "p12b", "p12s", "p12h"), min_matches=200, min_cross=50):
    """Every shadow model against 1.1 and the market, on the lines where ALL of them exist
    (identical matches for every comparison). Returns {model: compare_models block}."""
    rows = [g for g in graded if all(g.get(k) is not None for k in models)]
    return {k: compare_models([dict(g, p12=g[k]) for g in rows], min_matches, min_cross) for k in models}


def compare_models(graded, min_matches=200, min_cross=50):
    """Shadow evaluation of model 1.2 against 1.1 and the no-vig market.
    graded: [{matchId, at, group, cross (bool), p11, p12, pMarket (or None), pMarketKind, y in {0,1}}].
    Lines are averaged within each match first (they are correlated), then matches are compared in
    pairs. Returns mean log-loss differences with 95% intervals overall, cross-league, domestic and
    per market family. Promotion is never automatic: 'thresholdMet' only says the owner may review."""
    rows = [g for g in graded if g.get("p12") is not None and g.get("p11") is not None and g["y"] in (0, 1)]

    def per_match(sub, with_market=False):
        acc = {}
        for g in sub:
            if with_market and (g.get("pMarket") is None or g.get("pMarketKind", "novig") != "novig"):
                continue
            a = acc.setdefault(g["matchId"], [0.0, 0.0, 0.0, 0])
            a[0] += _ll(g["p11"], g["y"]); a[1] += _ll(g["p12"], g["y"])
            a[2] += _ll(g["pMarket"], g["y"]) if with_market else 0.0
            a[3] += 1
        return [(v[0] / v[3], v[1] / v[3], v[2] / v[3]) for v in acc.values()]

    def ci(d):
        n = len(d)
        if n < 2:
            return {"n": n, "mean": (d[0] if d else None), "lo95": None, "hi95": None}
        mu = sum(d) / n
        sd = math.sqrt(sum((x - mu) ** 2 for x in d) / (n - 1))
        return {"n": n, "mean": mu, "lo95": mu - 1.96 * sd / math.sqrt(n), "hi95": mu + 1.96 * sd / math.sqrt(n)}

    def block(sub):
        pm = per_match(sub)
        pmk = per_match(sub, with_market=True)
        return {"matches": len(pm), "m12_minus_m11": ci([b - a for a, b, _ in pm]),
                "m12_minus_market": ci([b - c for _, b, c in pmk]), "m11_minus_market": ci([a - c for a, _, c in pmk])}

    fams = sorted({g["group"] for g in rows})
    res = {"all": block(rows), "cross": block([g for g in rows if g.get("cross")]),
           "domestic": block([g for g in rows if not g.get("cross")]),
           "byFamily": {f: block([g for g in rows if g["group"] == f]) for f in fams}}
    res["thresholdMet"] = res["all"]["matches"] >= min_matches and res["cross"]["matches"] >= min_cross
    return res


def calibration(graded, bins=(0, 0.2, 0.4, 0.6, 0.8, 1.0001)):
    out = []
    for lo, hi in zip(bins, bins[1:]):
        rows = [g for g in graded if lo <= g["pModel"] < hi]
        if rows:
            out.append({"bin": f"{lo:.1f}-{min(hi, 1):.1f}", "n": len(rows),
                        "predicted": round(sum(g["pModel"] for g in rows) / len(rows), 3),
                        "observed": round(sum(g["y"] for g in rows) / len(rows), 3)})
    return out


def interval_coverage(graded, cfg):
    """Rough check that the q10-q90 interval is honest: in each probability bin the observed
    hit rate should fall inside the average interval about 80% of the time across bins.
    Needs a few hundred graded lines; until then intervalCalibrated stays False."""
    rows = [g for g in graded if g.get("pLow") is not None and g["y"] in (0, 1)]
    cal = []
    for lo, hi in zip((0, 0.2, 0.4, 0.6, 0.8), (0.2, 0.4, 0.6, 0.8, 1.0001)):
        b = [g for g in rows if lo <= g["pModel"] < hi]
        if len(b) >= 30:
            obs = sum(g["y"] for g in b) / len(b)
            low = sum(g["pLow"] for g in b) / len(b)
            high = sum(g["pHigh"] for g in b) / len(b)
            cal.append({"bin": f"{lo:.1f}-{min(hi, 1):.1f}", "n": len(b), "observed": round(obs, 3),
                        "low": round(low, 3), "high": round(high, 3), "inside": low <= obs <= high})
    return {"bins": cal, "ready": len(cal) >= 3}


# ---------------------------------------------------------------- CLI
def _main():
    cmd, path = sys.argv[1], sys.argv[2]
    data = json.load(open(path))
    if cmd == "run":
        print(json.dumps(run(data), ensure_ascii=False, indent=1))
    elif cmd == "verify":
        print(json.dumps({"issues": verify(data, data["stored"])}, ensure_ascii=False, indent=1))
    elif cmd == "slip":
        cfg = load_config(data.get("config"))
        print(json.dumps(price_slip(data["legs"], data.get("grids", {}), cfg), ensure_ascii=False, indent=1))
    elif cmd == "evaluate":
        print(json.dumps(evaluate(data["graded"]), ensure_ascii=False, indent=1))
    else:
        raise SystemExit(f"unknown command {cmd}")


def price_slip(legs, grids, cfg):
    """legs: [{matchId, group, selection, prob (%), nEff}], grids: {matchId: grid}. Same-match legs
    are priced exactly from the grid when all are goal markets; otherwise the slip is shadow."""
    rules = "v2" if cfg["mode"] == "active" else "v1"
    by = {}
    for l in legs:
        by.setdefault(l["matchId"], []).append(l)
    parts = []
    for mid, ls in by.items():
        if len(ls) == 1:
            parts.append((ls[0]["prob"] / 100, ls[0].get("nEff", cfg["nEff"]["C"])))
            continue
        g = grids.get(mid)
        pj = None
        if g and all(l["group"] in cfg["goalFamilies"] for l in ls):
            pj = joint_grid_prob(g["lh"], g["la"], [(l["group"], l["selection"]) for l in ls], g["home"], g["away"], g["rho"])
        if pj is None:
            return {"shadow": True, "reason": "same-match legs outside goal markets need a joint model"}
        parts.append((pj, min(l.get("nEff", cfg["nEff"]["C"]) for l in ls)))
    P, mo = combo_min_odds(parts, cfg, rules)
    return {"shadow": False, "prob": round(P * 100, 2), "minOdds": mo, "rules": rules}


if __name__ == "__main__":
    _main()
