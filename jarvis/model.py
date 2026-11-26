"""J.A.R.V.I.S. pricing model v1.1 (tunable).

Usage: python3 model.py input.json  ->  prints JSON with fair prices per market.
input.json is one match object or a list of them.

Football match object:
{"sport":"football","event":"PSG – Roma","home":{"name":"PSG","xgf":2.1,"xga":0.9,"corners_for":6.5,
 "corners_against":3.4,"cards":1.6},"away":{...},"home_adv":1.05,
 "referee":{"name":"X","yellow_pg":4.6,"red_rate":0.12},"intensity":1.0,
 "players":[{"name":"Dembélé","team":"home","sot_p90":1.3,"shots_p90":3.4,"exp_minutes":80,"opp_factor":1.0}]}
  - xgf/xga: expected (or actual) goals for/against per game, ideally venue-specific.
  - intensity: multiplier for cards (derby, relegation fight 1.1-1.25; dead rubber 0.9).
Hockey: {"sport":"hockey","home":{"name":..,"gf":3.2,"ga":2.6},"away":{..},"home_adv":1.04,
 "ot_home_share":0.52,"players":[{"name":..,"sog_pg":3.1,"points_pg":0.9}]}
Basketball: {"sport":"basketball","pred_margin":4.0,"pred_total":226.0,"sd_margin":12.5,"sd_total":17.5,
 "spreads":[-4.5,-6.5],"totals":[224.5],"players":[{"name":..,"stat":"points","mean":27.1,"sd":7.0,"line":26.5}]}
"""
import json, math, os, sys

# Tunable parameters. A params.json next to this file overrides any of them
# (written from the database document "brain/params" by the weekly calibration).
P = {
    "cushion": 1.03,          # Jarvis wants 3% expected value over break-even
    "dc_rho": -0.06,          # Dixon-Coles low-score correction
    "home_adv": 1.05,         # football home multiplier on expected goals
    "goal_scale": 1.0,        # global multiplier on expected goals (calibrates over/under)
    "first_half_share": 0.45, # share of goals before half-time
    "corners_r": 25,          # negative-binomial dispersion for match corners
    "team_corners_r": 20,
    "corner_scale": 1.0,      # multiplier on expected corners
    "cards_r": 10,
    "ref_weight": 0.5,        # weight of the referee average vs the teams' card averages
    "card_scale": 1.0,
    "red_default": 0.11,
    # corners/cards v2 (26 Nov 2026, research/corners_cards_v2.json: football-data 2021-2026, chosen on
    # 2021/22-2023/24, tested on 2024/25-2025/26). Used only when the input carries a "league" block.
    "cc_k": 10,               # games of shrinkage of a team rate toward the league venue mean
    "cc_w": 0.6,              # weight of the (shrunk) team rates vs the market-based estimate
    "cc_b": 1.2,              # market dominance: corners ~ exp(b * (own win prob - opponent's))
    "cc_team_r": 8,           # NB dispersion for team corners
    "cc_team_shrink": 0.9,    # team-corner probabilities pulled 10% toward 50% (calibration)
    "cc_total_league": 0.5,   # total corners: weight of the league average (team stats add little there)
    "cc_total_r": 15,
    "cards_k": 10, "cards_wt": 0.5, "cards_wr": 0.5, "cards_d": 0.3, "cards2_r": 20,
    # cross-league ties (26 Nov 2026): team xG is measured against each team's own league, so the gap between the
    # two leagues' mean Jarvis Elo moves the goal rates: lam * exp(+-elo_c * (league_home - league_away) / 400).
    # elo_c fitted on 17,106 domestic matches 2024-2026 (Poisson goals on Elo difference) and checked on 1,062
    # European club matches (log-loss 3.059 with it v 3.180 without).
    "elo_c": 0.7,
    # player shots (26 Nov 2026, research/player_shots_backtest.json: FotMob 2025/26 top five leagues, 7,510 starts of
    # regular shooters): counts are negative binomial with r 3, not Poisson (Poisson was 10-13 points too
    # confident: "74%" lines landed 61%). exp_minutes should be the player's average minutes per start.
    "player_r": 3,
    "player_scale": 1.0,      # multiplier on player shot rates
    "hockey_home_adv": 1.04,
    "hockey_draw_inflation": 1.08,
    "hockey_ot_home_share": 0.52,
    "nba_sd_margin": 12.5,
    "nba_sd_total": 17.5,
}
_pp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "params.json")
if os.path.exists(_pp):
    try:
        P.update({k: v for k, v in json.load(open(_pp)).items() if k in P})
    except Exception as e:  # a bad params file must never stop the sweep
        print(f"params.json ignored: {e}", file=sys.stderr)
CUSHION = P["cushion"]


def price(group, selection, p):
    p = max(min(p, 0.995), 0.005)
    return {"group": group, "selection": selection, "prob": round(p * 100, 1),
            "fair": round(1 / p, 2), "minOdds": math.ceil(CUSHION / p * 100) / 100}


def pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def nb_pmf(k, mu, r):
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(math.lgamma(k + r) - math.lgamma(k + 1) - math.lgamma(r)
                    + r * math.log(r / (r + mu)) + k * math.log(mu / (r + mu)))


def nb_over(line, mu, r):
    return 1 - sum(nb_pmf(k, mu, r) for k in range(int(math.floor(line)) + 1))


def pois_over(line, lam):
    return 1 - sum(pois(k, lam) for k in range(int(math.floor(line)) + 1))


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def score_grid(lh, la, rho=None, n=11):
    rho = P["dc_rho"] if rho is None else rho
    g = [[pois(i, lh) * pois(j, la) for j in range(n)] for i in range(n)]
    # Dixon-Coles low-score correction
    g[0][0] *= 1 - lh * la * rho
    g[0][1] *= 1 + lh * rho
    g[1][0] *= 1 + la * rho
    g[1][1] *= 1 - rho
    s = sum(map(sum, g))
    return [[v / s for v in row] for row in g]


def goal_rates(m):
    """Expected goals (home, away): geometric blend of attack and defence, home advantage, goal scale, and the
    cross-league correction when the input carries league_elo {home, away} (league mean Jarvis Elo)."""
    h, a = m["home"], m["away"]
    ha = m.get("home_adv", P["home_adv"])
    gs = P["goal_scale"]
    lh = math.sqrt(h["xgf"] * a["xga"]) * ha * gs
    la = math.sqrt(a["xgf"] * h["xga"]) / ha * gs
    le = m.get("league_elo") or {}
    if isinstance(le.get("home"), (int, float)) and isinstance(le.get("away"), (int, float)):
        f = math.exp(P["elo_c"] * (le["home"] - le["away"]) / 400)
        lh, la = lh * f, la / f
    return lh, la


def football(m):
    h, a = m["home"], m["away"]
    ha = m.get("home_adv", P["home_adv"])
    gs = P["goal_scale"]
    lh, la = goal_rates(m)
    g = score_grid(lh, la)
    n = len(g)
    ph = sum(g[i][j] for i in range(n) for j in range(n) if i > j)
    pd = sum(g[i][i] for i in range(n))
    pa = 1 - ph - pd
    hn, an = h.get("name", "Home"), a.get("name", "Away")
    out = [price("Match result", hn, ph), price("Match result", "Draw", pd), price("Match result", an, pa),
           price("Double chance", f"{hn} or draw", ph + pd), price("Double chance", f"{an} or draw", pa + pd),
           price("Double chance", f"{hn} or {an}", ph + pa),
           price("Draw no bet", hn, ph / (1 - pd)), price("Draw no bet", an, pa / (1 - pd))]
    tot = {}
    for i in range(n):
        for j in range(n):
            tot[i + j] = tot.get(i + j, 0) + g[i][j]
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        ov = sum(p for k, p in tot.items() if k > line)
        out += [price("Total goals", f"Over {line}", ov), price("Total goals", f"Under {line}", 1 - ov)]
    btts = sum(g[i][j] for i in range(1, n) for j in range(1, n))
    out += [price("Both teams to score", "Yes", btts), price("Both teams to score", "No", 1 - btts)]
    for name, lam in ((hn, lh), (an, la)):
        out += [price("Team goals", f"{name} over 0.5", 1 - math.exp(-lam)),
                price("Team goals", f"{name} over 1.5", pois_over(1.5, lam))]
    # half with most goals
    L = lh + la
    l1, l2 = P["first_half_share"] * L, (1 - P["first_half_share"]) * L
    p1 = p2 = pt = 0.0
    for x in range(12):
        for y in range(12):
            p = pois(x, l1) * pois(y, l2)
            if x > y:
                p1 += p
            elif y > x:
                p2 += p
            else:
                pt += p
    out += [price("Half with most goals", "First half", p1), price("Half with most goals", "Second half", p2),
            price("Half with most goals", "Tie", pt)]
    exp = {"home_goals": round(lh, 2), "away_goals": round(la, 2)}
    lg = m.get("league") or {}
    if lg.get("corners_home_pg") and lg.get("corners_away_pg") and all(k in h for k in ("corners_for", "corners_against")) \
            and all(k in a for k in ("corners_for", "corners_against")):
        out += corners_v2(m, h, a, hn, an, lg, exp)
    if lg.get("yellow_pg") and "cards" in h and "cards" in a:
        out += cards_v2(m, h, a, lg, exp)
    # corners (1.1, without league block)
    if not lg.get("corners_home_pg") and all(k in h for k in ("corners_for", "corners_against")) and all(k in a for k in ("corners_for", "corners_against")):
        mh = (h["corners_for"] + a["corners_against"]) / 2 * P["corner_scale"]
        ma = (a["corners_for"] + h["corners_against"]) / 2 * P["corner_scale"]
        mu = mh + ma
        exp["corners"] = round(mu, 1)
        for line in (7.5, 8.5, 9.5, 10.5, 11.5, 12.5):
            ov = nb_over(line, mu, P["corners_r"])
            out += [price("Corners", f"Over {line}", ov), price("Corners", f"Under {line}", 1 - ov)]
        for name, m_ in ((hn, mh), (an, ma)):
            for line in (2.5, 3.5, 4.5, 5.5, 6.5):
                out.append(price("Team corners", f"{name} over {line}", nb_over(line, m_, P["team_corners_r"])))
    # cards (1.1, without league block)
    if not lg.get("yellow_pg") and "cards" in h and "cards" in a:
        inten = m.get("intensity", 1.0)
        ref = m.get("referee") or {}
        teams = h["cards"] + a["cards"]
        w = P["ref_weight"]
        mu = inten * P["card_scale"] * (w * ref["yellow_pg"] + (1 - w) * teams if ref.get("yellow_pg") else teams)
        exp["cards"] = round(mu, 2)
        for line in (2.5, 3.5, 4.5, 5.5, 6.5):
            ov = nb_over(line, mu, P["cards_r"])
            out += [price("Cards", f"Over {line}", ov), price("Cards", f"Under {line}", 1 - ov)]
        rr = ref.get("red_rate", P["red_default"]) * inten
        out.append(price("Red card", "Yes", 1 - math.exp(-rr)))
    # players
    for pl in m.get("players", []):
        f = pl.get("exp_minutes", 80) / 90 * pl.get("opp_factor", 1.0) * P["player_scale"]
        if "sot_p90" in pl:
            lam = pl["sot_p90"] * f
            out += [price("Player shots on target", f"{pl['name']} 1+", nb_over(0.5, lam, P["player_r"])),
                    price("Player shots on target", f"{pl['name']} 2+", nb_over(1.5, lam, P["player_r"]))]
        if "shots_p90" in pl:
            lam = pl["shots_p90"] * f
            for k in (1, 2, 3):
                out.append(price("Player shots", f"{pl['name']} {k}+", nb_over(k - 0.5, lam, P["player_r"])))
    return out, exp


def _shrink(v, n, target, k):
    n = n if isinstance(n, (int, float)) and n > 0 else 5          # unknown sample: treat as 5 games
    return (n * v + k * target) / (n + k)


def corners_v2(m, h, a, hn, an, lg, exp):
    """Team corners from shrunk team rates blended with a market-dominance estimate; total corners half league
    average. Probabilities checked on 9,445 test matches (research/corners_cards_v2.json)."""
    k, w, b = P["cc_k"], P["cc_w"], P["cc_b"]
    LH, LA = lg["corners_home_pg"], lg["corners_away_pg"]
    th = (_shrink(h["corners_for"], h.get("corners_n"), LH, k) + _shrink(a["corners_against"], a.get("corners_n"), LH, k)) / 2
    ta = (_shrink(a["corners_for"], a.get("corners_n"), LA, k) + _shrink(h["corners_against"], h.get("corners_n"), LA, k)) / 2
    mk = m.get("market") or {}
    d = (mk["ph"] - mk["pa"]) if mk.get("ph") is not None and mk.get("pa") is not None else 0.0
    mh = w * th + (1 - w) * LH * math.exp(b * d)
    ma = w * ta + (1 - w) * LA * math.exp(-b * d)
    mu = P["cc_total_league"] * (LH + LA) + (1 - P["cc_total_league"]) * (mh + ma)
    exp.update({"corners": round(mu, 1), "corners_home": round(mh, 2), "corners_away": round(ma, 2),
                "setPieceModel": "v2" + ("" if mk.get("ph") is not None else "-nomarket")})
    out = []
    for line in (7.5, 8.5, 9.5, 10.5, 11.5, 12.5):
        ov = nb_over(line, mu, P["cc_total_r"])
        out += [price("Corners", f"Over {line}", ov), price("Corners", f"Under {line}", 1 - ov)]
    f = P["cc_team_shrink"]
    for name, m_ in ((hn, mh), (an, ma)):
        for line in (2.5, 3.5, 4.5, 5.5, 6.5):
            out.append(price("Team corners", f"{name} over {line}", 0.5 + f * (nb_over(line, m_, P["cc_team_r"]) - 0.5)))
    return out


def cards_v2(m, h, a, lg, exp):
    """Yellow cards: shrunk team and referee averages, a little more in close games (market prices)."""
    L = lg["yellow_pg"]
    n = min(h.get("cards_n") or 5, a.get("cards_n") or 5)
    team = _shrink(h["cards"] + a["cards"], n, L, P["cards_k"])
    ref = m.get("referee") or {}
    refv = _shrink(ref["yellow_pg"], ref.get("games"), L, P["cards_k"]) if ref.get("yellow_pg") else L
    base = P["cards_wt"] * team + P["cards_wr"] * refv + (1 - P["cards_wt"] - P["cards_wr"]) * L
    mk = m.get("market") or {}
    close = 1 - abs(mk["ph"] - mk["pa"]) if mk.get("ph") is not None and mk.get("pa") is not None else 0.7
    mu = base * math.exp(P["cards_d"] * (close - 0.7)) * m.get("intensity", 1.0)
    exp["cards"] = round(mu, 2)
    exp["cardsModel"] = "v2"
    out = []
    for line in (2.5, 3.5, 4.5, 5.5, 6.5):
        ov = nb_over(line, mu, P["cards2_r"])
        out += [price("Cards", f"Over {line}", ov), price("Cards", f"Under {line}", 1 - ov)]
    rr = ref.get("red_rate", P["red_default"]) * m.get("intensity", 1.0)
    out.append(price("Red card", "Yes", 1 - math.exp(-rr)))
    return out


def hockey(m):
    h, a = m["home"], m["away"]
    ha = m.get("home_adv", P["hockey_home_adv"])
    lh = math.sqrt(h["gf"] * a["ga"]) * ha
    la = math.sqrt(a["gf"] * h["ga"]) / ha
    n = 12
    ph = pd = 0.0
    tot, diff = {}, {}
    for i in range(n):
        for j in range(n):
            p = pois(i, lh) * pois(j, la)
            if i > j:
                ph += p
            elif i == j:
                pd += p
            tot[i + j] = tot.get(i + j, 0) + p
            diff[i - j] = diff.get(i - j, 0) + p
    pa = 1 - ph - pd
    pd *= P["hockey_draw_inflation"]  # regulation draws are a little more common than independent Poisson suggests
    s = ph + pd + pa
    ph, pd, pa = ph / s, pd / s, pa / s
    share = m.get("ot_home_share", P["hockey_ot_home_share"])
    hn, an = h.get("name", "Home"), a.get("name", "Away")
    out = [price("60-minute result", hn, ph), price("60-minute result", "Draw", pd), price("60-minute result", an, pa),
           price("Moneyline (incl. OT)", hn, ph + pd * share), price("Moneyline (incl. OT)", an, pa + pd * (1 - share)),
           price("Puck line", f"{hn} -1.5", sum(p for d, p in diff.items() if d >= 2)),
           price("Puck line", f"{an} +1.5", 1 - sum(p for d, p in diff.items() if d >= 2))]
    for line in (4.5, 5.5, 6.5):
        ov = sum(p for k, p in tot.items() if k > line)
        out += [price("Total goals (60 min)", f"Over {line}", ov), price("Total goals (60 min)", f"Under {line}", 1 - ov)]
    for pl in m.get("players", []):
        if "sog_pg" in pl:
            for k in (2, 3, 4):
                out.append(price("Player shots on goal", f"{pl['name']} {k}+", pois_over(k - 0.5, pl["sog_pg"])))
        if "points_pg" in pl:
            out.append(price("Player points", f"{pl['name']} 1+", 1 - math.exp(-pl["points_pg"])))
    return out, {"home_goals": round(lh, 2), "away_goals": round(la, 2)}


def basketball(m):
    pm, pt = m["pred_margin"], m["pred_total"]
    sm, st = m.get("sd_margin", P["nba_sd_margin"]), m.get("sd_total", P["nba_sd_total"])
    hn, an = m.get("home_name", "Home"), m.get("away_name", "Away")
    out = [price("Moneyline", hn, 1 - phi((0 - pm) / sm)), price("Moneyline", an, phi((0 - pm) / sm))]
    for s in m.get("spreads", []):
        pc = 1 - phi((-s - pm) / sm)
        out += [price("Spread", f"{hn} {s:+}", pc), price("Spread", f"{an} {-s:+}", 1 - pc)]
    for L in m.get("totals", []):
        ov = 1 - phi((L - pt) / st)
        out += [price("Total points", f"Over {L}", ov), price("Total points", f"Under {L}", 1 - ov)]
    for pl in m.get("players", []):
        ov = 1 - phi((pl["line"] - pl["mean"]) / pl.get("sd", 6.0))
        out += [price(f"Player {pl.get('stat', 'points')}", f"{pl['name']} over {pl['line']}", ov),
                price(f"Player {pl.get('stat', 'points')}", f"{pl['name']} under {pl['line']}", 1 - ov)]
    return out, {"margin": pm, "total": pt}


def run(m):
    sport = m.get("sport", "football")
    fn = {"football": football, "hockey": hockey, "basketball": basketball}[sport]
    markets, exp = fn(m)
    markets = [x for x in markets if 3 <= x["prob"] <= 97]
    return {"event": m.get("event"), "sport": sport, "expected": exp, "markets": markets, "modelVersion": "1.1"}


if __name__ == "__main__":
    data = json.load(open(sys.argv[1]))
    res = [run(x) for x in data] if isinstance(data, list) else run(data)
    print(json.dumps(res, ensure_ascii=False, indent=1))
