"""J.A.R.V.I.S. hockey model 1.2 (stdlib only). Production hockey model from 26 Nov 2026 (owner's decision).

This module only changes how each team's expected goals are estimated;
the result prices (Poisson grid, regulation draws, overtime split, puck line, totals) come from the
1.1 hockey pricing code, fed the 1.2 goal rates, so the comparison isolates the strength estimate.

Strength from SHOTS, not goals (goals in hockey are mostly shot volume times a very noisy percentage):
  per team, from its earlier games (exponential decay by games, half-life H; last season carried in,
  regressed one third to the league mean):
    shots_for, shots_against            decay-weighted per game
    team shooting %   = (goals + K_sh * league_sh%) / (shots + K_sh)          (heavy shrinkage)
    goalie save %     = (saves + K_sv * league_sv%) / (shots_against + K_sv)  (the STARTING goalie's
                         career-to-date record in this data; unknown starter -> team's own record)
  expected goals for team X against Y (home h, away a):
    lam_h = SF_h * sh%_h * (SA_a / L_sa) * ((1 - sv%_a) / (1 - L_sv)) * HA_h, similarly lam_a,
    where SF_h * (SA_a / L_sa) blends the home attack's volume with the away defence's shots allowed
    (geometric mean, as 1.1 does with goals), and back-to-back: a team that played the day before
    concedes (1 + B2B) times more.
  League constants L_* are recomputed from the training data only.
Inputs per match: {"home": TeamState, "away": TeamState, "starter_home": GoalieState|None, ...} built by
`build_states` from game rows (no look-ahead: only games strictly before the match date).
"""
import math

VERSION = "1.2"

# Chosen on NHL 2018/19-2022/23 only and frozen for the test seasons (research/hockey_backtest.json).
PARAMS = {"half_life": 90.0, "k_sh": 800.0, "k_sv": 800.0, "home_adv": 1.03, "b2b": 0.10, "carry": 2 / 3,
          "draw_inflation": 1.5}


def _w(age_games, hl):
    return 0.5 ** (age_games / hl)


class Team:
    """Running decay-weighted totals for one team (per game: shots for/against, goals for/against)."""

    __slots__ = ("sf", "sa", "gf", "ga", "n", "last_date")

    def __init__(self):
        self.sf = self.sa = self.gf = self.ga = self.n = 0.0
        self.last_date = None

    def add(self, sf, sa, gf, ga, hl, date):
        d = 0.5 ** (1 / hl)                     # decay one game
        self.sf, self.sa, self.gf, self.ga, self.n = (self.sf * d + sf, self.sa * d + sa, self.gf * d + gf,
                                                      self.ga * d + ga, self.n * d + 1)
        self.last_date = date

    def carry(self, f, lg=None):
        """New season: keep weight f of last season's per-game rates, as f*... games of evidence."""
        if self.n <= 0:
            return
        k = min(self.n, 20.0) * f / self.n      # at most 20 games' worth carried
        for a in ("sf", "sa", "gf", "ga"):
            setattr(self, a, getattr(self, a) * k)
        self.n *= k


def rates(t, P, L):
    """Per-game shots for/against and shooting % (shrunk to the league)."""
    n = max(t.n, 1e-9)
    sf, sa = t.sf / n, t.sa / n
    sh = (t.gf + P["k_sh"] * L["sh"]) / (t.sf + P["k_sh"]) if t.sf + P["k_sh"] > 0 else L["sh"]
    return sf, sa, sh


def goal_rates(home, away, g_home, g_away, L, P=None, b2b_home=False, b2b_away=False):
    """home/away: Team; g_home/g_away: (saves, shots_against) of the starting goalie or None."""
    P = P or PARAMS
    sf_h, sa_h, sh_h = rates(home, P, L)
    sf_a, sa_a, sh_a = rates(away, P, L)

    def sv(g, t):
        sv_, sa_ = g if g else (t.sf * 0 + (t.sa - t.ga), t.sa)     # unknown starter: team's own record
        return (sv_ + P["k_sv"] * L["sv"]) / (sa_ + P["k_sv"])

    sv_h, sv_a = sv(g_home, home), sv(g_away, away)
    vol_h = math.sqrt(max(sf_h, 1) * max(sa_a, 1))       # home shots expected: home attack x away defence
    vol_a = math.sqrt(max(sf_a, 1) * max(sa_h, 1))
    # shooting quality vs goaltending: shooter's shrunk sh% and the goalie's shrunk sv%, centred on the league
    q_h = sh_h * (1 - sv_a) / (1 - L["sv"])
    q_a = sh_a * (1 - sv_h) / (1 - L["sv"])
    lam_h = vol_h * q_h * P["home_adv"] * ((1 + P["b2b"]) if b2b_away else 1.0)
    lam_a = vol_a * q_a / P["home_adv"] * ((1 + P["b2b"]) if b2b_home else 1.0)
    return max(lam_h, 0.3), max(lam_a, 0.3)


class State:
    """Everything 1.2 knows after a sequence of games: team states, starting-goalie records, league totals.
    The backtest, the data robot and production all go through this class, so they compute the same numbers.
    update() order per game: season carry, then both teams, then both starters (as in the backtest)."""

    def __init__(self, P=None):
        self.P = dict(P or PARAMS)
        self.teams, self.goalies, self.season, self.abbrev = {}, {}, None, {}
        self.lg = {"sf": 0.0, "gf": 0.0, "sa_sv": 0.0, "sv": 0.0, "n": 0}
        self.games, self.through = 0, None

    def league(self):
        lg = self.lg
        return {"sh": lg["gf"] / lg["sf"] if lg["sf"] else 0.09, "sv": lg["sv"] / lg["sa_sv"] if lg["sa_sv"] else 0.905,
                "sa": lg["sf"] / max(lg["n"], 1) if lg["n"] else 30.0}

    def start_season(self, season):
        """A new season: every team keeps at most 20 games' worth of evidence, scaled by carry."""
        if season != self.season:
            if self.season is not None:
                for t in self.teams.values():
                    t.carry(self.P["carry"])
            self.season = season

    def update(self, season, date, rows, starters):
        """rows: [(team, shots_for, shots_against, goals_for, goals_against), ...] for the two teams;
        starters: [(goalie, saves, shots_against), ...] for the starting goalies that are known."""
        self.start_season(season)
        for name, sf, sa, gf, ga in rows:
            sf, sa = sf or 0, sa or 0
            self.teams.setdefault(name, Team()).add(sf, sa, gf, ga, self.P["half_life"], date)
            self.lg["sf"] += sf; self.lg["gf"] += gf; self.lg["n"] += 1
        for g, sv, sa in starters:
            if g and sa:
                r = self.goalies.setdefault(g, [0.0, 0.0])
                r[0] += sv; r[1] += sa
                self.lg["sv"] += sv; self.lg["sa_sv"] += sa
        self.games += 1
        self.through = max(self.through or date, date)

    def snapshot(self):
        return {"version": VERSION, "params": self.P, "season": self.season, "games": self.games,
                "gamesThrough": self.through, "leagueTotals": dict(self.lg), "league": self.league(),
                "teams": {k: {"sf": t.sf, "sa": t.sa, "gf": t.gf, "ga": t.ga, "n": t.n, "lastDate": t.last_date,
                              "abbrev": self.abbrev.get(k)} for k, t in sorted(self.teams.items())},
                "goalies": {k: v for k, v in sorted(self.goalies.items())}}

    @classmethod
    def from_snapshot(cls, s):
        st = cls(s["params"])
        st.season, st.games, st.through, st.lg = s["season"], s["games"], s["gamesThrough"], dict(s["leagueTotals"])
        for k, d in s["teams"].items():
            t = Team()
            t.sf, t.sa, t.gf, t.ga, t.n, t.last_date = d["sf"], d["sa"], d["gf"], d["ga"], d["n"], d["lastDate"]
            st.teams[k] = t
            if d.get("abbrev"):
                st.abbrev[k] = d["abbrev"]
        st.goalies = {k: list(v) for k, v in s["goalies"].items()}
        return st


def match_block(snap, home, away, goalie_home=None, goalie_away=None, b2b_home=False, b2b_away=False):
    """The exact numbers 1.2 needs for one match, copied from a state snapshot (production input "h12")."""
    T = snap["teams"]
    pick = lambda n: {k: T[n][k] for k in ("sf", "sa", "gf", "ga", "n", "lastDate")}
    g = snap["goalies"]

    def gk(name):   # a named starter with no record gets the league prior (as in the backtest); no starter -> team
        if not name:
            return None
        rec = g.get(name, [0.0, 0.0])
        return {"name": name, "saves": rec[0], "shotsAgainst": rec[1], "inData": name in g}
    return {"version": snap["version"], "params": snap["params"], "league": snap["league"],
            "home": dict(pick(home), team=home), "away": dict(pick(away), team=away),
            "goalieHome": gk(goalie_home), "goalieAway": gk(goalie_away),
            "b2bHome": bool(b2b_home), "b2bAway": bool(b2b_away), "gamesThrough": snap["gamesThrough"]}


def rates_from_block(b):
    """Production: expected goals (home, away) from an "h12" match block; params come from the block."""
    P = dict(PARAMS, **(b.get("params") or {}))

    def team(d):
        t = Team()
        t.sf, t.sa, t.gf, t.ga, t.n, t.last_date = d["sf"], d["sa"], d["gf"], d["ga"], d["n"], d.get("lastDate")
        return t
    g = lambda x: (x["saves"], x["shotsAgainst"]) if x else None
    return goal_rates(team(b["home"]), team(b["away"]), g(b.get("goalieHome")), g(b.get("goalieAway")),
                      b["league"], P, bool(b.get("b2bHome")), bool(b.get("b2bAway")))


def price(lam_h, lam_a, names=("Home", "Away"), P=None, players=None):
    """1.1 hockey pricing with the 1.2 goal rates (home advantage already inside the rates) and 1.2's own
    regulation-draw inflation (the only pricing parameter 1.2 changes). Player lines are priced as in 1.1."""
    import model                    # here, so the data robot can use State without model.py
    P = P or PARAMS
    m = {"sport": "hockey", "home_adv": 1.0, "home": {"name": names[0], "gf": lam_h, "ga": lam_a},
         "away": {"name": names[1], "gf": lam_a, "ga": lam_h}, "players": players or []}
    old = model.P["hockey_draw_inflation"]
    model.P["hockey_draw_inflation"] = P.get("draw_inflation", old)
    try:
        return model.run(m)
    finally:
        model.P["hockey_draw_inflation"] = old
