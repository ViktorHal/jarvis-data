"""Price EVERY upcoming match the robot has data for, in code, every hour (owner, 26 Nov 2026 evening).

The scans used to research each match on the web and only reached ~16 matches a run. This module builds the model
inputs for every match in the Pinnacle feed from the files already in this repo, runs jarvis/decision.py and writes:

  data/auto/drift.json    how Pinnacle's own no-vig probability has moved for every result line since the
                          first time it was seen today - the signal the price hunt is built on
  data/auto/board.json    every match priced: expected values, the most interesting lines, the products decision.py
                          chose (max two per match), the strong-favourites list, and every match it could not price
  data/auto/payload.json  the exact decision.py input (inputs, provenance, prices) so a scan can reuse a match,
                          add line-ups/injuries and re-run it

Inputs per football match (copied by code, never typed):
  goals   statz.ai xG (venue split blended 60/40 with the last five); no xG for the league -> football-data goals;
          no domestic data for a team at all (e.g. Cypriot clubs in Europe) -> goal rates fitted to Pinnacle's own
          1X2 and 2.5 prices ("market-implied": priced at the market, so those goal lines show no value by design)
  corners/cards  football-data team rates + league block (corners/cards v2)
  players FotMob shots/SoT per 90, avg minutes per start; only players who played in the team's last ~8 days
          (a long-injured player such as Brunner drops out by himself); line-ups are NOT known: every product with a
          player leg is flagged needsSquadCheck and the scan must check the squad before posting it
  league strength  Jarvis Elo league means when the clubs play in different leagues
  prices  Pinnacle (de-margined by decision.py), oddsAt = the odds file's fetchedAt
Hockey (NHL, SHL): hockey 1.2 state block (starters unknown -> league prior) + 1.1 inputs from teams.json.
"""
import difflib
import json
import math
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import DATA, ROOT, now_iso, write_json  # noqa: E402

JARVIS = os.path.join(ROOT, "jarvis")

# Pinnacle league -> (football-data code, statz.ai xG slug, FotMob players slug, Jarvis Elo league label)
LEAGUES = {
    "England - Premier League": ("E0", "premier-league", "premier-league", "Premier League (47)"),
    "England - Championship": ("E1", "championship", "championship", "Championship (48)"),
    "Spain - La Liga": ("SP1", "la-liga", "la-liga", "LaLiga (87)"),
    "Spain - Segunda Division": ("SP2", None, None, "LaLiga2 (140)"),
    "Italy - Serie A": ("I1", "serie-a", "serie-a", "Serie A (55)"),
    "Italy - Serie B": ("I2", None, None, "Serie B (86)"),
    "Germany - Bundesliga": ("D1", "bundesliga", "bundesliga", "Bundesliga (54)"),
    "Germany - Bundesliga 2": ("D2", None, None, "2. Bundesliga (146)"),
    "France - Ligue 1": ("F1", "ligue-1", "ligue-1", "Ligue 1 (53)"),
    "France - Ligue 2": ("F2", None, None, "Ligue 2 (110)"),
    "Netherlands - Eredivisie": ("N1", "eredivisie", "eredivisie", "Eredivisie (57)"),
    "Belgium - Pro League": ("B1", "belgian-pro-league", "belgian-pro-league", "First Division A (40)"),
    "Portugal - Primeira Liga": ("P1", "liga-portugal", "liga-portugal", "Liga Portugal (61)"),
    "Turkey - Super League": ("T1", "super-lig", "super-lig", "Süper Lig (71)"),
    "Greece - Super League": ("G1", None, None, "Super League 1 (135)"),
    "Scotland - Premiership": ("SC0", "scottish-premiership", "scottish-premiership", "Premiership (64)"),
    "Sweden - Allsvenskan": (None, "allsvenskan", "allsvenskan", "Allsvenskan (67)"),
    "Saudi Arabia - Pro League": (None, None, None, None),
}
BY_ELO = {v[3]: v for v in LEAGUES.values() if v[3]}
EUROPE = ("UEFA - Champions League", "UEFA - Europa League", "UEFA - Conference League")
HOCKEY = {"NHL": "nhl", "Sweden - SHL": "shl"}
HORIZON_H = 48          # matches starting within the next two days
DRIFT_KEEP_H = 60       # a drift entry is dropped this long after kick-off
CLOSE_KEEP_D = 45       # a closing price is kept this long, so the weekly report can measure realised CLV
MIN_START_MIN = 30

STOP = {"fc", "afc", "cf", "sc", "ac", "as", "ssc", "club", "de", "the", "cd", "ud", "rc", "rcd", "sd", "fk", "sk", "bk",
        "if", "ik", "calcio", "vfb", "vfl", "tsg", "1.", "sv", "bsc", "aik", "kv", "krc", "rsc", "royal", "cp", "sporting"}
ALIASES = {  # Pinnacle name (folded) -> other spellings, tried first
    "athletic bilbao": ["ath bilbao", "athletic club"], "atletico madrid": ["ath madrid", "atletico de madrid"],
    "espanyol": ["espanol"], "racing santander": ["santander"], "deportivo la coruna": ["la coruna"],
    "rayo vallecano": ["vallecano"], "real sociedad": ["sociedad"], "real betis": ["betis"], "celta vigo": ["celta", "celta de vigo"],
    "deportivo alaves": ["alaves"], "manchester united": ["man united"], "manchester city": ["man city"],
    "nottingham forest": ["nott'm forest"], "wolverhampton": ["wolves", "wolverhampton wanderers"],
    "internazionale": ["inter"], "ac milan": ["milan"], "borussia monchengladbach": ["m'gladbach", "monchengladbach"],
    "eintracht frankfurt": ["ein frankfurt"], "bayer leverkusen": ["leverkusen"], "bayern munich": ["bayern munchen"],
    "fc koln": ["koln", "fc cologne"], "paris saint-germain": ["paris sg", "psg"], "psv": ["psv eindhoven"],
    "sporting cp": ["sp lisbon", "sporting"], "vitoria guimaraes": ["guimaraes"], "sint truidense": ["st truiden", "sint-truiden"],
    "union saint-gilloise": ["st. gilloise", "union st.-gilloise"], "standard liege": ["standard"],
    "hearts": ["heart of midlothian"], "crvena zvezda": ["red star belgrade"], "fc copenhagen": ["fc kobenhavn", "copenhagen"],
    "tottenham hotspur": ["tottenham"], "newcastle united": ["newcastle"], "west ham united": ["west ham"],
    "leeds united": ["leeds"], "brighton": ["brighton and hove albion"], "az alkmaar": ["az"], "nec nijmegen": ["nec", "nijmegen"],
    "go ahead eagles": ["go ahead"], "fc twente": ["twente"], "fc utrecht": ["utrecht"], "fc nordsjaelland": ["nordsjaelland"],
    "rb leipzig": ["leipzig"], "mainz 05": ["mainz"], "hamburger sv": ["hamburg"], "werder bremen": ["werder"],
    "schalke 04": ["schalke"], "borussia dortmund": ["dortmund"], "olympiacos": ["olympiakos"], "omonia nicosia": ["omonia"],
    "qarabag": ["qarabag fk"], "sparta prague": ["sparta praha"], "slavia prague": ["slavia praha"],
    "bodo glimt": ["bodo/glimt"], "queens park rangers": ["qpr"], "dundee fc": ["dundee"], "preston north end": ["preston"], "lyon": ["olympique lyonnais", "olympique lyon"],
    "marseille": ["olympique marseille", "olympique de marseille"], "sheffield united": ["sheffield utd", "sheff utd"],
    "sheffield wednesday": ["sheffield weds", "sheff wed"], "oud-heverlee leuven": ["oh leuven"], "west bromwich albion": ["west brom"], "besiktas": ["besiktas jk"], "lasK linz": ["lask"],
}


def fold(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s or "")) if not unicodedata.combining(c)).lower()
    return s.replace("ø", "o").replace("æ", "ae").replace("ß", "ss").replace("ı", "i").replace("ł", "l").replace("đ", "d").replace("(corners)", "").strip()


GENERIC = {"city", "united", "town", "county", "rovers", "wanderers", "athletic", "albion", "utd", "fk", "hotspur"}


def toks(s):
    t = [t for t in re.split(r"[\s\-./']+", fold(s)) if t and t not in STOP]
    strong = [x for x in t if x not in GENERIC]
    return strong or t


def _tok_score(a, b):
    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return 0.0
    hit = 0
    for x in ta:
        if any(x == y or (len(x) >= 3 and len(y) >= 3 and (x.startswith(y) or y.startswith(x))) for y in tb):
            hit += 1
    return hit / max(len(ta), len(tb))


def match_name(q, names):
    """Best matching name in `names` for the Pinnacle name q, or None. Aliases first, then exact, then token score."""
    names = list(names)
    fq = fold(q)
    F = {fold(n): n for n in names}
    for cand in [fq] + [fold(x) for x in ALIASES.get(fq, [])]:
        if cand in F:
            return F[cand]
    scored = []
    for n in names:
        s = max([_tok_score(q, n)] + [_tok_score(x, n) for x in ALIASES.get(fq, [])])
        s += 0.15 * difflib.SequenceMatcher(None, fq, fold(n)).ratio()
        scored.append((s, n))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < 0.6:
        return None
    if len(scored) > 1 and scored[1][0] >= scored[0][0] - 0.05:
        return None                                  # ambiguous: better nothing than the wrong team
    return scored[0][1]


def load(rel):
    p = os.path.join(DATA, rel)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


class Repo:
    def __init__(self):
        self.cache = {}
        self.jelo = load("elo/jelo.json") or {"clubs": {}, "leagues": {}}
        self.commit = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or None

    def get(self, rel):
        if rel not in self.cache:
            self.cache[rel] = load(rel)
        return self.cache[rel]

    def elo_club(self, name, league_label=None):
        C = self.jelo["clubs"]
        pool = {k: c for k, c in C.items() if c.get("league") and (league_label is None or c["league"] == league_label)}
        by_name = {}
        for k, c in pool.items():
            if c["name"] not in by_name or c["games"] > pool[by_name[c["name"]]]["games"]:
                by_name[c["name"]] = k
        n = match_name(name, by_name)
        return C[by_name[n]] if n else None


def _prov(rel, meta, values, note=None, commit=None):
    e = {"status": "verified", "source": f"jarvis-data/data/{rel}", "repoCommit": commit,
         "repoFetchedAt": meta.get("fetchedAt"), "at": meta.get("fetchedAt"), "values": values}
    if note:
        e["note"] = note
    return e


def _missing(status, note):
    return {"status": status, "note": note}


def _date(s):
    try:
        return datetime.fromisoformat(str(s)[:10]).date()
    except Exception:
        return None


def xg_stale(xgf, fd):
    """statz.ai updates a league after its round; when its update date is more than 3 days before football-data's
    last league match, its xG misses games."""
    m = re.search(r"(\d{1,2}) (\w+) (\d{4})", (xgf.get("meta") or {}).get("updatedText", ""))
    if not m or not fd:
        return False
    try:
        upd = datetime.strptime(" ".join(m.groups()), "%d %B %Y").date()
    except ValueError:
        return False
    last = _date((fd.get("meta") or {}).get("lastMatchDate"))
    return bool(last and (last - upd).days > 3)


def team_side(repo, pin_name, league):
    """Everything the repo holds for one team, with the names used in each file. league = LEAGUES tuple."""
    code, xslug, fslug, elab = league
    out = {"pinnacle": pin_name, "league": elab}
    if code:
        fd = repo.get(f"football/teams_{code}.json")
        if fd:
            n = match_name(pin_name, fd["teams"])
            if n:
                out["fd"] = (f"football/teams_{code}.json", fd, n)
    if xslug:
        xg = repo.get(f"xg/{xslug}.json")
        if xg:
            n = match_name(pin_name, xg["teams"])
            if n:
                out["xg"] = (f"xg/{xslug}.json", xg, n)
    if fslug:
        pf = repo.get(f"players_fm/{fslug}.json")
        if pf and (pf.get("meta") or {}).get("complete"):
            n = match_name(pin_name, {p["team"] for p in pf["players"].values()})
            if n:
                out["fm"] = (f"players_fm/{fslug}.json", pf, n)
    return out


def blend(venue, last, k_all):
    """60/40 venue split and recent form; the season figure when the venue split is under four games."""
    return venue if last is None else 0.6 * venue + 0.4 * last


def goals_inputs(side, venue):
    """(xgf, xga, provenance values, note) or None."""
    if "xg" in side and not side.get("xgStale"):
        rel, f, n = side["xg"]
        t = f["teams"][n]
        v = t.get(venue) if (t.get(venue) or {}).get("played", 0) >= 4 else t["all"]
        l5 = t.get("last5") if (t.get("last5") or {}).get("played", 0) >= 5 else None
        xgf = blend(v["xg_pg"], l5 and l5["xg_pg"], None)
        xga = blend(v["xga_pg"], l5 and l5["xga_pg"], None)
        return xgf, xga, {"team": n, "source": f"jarvis-data/data/{rel}", "split": venue if v is t.get(venue) else "all",
                          "xg_pg": v["xg_pg"], "xga_pg": v["xga_pg"], "last5_xg_pg": l5 and l5["xg_pg"],
                          "last5_xga_pg": l5 and l5["xga_pg"], "meta": f["meta"]}, None
    if "fd" in side:
        rel, f, n = side["fd"]
        t = f["teams"][n]
        v = t.get(venue) if (t.get(venue) or {}).get("games", 0) >= 4 else t["all"]
        l6 = t.get("last6")
        xgf = blend(v["goals_for_pg"], l6 and l6.get("goals_for_pg"), None)
        xga = blend(v["goals_against_pg"], l6 and l6.get("goals_against_pg"), None)
        return xgf, xga, {"team": n, "source": f"jarvis-data/data/{rel}", "split": venue if v is t.get(venue) else "all",
                          "goals_for_pg": v["goals_for_pg"], "goals_against_pg": v["goals_against_pg"],
                          "last6_goals_for_pg": l6 and l6.get("goals_for_pg"), "meta": f["meta"]}, \
            "goals per game (football-data), no current xG for this team"
    return None


def fit_market_rates(ph, pa, pov, model):
    """Goal rates whose Dixon-Coles grid reproduces Pinnacle's de-margined home/away win and over-2.5 chances."""
    def probs(lh, la):
        g = model.score_grid(lh, la)
        n = len(g)
        h = sum(g[i][j] for i in range(n) for j in range(n) if i > j)
        a = sum(g[i][j] for i in range(n) for j in range(n) if j > i)
        o = sum(g[i][j] for i in range(n) for j in range(n) if i + j > 2)
        return h, a, o

    def err(lh, la):
        h, a, o = probs(lh, la)
        return (h - ph) ** 2 + (a - pa) ** 2 + (0 if pov is None else (o - pov) ** 2)

    best = min(((err(x / 4, y / 4), x / 4, y / 4) for x in range(1, 20) for y in range(1, 20)))
    _, lh, la = best
    step = 0.125
    while step > 0.004:
        cands = [(err(lh + dx, la + dy), lh + dx, la + dy) for dx in (-step, 0, step) for dy in (-step, 0, step)
                 if lh + dx > 0.05 and la + dy > 0.05]
        e, nlh, nla = min(cands)
        if (nlh, nla) == (lh, la):
            step /= 2
        lh, la = nlh, nla
    return lh, la


def novig(prices):
    inv = [1 / p for p in prices]
    s = sum(inv)
    return [x / s for x in inv]


def pinnacle_prices(ev, corners_ev, line_key, hn, an, sets=None):
    """Pinnacle num_0 prices -> {line key: price} in Jarvis' market names. `sets` collects the groups of keys
    that form one complete market, so they can be de-margined together later."""
    mo = {}
    sets = [] if sets is None else sets
    p0 = (ev.get("periods") or {}).get("num_0") or {}
    ml = p0.get("money_line") or {}
    if all(isinstance(ml.get(k), (int, float)) for k in ("home", "draw", "away")):
        mo[line_key("Match result", hn)] = ml["home"]
        mo[line_key("Match result", "Draw")] = ml["draw"]
        mo[line_key("Match result", an)] = ml["away"]
        sets.append([line_key("Match result", x) for x in (hn, "Draw", an)])
    for pts, t in (p0.get("totals") or {}).items():
        if pts in ("0.5", "1.5", "2.5", "3.5", "4.5") and t.get("over") and t.get("under"):
            mo[line_key("Total goals", f"Over {pts}")] = t["over"]
            mo[line_key("Total goals", f"Under {pts}")] = t["under"]
            sets.append([line_key("Total goals", f"{s_} {pts}") for s_ in ("Over", "Under")])
    for side, name in (("home", hn), ("away", an)):
        for pts, t in ((p0.get("team_totals") or {}).get(side) or {}).items():
            if pts in ("0.5", "1.5") and t.get("over"):
                mo[line_key("Team goals", f"{name} over {pts}")] = t["over"]
    if corners_ev:
        c0 = (corners_ev.get("periods") or {}).get("num_0") or {}
        for pts, t in (c0.get("totals") or {}).items():
            if pts.endswith(".5") and t.get("over") and t.get("under"):
                mo[line_key("Corners", f"Over {pts}")] = t["over"]
                mo[line_key("Corners", f"Under {pts}")] = t["under"]
                sets.append([line_key("Corners", f"{s_} {pts}") for s_ in ("Over", "Under")])
    return mo


def players_for(side, opp, venue_team, league_sot):
    """2-3 most frequent shooters who played recently (FotMob), with the opponent factor from football-data."""
    if "fm" not in side:
        return [], None
    rel, f, n = side["fm"]
    team = [dict(p, name=k) for k, p in f["players"].items() if p.get("team") == n]
    last = max((_date(p.get("lastDate")) for p in team if _date(p.get("lastDate"))), default=None)
    pool = [p for p in team if p.get("minutes", 0) >= 450 and p.get("starts", 0) >= 3 and p.get("avg_start_minutes")
            and last and _date(p.get("lastDate")) and (last - _date(p["lastDate"])).days <= 8]
    pool.sort(key=lambda p: -p.get("shots_p90", 0))
    opp_f = 1.0
    if "fd" in opp and league_sot:
        _, fdo, on = opp["fd"]
        s = (fdo["teams"][on].get("all") or {}).get("sot_against_pg")
        if s:
            opp_f = max(0.8, min(1.2, s / league_sot))
    out, vals = [], {}
    for p in pool[:3]:
        out.append({"name": p["name"], "team": venue_team, "shots_p90": p["shots_p90"], "sot_p90": p["sot_p90"],
                    "minutes": p.get("minutes"),          # how much he has played: the model regresses rare
                    "exp_minutes": p["avg_start_minutes"],  # statistics harder for a small sample
                    "opp_factor": round(opp_f, 3), "status": "probable"})
        vals[p["name"]] = {k: p.get(k) for k in ("team", "minutes", "starts", "avg_start_minutes", "shots_p90", "sot_p90", "lastDate")}
    return out, {"rel": rel, "meta": f["meta"], "values": vals}


def football_match(repo, ev, corners_ev, odds_meta, D, model):
    hn, an = ev["home"], ev["away"]
    if ev["league"] in EUROPE:
        lh_, la_ = repo.elo_club(hn), repo.elo_club(an)
        lg_h = BY_ELO.get(lh_["league"]) if lh_ else None
        lg_a = BY_ELO.get(la_["league"]) if la_ else None
    else:
        lg_h = lg_a = LEAGUES[ev["league"]]
        lh_ = repo.elo_club(hn, lg_h[3]) if lg_h[3] else None
        la_ = repo.elo_club(an, lg_a[3]) if lg_a[3] else None
    H = team_side(repo, hn, lg_h) if lg_h else {"pinnacle": hn}
    A = team_side(repo, an, lg_a) if lg_a else {"pinnacle": an}
    for S in (H, A):
        if "xg" in S and "fd" in S and xg_stale(S["xg"][1], S["fd"][1]):
            S["xgStale"] = True
    sets = []
    mo = pinnacle_prices(ev, corners_ev, D.line_key, hn, an, sets)
    if len(mo) < 3:
        return None, "no complete Pinnacle 1X2"
    at = odds_meta.get("fetchedAt")
    prov, notes = {}, []
    inputs = {"home": {"name": hn}, "away": {"name": an}}
    gh, ga = goals_inputs(H, "home"), goals_inputs(A, "away")
    ml = ev["periods"]["num_0"]["money_line"]
    ph, _, pa = novig([ml["home"], ml["draw"], ml["away"]])
    t25 = (ev["periods"]["num_0"].get("totals") or {}).get("2.5") or {}
    pov = novig([t25["over"], t25["under"]])[0] if t25.get("over") and t25.get("under") else None
    if gh and ga:
        inputs["home"].update(xgf=round(gh[0], 3), xga=round(gh[1], 3))
        inputs["away"].update(xgf=round(ga[0], 3), xga=round(ga[1], 3))
        meta = gh[2].pop("meta")
        ga[2].pop("meta")
        prov["F1"] = {"status": "verified", "source": gh[2]["source"], "repoCommit": repo.commit,
                      "repoFetchedAt": meta.get("fetchedAt"), "at": meta.get("fetchedAt"),
                      "values": {"home": gh[2], "away": ga[2]}, "note": "; ".join(x for x in (gh[3], ga[3]) if x) or None}
        prov["F1b"] = dict(prov["F1"], note="venue split in F1 values")
        prov["F1c"] = dict(prov["F1"], note="last five (xG) / last six (goals) in F1 values")
        model_kind = "stats"
    else:
        # no domestic statistics for at least one side: goal rates from Pinnacle's own prices (honest: no edge claimed)
        lam_h, lam_a = fit_market_rates(ph, pa, pov, model)
        ha, gs = model.P["home_adv"], model.P["goal_scale"]
        inputs["home"].update(xgf=round(lam_h / ha / gs, 4), xga=round(lam_a * ha / gs, 4))
        inputs["away"].update(xgf=round(lam_a * ha / gs, 4), xga=round(lam_h / ha / gs, 4))
        missing = [S["pinnacle"] for S, g in ((H, gh), (A, ga)) if not g]
        prov["F1"] = {"status": "verified", "source": "jarvis-data/data/pinnacle/odds.json", "repoCommit": repo.commit,
                      "repoFetchedAt": at, "at": at, "values": {"lam_home": round(lam_h, 3), "lam_away": round(lam_a, 3)},
                      "note": f"market-implied goal rates (Pinnacle 1X2 + 2.5): no team statistics for {', '.join(missing)}"}
        prov["F1b"] = _missing("not_available", "market-implied")
        prov["F1c"] = _missing("not_available", "market-implied")
        model_kind = "market"
    # league strength for cross-league ties
    if model_kind == "stats" and lh_ and la_ and lh_["league"] != la_["league"]:
        L = repo.jelo.get("leagues", {})
        mh, ma = (L.get(lh_["league"]) or {}).get("mean"), (L.get(la_["league"]) or {}).get("mean")
        if mh and ma:
            inputs["league_elo"] = {"home": mh, "away": ma}
            prov["F7"] = _prov("elo/jelo.json", repo.jelo["meta"], {"home": lh_, "away": la_, "league_elo": inputs["league_elo"]},
                               commit=repo.commit)
    # corners and cards (football-data, both sides needed)
    if "fd" in H and "fd" in A:
        relh, fdh, nh = H["fd"]
        rela, fda, na = A["fd"]
        th, ta = fdh["teams"][nh], fda["teams"][na]
        sh = th["home"] if th["home"].get("corners_games", 0) >= 5 else th["all"]
        sa = ta["away"] if ta["away"].get("corners_games", 0) >= 5 else ta["all"]
        inputs["home"].update(corners_for=sh["corners_for_pg"], corners_against=sh["corners_against_pg"],
                              corners_n=sh["corners_games"], cards=th["all"]["yellow_for_pg"], cards_n=th["all"]["yellow_games"])
        inputs["away"].update(corners_for=sa["corners_for_pg"], corners_against=sa["corners_against_pg"],
                              corners_n=sa["corners_games"], cards=ta["all"]["yellow_for_pg"], cards_n=ta["all"]["yellow_games"])
        inputs["league"] = fdh["league"]
        vals = {"home": {"team": nh, "source": f"jarvis-data/data/{relh}", "split": "home" if sh is th["home"] else "all",
                         "corners_for_pg": sh["corners_for_pg"], "corners_against_pg": sh["corners_against_pg"]},
                "away": {"team": na, "source": f"jarvis-data/data/{rela}", "split": "away" if sa is ta["away"] else "all",
                         "corners_for_pg": sa["corners_for_pg"], "corners_against_pg": sa["corners_against_pg"]},
                "league": fdh["league"]}
        prov["F2"] = _prov(relh, fdh["meta"], vals, None if relh == rela else "cross-league: league block of the home team", repo.commit)
        prov["F3"] = _prov(relh, fdh["meta"], {"home": {"team": nh, "source": f"jarvis-data/data/{relh}", "split": "all",
                                                         "yellow_for_pg": th["all"]["yellow_for_pg"]},
                                                "away": {"team": na, "source": f"jarvis-data/data/{rela}", "split": "all",
                                                         "yellow_for_pg": ta["all"]["yellow_for_pg"]}}, None, repo.commit)
    else:
        prov["F2"] = _missing("not_available", "no football-data team file for one side")
        prov["F3"] = _missing("not_available", "no football-data team file for one side")
    prov["F4"] = _missing("not_published", "referee not known to the robot")
    # players
    league_sot = None
    if "fd" in H:
        T = H["fd"][1]["teams"].values()
        xs = [t["all"].get("sot_against_pg") for t in T if t["all"].get("sot_against_pg")]
        league_sot = sum(xs) / len(xs) if xs else None
    ph_, pv_h = players_for(H, A, "home", league_sot)
    pa_, pv_a = players_for(A, H, "away", league_sot)
    if ph_ or pa_:
        inputs["players"] = ph_ + pa_
        pv = pv_h or pv_a
        prov["F5"] = _prov(pv["rel"], pv["meta"], {**(pv_h or {}).get("values", {}), **(pv_a or {}).get("values", {})},
                           "FotMob rates; line-ups and injuries NOT checked by the robot (squad check before posting)", repo.commit)
    else:
        prov["F5"] = _missing("not_available", "no FotMob player file for these teams")
    prov["F6"] = {"status": "verified", "source": "Pinnacle via pinnapi (jarvis-data)", "at": at}
    mid = f'{ev["starts"][:10]}-{re.sub(r"[^a-z0-9]+", "-", fold(hn)).strip("-")}-{re.sub(r"[^a-z0-9]+", "-", fold(an)).strip("-")}'
    m = {"id": mid, "event": f"{hn} – {an}", "sport": "Football", "league": ev["league"], "start": ev["starts"],
         "inputs": inputs, "flags": {"defaulted": 0, "stale": 0, "lineup": "unknown", "refereeKnown": False,
                                     "priceSources": 1, "primarySource": model_kind == "stats"},
         "marketOdds": mo, "marketSources": {k: "Pinnacle via pinnapi (jarvis-data)" for k in mo},
         "oddsAt": {k: at for k in mo}, "provenance": prov, "pinnacleEventId": ev.get("event_id"),
         "auto": {"goalModel": model_kind, "names": {"home": {k: H[k][2] for k in ("fd", "xg", "fm") if k in H},
                                                     "away": {k: A[k][2] for k in ("fd", "xg", "fm") if k in A}},
                  "xgStale": [S["pinnacle"] for S in (H, A) if S.get("xgStale")]},
         "priceSets": sets}
    return m, None


def hockey_match(repo, ev, odds_meta, D):
    lg = HOCKEY[ev["league"]]
    snap = repo.get(f"{lg}/h12_state.json")
    teams = repo.get(f"{lg}/teams.json")
    if not snap or not teams:
        return None, "no hockey state"
    import h12_match
    day = (datetime.fromisoformat(ev["starts"].replace("Z", "+00:00")) - timedelta(hours=8 if lg == "nhl" else 0)).date().isoformat()
    T = teams["teams"]
    names = {k: (v.get("name") or k) for k, v in T.items()}

    def tk(q):
        n = match_name(q, names.values())
        return next((k for k, v in names.items() if v == n), None) if n else None
    kh, ka = tk(ev["home"]), tk(ev["away"])
    if not kh or not ka:
        return None, f"team not found: {ev['home'] if not kh else ev['away']}"
    try:
        b = h12_match.block(snap, kh, ka, day, None, None, lg)
    except KeyError as e:
        return None, str(e)

    def gfga(t):
        gf = 0.5 * t["gf_pg"] + 0.5 * t.get("l10_gf_pg", t["gf_pg"])
        ga = 0.5 * t["ga_pg"] + 0.5 * t.get("l10_ga_pg", t["ga_pg"])
        return round(gf, 3), round(ga, 3)
    hgf, hga = gfga(T[kh])
    agf, aga = gfga(T[ka])
    hn, an = ev["home"], ev["away"]
    inputs = {"home": {"name": hn, "gf": hgf, "ga": hga}, "away": {"name": an, "gf": agf, "ga": aga}}
    ml = ((ev.get("periods") or {}).get("num_0") or {}).get("money_line") or {}
    mo, sets = {}, []
    if ml.get("home") and ml.get("away"):
        mo[D.line_key("Moneyline (incl. OT)", hn)] = ml["home"]
        mo[D.line_key("Moneyline (incl. OT)", an)] = ml["away"]
        sets = [[D.line_key("Moneyline (incl. OT)", x) for x in (hn, an)]]
    at = odds_meta.get("fetchedAt")
    rel = f"{lg}/h12_state.json"
    prov = {"H1": _prov(f"{lg}/teams.json", teams["meta"], {"home": {"team": kh, **{k: T[kh].get(k) for k in ("gf_pg", "ga_pg", "l10_gf_pg", "l10_ga_pg")}},
                                                            "away": {"team": ka, **{k: T[ka].get(k) for k in ("gf_pg", "ga_pg", "l10_gf_pg", "l10_ga_pg")}}}, None, repo.commit),
            "H2": _missing("not_published", "starting goalies not known to the robot (league prior)"),
            "H6": {"status": "verified", "source": f"jarvis-data/data/{rel}", "repoCommit": repo.commit,
                   "repoFetchedAt": snap["meta"]["fetchedAt"], "at": snap["meta"]["fetchedAt"],
                   "values": {"home": b["home"], "away": b["away"], "league": b["league"]}, "note": "built by fetch/autoprice.py; starters unknown"},
            "H5": {"status": "verified", "source": "Pinnacle via pinnapi (jarvis-data)", "at": at}}
    prov["H1b"] = dict(prov["H1"], note="last-10 in H1 values")
    prov["H3"] = dict(prov["H6"], note="back-to-back from the h12 state")
    mid = f'{ev["starts"][:10]}-{re.sub(r"[^a-z0-9]+", "-", fold(hn)).strip("-")}-{re.sub(r"[^a-z0-9]+", "-", fold(an)).strip("-")}'
    return {"id": mid, "event": f"{hn} – {an}", "sport": "Ice hockey", "league": ev["league"], "start": ev["starts"],
            "inputs": inputs, "h12": b, "flags": {"defaulted": 0, "stale": 0, "lineup": "unknown", "priceSources": 1, "primarySource": True},
            "marketOdds": mo, "marketSources": {k: "Pinnacle via pinnapi (jarvis-data)" for k in mo},
            "oddsAt": {k: at for k in mo}, "provenance": prov, "pinnacleEventId": ev.get("event_id"),
            "auto": {"goalModel": "h12", "names": {"home": kh, "away": ka}}, "priceSets": sets}, None


BOOK_MAX_AGE_H = 6       # a bookmaker price older than this is not a price he could take now


def attach_book_odds(matches, repo, now, D):
    """3.13: the prices his OWN bookmakers publish, from data/oddsapi/odds.json (fetch/oddsapi.py, region se:
    BetMGM, Svenska Spel and the rest). decision.py then compares its minimum with what he can actually get,
    so a pick is only posted when a bookmaker already pays it - which is the whole point, because going
    through thirty matches at three bookmakers by hand is a full-time job.
    Hockey totals are deliberately ignored: like Pinnacle's, they include overtime, and Jarvis' total-goals
    lines are regulation time only."""
    feed = repo.get("oddsapi/odds.json") or {}
    at = ((feed.get("meta") or {}).get("fetchedAt") or "")
    if not feed.get("events"):
        return 0
    try:
        age = (now - datetime.fromisoformat(at.replace("Z", "+00:00"))).total_seconds() / 3600
    except Exception:
        return 0
    if age > BOOK_MAX_AGE_H or age < -1:
        print(f"bookOdds ignored: feed is {age:.1f} h old", file=sys.stderr)
        return 0
    by = {}
    for ev in feed["events"]:
        by[(fold(ev.get("home")), fold(ev.get("away")))] = ev
    hit = 0
    for m in matches:
        names = ((m.get("auto") or {}).get("names") or {})
        hn, an = names.get("home") or (m.get("inputs") or {}).get("home", {}).get("name"),             names.get("away") or (m.get("inputs") or {}).get("away", {}).get("name")
        jh = (m.get("inputs") or {}).get("home", {}).get("name") or hn
        ja = (m.get("inputs") or {}).get("away", {}).get("name") or an
        ev = None
        for (fh, fa), cand in by.items():
            if not (fh and fa):
                continue
            if (fold(hn)[:6] in fh or fh[:6] in fold(hn)) and (fold(an)[:6] in fa or fa[:6] in fold(an)):
                ev = cand
                break
        if ev is None:
            continue
        hockey = str(m.get("sport", "")).lower().startswith("ice") or m.get("h12") is not None
        out = {}
        for bk, mk in (ev.get("books") or {}).items():
            h2h = mk.get("h2h") or {}
            if h2h.get("home") and h2h.get("away"):
                if hockey:
                    out.setdefault(D.line_key("Moneyline (incl. OT)", jh), {})[bk] = h2h["home"]
                    out.setdefault(D.line_key("Moneyline (incl. OT)", ja), {})[bk] = h2h["away"]
                elif h2h.get("draw"):
                    out.setdefault(D.line_key("Match result", jh), {})[bk] = h2h["home"]
                    out.setdefault(D.line_key("Match result", "Draw"), {})[bk] = h2h["draw"]
                    out.setdefault(D.line_key("Match result", ja), {})[bk] = h2h["away"]
            if hockey:
                continue
            for line, oversunder in (mk.get("totals") or {}).items():
                try:
                    L = float(line)
                except (TypeError, ValueError):
                    continue
                for side, sel in (("over", f"Over {L:g}"), ("under", f"Under {L:g}")):
                    if oversunder.get(side):
                        out.setdefault(D.line_key("Total goals", sel), {})[bk] = oversunder[side]
        if out:
            m["bookOdds"] = out
            m["bookOddsAt"] = at
            hit += 1
    print(f"bookOdds: {hit} of {len(matches)} matches priced by his own bookmakers ({at})", file=sys.stderr)
    return hit


def drift_update(matches, now, path="auto/drift.json"):
    """Keep, per match and line, the first and the latest de-margined Pinnacle probability seen. Jarvis bets
    with the movement, not against it: research/line_drift.json."""
    prev = load(path) or {}
    lines = prev.get("lines") or {}
    cutoff = (now - timedelta(hours=DRIFT_KEEP_H)).strftime("%Y-%m-%dT%H:%M")
    lines = {k: v for k, v in lines.items() if (v.get("start") or "") >= cutoff}
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for m in matches:
        mo = m.get("marketOdds") or {}
        for keys in m.get("priceSets") or []:
            vals = [mo.get(k) for k in keys]
            if len(keys) < 2 or not all(isinstance(v, (int, float)) and v > 1 for v in vals):
                continue
            tot = sum(1 / v for v in vals)
            for k, v in zip(keys, vals):
                p = round((1 / v) / tot, 5)
                e = lines.get(f'{m["id"]}|{k}')
                if e is None:
                    lines[f'{m["id"]}|{k}'] = {"start": m.get("start"), "firstAt": stamp, "first": p,
                                               "lastAt": stamp, "last": p, "n": 1}
                else:
                    e.update(lastAt=stamp, last=p, n=e.get("n", 1) + 1)
    write_json(path, {"meta": {"fetchedAt": now_iso(), "lines": len(lines),
                               "note": "first and latest de-margined Pinnacle probability per line; "
                                       "drift = last - first"}, "lines": lines})
    closing_update(lines, now)
    return lines


def closing_update(lines, now, path="auto/closing.json"):
    """The last de-margined Pinnacle probability seen BEFORE kick-off, kept per line for CLOSE_KEEP_D days.
    This is what a bet is really measured against: a price better than the market's closing price is the only
    kind of edge that has ever survived a backtest (research/line_drift.json, research/value_backtest.json).
    Written once per line and never overwritten, so the weekly report can compute realised closing-line value."""
    out = load(path) or {}
    rows = out.get("lines") or {}
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    keep = (now - timedelta(days=CLOSE_KEEP_D)).strftime("%Y-%m-%dT%H:%M")
    rows = {k: v for k, v in rows.items() if (v.get("start") or "") >= keep}
    for k, e in lines.items():
        if k in rows or not e.get("start") or e["start"] > stamp:
            continue                       # not started yet, or already recorded
        rows[k] = {"start": e["start"], "close": e["last"], "closeAt": e["lastAt"],
                   "open": e["first"], "openAt": e["firstAt"], "n": e.get("n", 1)}
    write_json(path, {"meta": {"fetchedAt": now_iso(), "lines": len(rows), "keptDays": CLOSE_KEEP_D,
                               "note": "last de-margined Pinnacle probability before kick-off, per line "
                                       "(key = matchId|lineKey); for measuring closing-line value"}, "lines": rows})
    return rows


def load_jarvis():
    sys.path.insert(0, JARVIS)
    import decision as D
    import model
    try:
        import hockey12  # noqa: F401
        D.hockey12 = hockey12
    except Exception:
        pass
    return D, model


def build(now=None):
    now = now or datetime.now(timezone.utc)
    if isinstance(now, str):
        now = datetime.fromisoformat(now.replace("Z", "+00:00"))
    D, model = load_jarvis()
    repo = Repo()
    odds = repo.get("pinnacle/odds.json") or {"events": [], "meta": {}}
    settings = json.load(open(os.path.join(JARVIS, "robot_settings.json"), encoding="utf-8"))
    corners = {}
    for e in odds["events"]:
        if e["league"].endswith(" Corners"):
            corners[(e["league"][:-8], fold(e["home"]), fold(e["away"]))] = e
    matches, skipped = [], []
    for e in odds["events"]:
        lg = e["league"]
        if not (lg in LEAGUES or lg in EUROPE or lg in HOCKEY):
            continue
        try:
            st = datetime.fromisoformat(e["starts"].replace("Z", "+00:00"))
        except Exception:
            continue
        mins = (st - now).total_seconds() / 60
        if mins < MIN_START_MIN or mins > HORIZON_H * 60:
            continue
        try:
            if lg in HOCKEY:
                m, why = hockey_match(repo, e, odds["meta"], D)
            else:
                m, why = football_match(repo, e, corners.get((lg, fold(e["home"]), fold(e["away"]))), odds["meta"], D, model)
        except Exception as ex:                    # one odd match never stops the board
            m, why = None, f"error: {type(ex).__name__}: {ex}"[:200]
        if m:
            matches.append(m)
        else:
            skipped.append({"event": f'{e["home"]} – {e["away"]}', "league": lg, "start": e["starts"], "reason": why})
    try:
        drift = drift_update(matches, now)
        for m in matches:
            d = {}
            for k in (m.get("marketOdds") or {}):
                e = drift.get(f'{m["id"]}|{k}')
                if e and e.get("n", 1) > 1:
                    d[k] = {"move": round(e["last"] - e["first"], 5), "first": e["first"], "since": e["firstAt"]}
            if d:
                m["drift"] = d
    except Exception as e:                       # the drift file must never stop a run
        print(f"drift skipped: {e}", file=sys.stderr)
    try:
        attach_book_odds(matches, repo, now, D)
    except Exception as e:                       # the bookmaker feed must never stop a run
        print(f"bookOdds skipped: {e}", file=sys.stderr)
    payload = {"now": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "markets": settings["markets"], "config": settings["config"],
               "openBets": [], "matches": matches}
    out = D.run(payload)
    for b in out["products"]:
        b["needsSquadCheck"] = any(r.get("group", "").startswith("Player") for _, r in D._rows_of(b))
    # Ranking for the scan, which posts the best of these. Value singles first (they beat Pinnacle's own price,
    # the only kind with a measured edge), then every product whose price rests on the market - Pinnacle's own
    # de-margined price or the grid fitted to it (decision.py 3.10) - then the rest, which are the model's word
    # alone; within each group the ones needing a squad check come last and the likeliest first.
    dmin = 100 * (D.DEFAULT_CONFIG.get("hunt") or {}).get("driftMin", 0.02)

    def rank(b):
        conf = b.get("confidence") if b.get("confidence") is not None else b["line"]["prob"]
        rows = [r for _, r in D._rows_of(b)]
        value = "line" in b and b["line"].get("odds") and not b.get("kind")
        market = all(r.get("anchored") for r in rows)
        moving = market and all((r.get("drift") or {}).get("move", -9) >= dmin for r in rows)
        return (0 if value else 1 if moving else 2 if market else 3, b["needsSquadCheck"], -conf)
    out["products"].sort(key=rank)
    for i, b in enumerate(out["products"]):
        b["rank"] = i + 1
    # compact board for the scan and the HUD
    board = []
    pm = {m["id"]: m for m in matches}
    for r in out["matches"]:
        rows = sorted(r["markets"], key=lambda x: -x["prob"])
        keep = [x for x in rows if x["v1"]["post"] or x["v2"]["post"] or (x["prob"] >= 60 and x["prob"] < 97)]
        board.append({"id": r["id"], "event": r["event"], "sport": r["sport"], "league": pm[r["id"]]["league"], "start": r["start"],
                      "expected": r["expected"], "goalModel": pm[r["id"]]["auto"]["goalModel"],
                      "coverage": (r.get("coverage") or {}).get("pct"),
                      "lines": [{k: x.get(k) for k in ("group", "selection", "key", "prob", "minOdds", "odds", "pMarket", "gap", "ev", "status", "anchored", "probModel", "drift")}
                                | {"post": x[out["rules"]]["post"]} for x in keep[:25]]})
    meta = {"fetchedAt": now_iso(), "oddsFetchedAt": odds["meta"].get("fetchedAt"), "repoCommit": repo.commit,
            "decisionVersion": out["decisionVersion"], "rules": out["rules"], "profile": out["profile"],
            "matches": len(matches), "skipped": len(skipped), "products": len(out["products"]),
            "note": "priced by code from repo data and Pinnacle; line-ups, injuries and referees are NOT known to the robot"}
    return {"meta": meta, "products": out["products"], "favourites": out["favourites"], "hunts": out.get("hunts", []), "matches": board,
            "skipped": skipped, "summary": out["summary"]}, payload


def run(log):
    try:
        board, payload = build()
        write_json("auto/board.json", board)
        write_json("auto/payload.json", {"meta": board["meta"], **payload})
        log.ok("autoprice", "data/auto/board.json", board["meta"]["matches"])
    except Exception as e:
        log.fail("autoprice", "data/auto/board.json", e)


if __name__ == "__main__":
    b, p = build(sys.argv[1] if len(sys.argv) > 1 else None)
    write_json("auto/board.json", b)
    write_json("auto/payload.json", {"meta": b["meta"], **p})
    print(json.dumps(b["meta"]))
