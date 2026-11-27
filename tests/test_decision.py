import json, math, os, random, sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import decision as D  # noqa: E402

D.DEFAULT_CONFIG["marketAnchor"] = False   # the legacy tests below describe 3.7 and earlier; 3.8's anchor: test_38_*
import model  # noqa: E402

CFG = D.load_config()
NOW = "2026-11-25T12:00:00Z"


def football_match(**kw):
    m = {"id": "2026-11-25-a-b", "event": "Alpha – Beta", "sport": "Football", "start": "2026-11-25T20:00:00Z",
         "inputs": {"event": "Alpha – Beta", "home": {"name": "Alpha", "xgf": 1.8, "xga": 1.0, "corners_for": 6, "corners_against": 4, "cards": 1.8},
                    "away": {"name": "Beta", "xgf": 1.1, "xga": 1.6, "corners_for": 4.5, "corners_against": 5.5, "cards": 2.1}},
         "flags": {"defaulted": 0, "stale": 0, "lineup": "confirmed", "refereeKnown": True, "priceSources": 2, "primarySource": True},
         "marketOdds": {}}
    m.update(kw)
    return m


# ---------------------------------------------------------------- EV and minimum odds
def test_ev():
    assert D.ev(0.5, 2.0) == pytest.approx(0.0)
    assert D.ev(0.55, 2.0) == pytest.approx(0.10)
    assert D.ev(0.4, 2.0) == pytest.approx(-0.2)


def test_ceil2_is_robust_to_float_noise():
    assert D.ceil2(2.06) == 2.06            # 1.03/0.5 must not become 2.07
    assert D.ceil2(1.03 / 0.5) == 2.06
    assert D.ceil2(2.0601) == 2.07


def test_min_odds_v1_is_the_real_old_threshold():
    # tier A, p = 0.5: edge needs 2.06, band low 0.47 needs 0.98/0.47 = 2.09 -> 2.09
    assert D.min_odds_v1(0.5, "A", CFG) == 2.09
    # high p: the edge part binds and equals the old HUD formula
    assert D.min_odds_v1(0.75, "A", CFG) == math.ceil(1.03 / 0.75 * 100) / 100
    for p in (0.2, 0.5, 0.731):
        lo = p - CFG["v1"]["band"]["B"]
        mo = D.min_odds_v1(p, "B", CFG)
        assert D.ev(p, mo) >= 0.03 - 1e-9 and D.ev(lo, mo) >= -0.02 - 1e-9


def test_min_odds_v2_is_never_below_v1_and_grows_with_uncertainty():
    p = 0.5
    mos = [D.min_odds_v2(p, D.prob_summary(p, t, CFG)["pQ"], CFG) for t in ("A", "B", "C")]
    assert all(m >= D.ceil2(1.03 / p) for m in mos)
    assert mos[0] < mos[1] < mos[2]


def test_min_odds_v2_break_even_at_cautious_quantile():
    p = 0.45
    s = D.prob_summary(p, "B", CFG)
    mo = D.min_odds_v2(p, s["pQ"], CFG)
    assert D.ev(s["pQ"], mo) >= -1e-9          # EV >= 0 at the 20th percentile
    assert D.ev(p, mo) >= CFG["edge"] - 1e-9    # and >= 3% at the point estimate


def test_verdict_matches_hud_rule():
    assert D.verdict(0.5, 2.10, None) == "check"
    assert D.verdict(0.5, 2.10, 2.10) == "ok"
    assert D.verdict(0.5, 2.10, 2.05) == "thin"
    assert D.verdict(0.5, 2.10, 1.90) == "no"


# ---------------------------------------------------------------- intervals
def test_interval_ordering_and_bounds():
    for p in (0.05, 0.3, 0.5, 0.9):
        s = D.prob_summary(p, "B", CFG)
        assert 0 < s["pLow"] < s["pQ"] < p < s["pHigh"] < 1


def test_interval_narrows_with_more_data():
    w = [D.prob_summary(0.4, t, CFG) for t in ("A", "B", "C")]
    widths = [s["pHigh"] - s["pLow"] for s in w]
    assert widths[0] < widths[1] < widths[2]


def test_interval_matches_monte_carlo():
    random.seed(1)
    p, n = 0.35, 20
    se = 1 / math.sqrt(n * p * (1 - p))
    draws = sorted(D.sigmoid(D.logit(p) + random.gauss(0, se)) for _ in range(40000))
    assert D.quantile(p, n, 0.2) == pytest.approx(draws[8000], abs=0.004)


def test_interval_scales_with_p_unlike_fixed_band():
    a = D.prob_summary(0.10, "B", CFG)
    b = D.prob_summary(0.50, "B", CFG)
    assert (a["pHigh"] - a["pLow"]) < (b["pHigh"] - b["pLow"])


# ---------------------------------------------------------------- data and quality scores
def test_data_quality_is_deterministic():
    assert D.data_quality({"lineup": "confirmed", "priceSources": 2}) == 100
    assert D.data_quality({"lineup": "unknown", "priceSources": 1, "defaulted": 2, "stale": 1}) == 100 - 12 - 10 - 16 - 6
    assert D.data_quality({"defaulted": 20}) == 0
    assert D.data_tier(80) == "A" and D.data_tier(79) == "B" and D.data_tier(59) == "C"


def test_quality_score_components():
    q, parts = D.quality_score(0.10, "active", "A", 2, 0.01, "confirmed", 100)
    assert parts == {"ev": 30.0, "model": 20, "evidence": 15, "market": 15, "info": 10, "data": 10}
    assert q == 100
    q2, p2 = D.quality_score(None, "trial", "C", 0, None, "unknown", 40)
    assert p2["ev"] == 0 and p2["model"] == 10 and p2["market"] == 0
    assert q2 == 0 + 10 + 3 + 0 + 2 + 4


def test_quality_score_ev_capped_and_floored():
    assert D.quality_score(0.5, "active", "A", 2, 0, "confirmed", 100)[1]["ev"] == 30
    assert D.quality_score(-0.2, "active", "A", 2, 0, "confirmed", 100)[1]["ev"] == 0


# ---------------------------------------------------------------- gates and market status
def gate(p=0.5, rules="v2", status="active", tier="A", odds=2.2, gap=0.0, quality=80, mins=300, on=True):
    return D.evaluate_line(p, rules=rules, cfg=CFG, status=status, tier=tier, odds=odds, gap=gap,
                           quality=quality, minutes_to_start=mins, sport_on=on)


def test_gate_passes_good_line():
    ok, why, mo = gate()
    assert ok and not why and mo <= 2.2


def test_gate_market_status():
    assert not gate(status="shadow")[0]
    assert "market shadow" in gate(status="shadow")[1]
    assert gate(status="trial", odds=2.2)[0]                  # 10% edge, quality 80
    assert not gate(status="trial", odds=2.08)[0]             # under 5% edge
    assert not gate(status="trial", quality=58)[0]            # under 60 quality


def test_gate_blocks_gap_sport_and_late_start():
    assert "model-market gap +9.0 pp" in gate(gap=0.09)[1]
    assert gate(gap=0.08)[0]
    assert "sport off" in gate(on=False)[1]
    assert "starts too soon" in gate(mins=20)[1]


def test_gate_quality_floor():
    assert not gate(quality=54)[0]
    assert gate(quality=55)[0]


def test_v1_and_v2_differ_as_designed():
    # p = 0.5, tier C: v1 needs 0.98/0.42 = 2.34 (band rule); v2 needs 1/pQ = 2.24
    ok1, why1, mo1 = gate(rules="v1", tier="C", odds=2.30)
    ok2, why2, mo2 = gate(rules="v2", tier="C", odds=2.30)
    assert mo1 == 2.34 and not ok1 and any("band low" in w for w in why1)
    assert mo2 == 2.24 and ok2
    # p = 0.2, tier C: the flat 8 pp band demands 8.17; v2 scales with p
    assert D.min_odds_v1(0.2, "C", CFG) == 8.17
    assert D.min_odds_v2(0.2, D.prob_summary(0.2, "C", CFG)["pQ"], CFG) == 6.21
    # with no price, both defer to the BetMGM check
    assert gate(rules="v2", odds=None)[0] and gate(rules="v1", odds=None)[0]


def test_unknown_market_defaults_to_shadow():
    out = D.process_match(football_match(), {}, NOW, CFG)
    assert all(r["status"] == "shadow" and not r["v2"]["post"] for r in out["markets"])


# ---------------------------------------------------------------- market reference
def test_novig_sums_to_one():
    p = D.novig([1.9, 1.9])
    assert sum(p) == pytest.approx(1) and p[0] == pytest.approx(0.5)


def test_market_probs_and_derived_double_chance():
    priced = model.run({"sport": "football", **football_match()["inputs"]})["markets"]
    odds = {D.line_key("Match result", "Alpha"): 1.9, D.line_key("Match result", "Draw"): 3.6,
            D.line_key("Match result", "Beta"): 4.2,
            D.line_key("Total goals", "Over 2.5"): 1.8, D.line_key("Total goals", "Under 2.5"): 2.0}
    pm = D.market_probs(priced, odds)
    h, d = pm[D.line_key("Match result", "Alpha")], pm[D.line_key("Match result", "Draw")]
    assert pm[D.line_key("Double chance", "Alpha or draw")] == pytest.approx(h + d)
    assert pm[D.line_key("Total goals", "Over 2.5")] + pm[D.line_key("Total goals", "Under 2.5")] == pytest.approx(1)
    assert D.line_key("Total goals", "Over 1.5") not in pm      # incomplete set: no reference


def test_line_key_matches_hud():
    assert D.line_key("Total goals", "Over 2.5") == "Total_goals_Over_2_5"
    assert D.line_key("Team goals", "Atlético over 0.5") == "Team_goals_Atl_tico_over_0_5"


# ---------------------------------------------------------------- exact goal combinations
def test_grid_lambdas_match_model():
    inp = football_match()["inputs"]
    lh, la = D.football_lambdas(inp)
    out = model.run({"sport": "football", **inp})
    assert round(lh, 2) == out["expected"]["home_goals"] and round(la, 2) == out["expected"]["away_goals"]


def test_single_leg_grid_equals_model_price():
    inp = football_match()["inputs"]
    lh, la = D.football_lambdas(inp)
    out = {(x["group"], x["selection"]): x["prob"] for x in model.run({"sport": "football", **inp})["markets"]}
    for leg in [("Match result", "Alpha"), ("Both teams to score", "Yes"), ("Total goals", "Over 2.5"),
                ("Double chance", "Beta or draw"), ("Team goals", "Beta over 0.5")]:
        assert round(D.joint_grid_prob(lh, la, [leg], "Alpha", "Beta") * 100, 1) == pytest.approx(out[leg], abs=0.11)


def test_joint_differs_from_independent_product():
    lh, la = 1.6, 1.1
    a, b = ("Match result", "Alpha"), ("Total goals", "Over 2.5")
    pa = D.joint_grid_prob(lh, la, [a], "Alpha", "Beta")
    pb = D.joint_grid_prob(lh, la, [b], "Alpha", "Beta")
    pj = D.joint_grid_prob(lh, la, [a, b], "Alpha", "Beta")
    assert pj > pa * pb                       # home win and goals are positively linked
    assert pj <= min(pa, pb)


def test_contradictory_legs_have_zero_probability():
    assert D.joint_grid_prob(1.5, 1.2, [("Both teams to score", "Yes"), ("Total goals", "Under 1.5")], "A", "B") == 0


def test_non_goal_legs_are_not_priced():
    assert D.joint_grid_prob(1.5, 1.2, [("Match result", "A"), ("Corners", "Over 9.5")], "A", "B") is None


def test_slip_same_match_props_go_to_shadow_and_goal_only_is_exact():
    g = {"lh": 1.6, "la": 1.1, "rho": -0.06, "home": "Alpha", "away": "Beta"}
    props = D.price_slip([{"matchId": "m", "group": "Match result", "selection": "Alpha", "prob": 50, "nEff": 40},
                          {"matchId": "m", "group": "Cards", "selection": "Over 4.5", "prob": 45, "nEff": 40}], {"m": g}, CFG)
    assert props["shadow"]
    goals = D.price_slip([{"matchId": "m", "group": "Match result", "selection": "Alpha", "prob": 50, "nEff": 40},
                          {"matchId": "m", "group": "Total goals", "selection": "Over 2.5", "prob": 50, "nEff": 40}], {"m": g}, CFG)
    exact = D.joint_grid_prob(1.6, 1.1, [("Match result", "Alpha"), ("Total goals", "Over 2.5")], "Alpha", "Beta", -0.06)
    assert not goals["shadow"] and goals["prob"] == pytest.approx(exact * 100, abs=0.01)


def test_combo_quantile_close_to_monte_carlo():
    random.seed(2)
    legs = [(0.7, 40), (0.6, 20), (0.75, 40)]
    P, Pq = D.combo_quantile(legs, 0.2)
    sims = []
    for _ in range(30000):
        v = 1.0
        for p, n in legs:
            v *= D.sigmoid(D.logit(p) + random.gauss(0, 1 / math.sqrt(n * p * (1 - p))))
        sims.append(v)
    sims.sort()
    assert P == pytest.approx(0.315)
    assert Pq == pytest.approx(sims[6000], abs=0.012)


# ---------------------------------------------------------------- pipeline, products, verify
STATUSES = {g: {"status": "active"} for g in ["Match result", "Double chance", "Draw no bet", "Total goals",
                                               "Both teams to score", "Team goals"]}


def priced_match(i, odds_factor=1.15, **kw):
    m = football_match(id=f"m{i}", event=f"Home{i} – Away{i}", **kw)
    m["inputs"]["home"]["name"], m["inputs"]["away"]["name"] = f"Home{i}", f"Away{i}"
    out = model.run({"sport": "football", **m["inputs"]})["markets"]
    m["marketOdds"] = {D.line_key(x["group"], x["selection"]): round(100 / x["prob"] * odds_factor, 2)
                       for x in out if x["group"] in ("Match result", "Total goals", "Both teams to score")}
    return m


def test_process_match_shadow_mode_stores_v1_min_odds():
    out = D.process_match(priced_match(1), STATUSES, NOW, CFG)
    r = out["markets"][0]
    assert r["minOdds"] == r["minOddsV1"]
    # stored minOdds decides the posting gate exactly as the HUD's verdict would
    for r in out["markets"]:
        if r["odds"] is not None and r["status"] == "active" and r["qualityScore"] >= 55 and r["gap"] is not None and abs(r["gap"]) <= 8:
            assert r["v1"]["post"] == (D.verdict(r["prob"] / 100, r["minOdds"], r["odds"]) == "ok")
    act = D.process_match(priced_match(1), STATUSES, NOW, D.load_config({"mode": "active"}))
    assert act["markets"][0]["minOdds"] == act["markets"][0]["minOddsV2"]


def test_basketball_is_off_but_still_priced():
    m = {"id": "nba", "event": "X – Y", "sport": "Basketball", "start": "2026-11-26T01:00:00Z",
         "inputs": {"pred_margin": 3, "pred_total": 225, "spreads": [-3.5], "totals": [224.5]},
         "flags": {"lineup": "confirmed", "priceSources": 2}, "marketOdds": {}}
    out = D.process_match(m, {"Moneyline": {"status": "active"}}, NOW, CFG)
    assert out["markets"] and all("sport off" in r["v2"]["why"] for r in out["markets"])


def test_products_respect_exposure_and_run_limits():
    ms = [priced_match(i) for i in range(12)]
    res = D.run({"now": NOW, "matches": ms, "markets": STATUSES, "openBets": [{"matchId": "m0"}, {"matchId": "m0"}],
                 "config": {"mode": "active", "maxPerEvent": 2}})       # a cap still works when the owner sets one
    prods = res["products"]
    assert len([p for p in prods if p["tier"] in ("safe", "value", "long")]) == 11   # no quotas: one per free match
    per = {}
    for b in prods:
        for mid in [b.get("matchId")] + [l["matchId"] for l in b.get("legs", []) if isinstance(l, dict) and "matchId" in l]:
            if mid:
                per[mid] = per.get(mid, 0) + 1
    assert "m0" not in per                          # already at two open bets
    assert all(v <= 2 for v in per.values())


def test_no_cap_per_match_when_switched_off_and_shared_matches_flagged():
    assert CFG["maxPerEvent"] == 2                 # 3.6 default: two products per match
    ms = [priced_match(i) for i in range(4)]
    res = D.run({"now": NOW, "matches": ms, "markets": STATUSES, "openBets": [{"matchId": "m0"}, {"matchId": "m0"}],
                 "config": {"mode": "active", "maxPerEvent": None}})
    prods = res["products"]
    assert any(p.get("matchId") == "m0" for p in prods)          # two open bets no longer block a match
    mids = [D._mids(p) for p in prods]
    for i, p in enumerate(prods):
        want = [j for j in range(len(prods)) if j != i and mids[i] & mids[j]]
        assert p.get("sharedWith", []) == want
    # near-duplicates merged: at most one single per market group per match
    seen = set()
    for p in prods:
        if "line" in p and p.get("kind") is None:
            k = (p["matchId"], D.FAMILY_GROUP.get(p["line"]["group"], p["line"]["group"]))
            assert k not in seen
            seen.add(k)


def test_no_value_means_no_products():
    ms = [priced_match(i, odds_factor=0.95) for i in range(5)]   # every price below fair
    res = D.run({"now": NOW, "matches": ms, "markets": STATUSES, "openBets": [], "config": {"mode": "active"}})
    assert not [p for p in res["products"] if p["tier"] != "builder"]


def test_verify_detects_a_changed_probability():
    m = priced_match(1)
    payload = {"now": NOW, "matches": [m], "markets": STATUSES}
    res = D.run(payload)
    k = res["matches"][0]["markets"][0]
    stored = {"m1": {k["key"]: {"prob": k["prob"], "minOdds": k["minOdds"]}}}
    assert D.verify(payload, stored) == []
    stored["m1"][k["key"]]["prob"] = k["prob"] + 3
    assert D.verify(payload, stored)


def test_input_hash_is_stable():
    a = D.input_hash({"b": 1, "a": [1, 2]})
    assert a == D.input_hash({"a": [1, 2], "b": 1}) and len(a) == 16


# ---------------------------------------------------------------- CLV and evaluation
def test_clv_uses_no_vig_close_and_labels_timing():
    v, lab = D.clv(2.10, [1.95, 1.95], 25, CFG)
    assert v == pytest.approx(0.05) and lab == "close"
    assert D.clv(2.10, [1.95, 1.95], 180, CFG)[1] == "pre-close"
    assert D.clv(2.10, [2.0], 10, CFG)[1] == "close-with-margin"


def test_evaluate_counts_matches_not_lines():
    g = [{"matchId": f"m{i // 5}", "at": f"2026-11-{10 + i // 5:02d}", "group": "Total goals", "pModel": 0.6,
          "pMarket": 0.55, "y": i % 2} for i in range(40)]
    r = D.evaluate(g)
    assert r["matches"] == 8 and r["lines"] == 40 and r["familyMatches"]["Total goals"] == 8
    assert not r["enough"]
    assert r["chosenWeight"] in (0.0, 0.25, 0.5, 0.75, 1.0)


def test_evaluate_prefers_market_when_model_is_noise():
    random.seed(3)
    g = []
    for i in range(400):
        pm = random.uniform(0.2, 0.8)
        g.append({"matchId": f"m{i}", "at": f"{i:05d}", "group": "Match result", "pMarket": pm,
                  "pModel": min(max(pm + random.gauss(0, 0.15), 0.02), 0.98), "y": int(random.random() < pm)})
    r = D.evaluate(g)
    assert r["chosenWeight"] <= 0.25 and r["test"]["market"] < r["test"]["model"]


def test_interval_coverage_needs_data():
    assert not D.interval_coverage([], CFG)["ready"]


def test_one_sided_price_still_gets_the_gap_check():
    # 17:44 run: Atletico-Viking BTTS priced 68.7% with only "Yes" at 2.05 -> gap never checked
    m = football_match(marketOdds={D.line_key("Both teams to score", "Yes"): 1.30})
    out = D.process_match(m, {"Both teams to score": {"status": "active"}}, NOW, CFG)
    r = next(x for x in out["markets"] if x["key"] == "Both_teams_to_score_Yes")
    assert r["pMarketKind"] == "single" and r["pMarket"] == pytest.approx(100 / 1.30 / 1.05, abs=0.06)
    assert r["gap"] is not None
    m2 = football_match(marketOdds={D.line_key("Match result", "Alpha"): 1.11})
    out2 = D.process_match(m2, {"Match result": {"status": "active"}}, NOW, CFG)
    r2 = next(x for x in out2["markets"] if x["key"] == "Match_result_Alpha")
    assert any(w.startswith("model-market gap") for w in r2["v1"]["why"])


def test_blend_test_ignores_single_price_references():
    g = [{"matchId": "a", "at": "1", "group": "X", "pModel": 0.6, "pMarket": 0.5, "pMarketKind": "single", "y": 1},
         {"matchId": "b", "at": "2", "group": "X", "pModel": 0.6, "pMarket": 0.5, "pMarketKind": "novig", "y": 1}]
    assert D.evaluate(g)["lines"] == 1


def test_tiers_are_labels_not_caps():
    assert D._tier_for(1.25, 0.82, 0.03, CFG) == "safe"
    assert D._tier_for(1.60, 0.50, 0.03, CFG) == "value"
    assert D._tier_for(3.80, 0.30, 0.10, CFG) == "value"      # 3.50-4.00 was a hole before
    assert D._tier_for(6.00, 0.20, 0.04, CFG) is None         # long shots keep the 5% edge rule
    assert D._tier_for(6.00, 0.20, 0.08, CFG) == "long"


def test_one_builder_per_match_allowed():
    ms = [priced_match(i, odds_factor=1.0) for i in range(3)]
    for m in ms:   # make the model disagree mildly with the market so builders exist
        for k in list(m["marketOdds"]):
            m["marketOdds"][k] = round(m["marketOdds"][k] * 1.06, 2)
    res = D.run({"now": NOW, "matches": ms, "markets": STATUSES, "openBets": [], "config": {"mode": "active"}})
    b = [p for p in res["products"] if p["tier"] == "builder"]
    assert len({p["matchId"] for p in b}) == len(b)


def test_shadow_model12_is_stored_but_never_decides():
    m = priced_match(1)
    base = D.process_match(m, STATUSES, NOW, CFG)
    m2 = priced_match(1)
    m2["p2"] = {"date": "2026-11-25", "leagues": {"L1": {"mu_home": 1.5, "mu_away": 1.2}, "L2": {"mu_home": 1.3, "mu_away": 1.1}},
                "home": {"league": "L1", "history": [{"date": "2026-11-01", "venue": "H", "xgf": 2.5, "xga": 0.6}], "elo": 1850},
                "away": {"league": "L2", "history": [{"date": "2026-11-01", "venue": "A", "xgf": 0.9, "xga": 1.9}], "elo": 1600}}
    with_p2 = D.process_match(m2, STATUSES, NOW, CFG)
    strip = lambda rows: [{k: v for k, v in r.items() if k not in ("prob12", "prob12a", "prob12b", "prob12s", "prob12h")} for r in rows]
    assert strip(with_p2["markets"]) == base["markets"]          # decisions identical
    assert any("prob12" in r for r in with_p2["markets"]) and with_p2["model12"]["version"].startswith("1.2")
    assert any(r["prob12"] != r["prob"] for r in with_p2["markets"] if "prob12" in r)


def test_compare_models_pairs_by_match():
    g = []
    for i in range(60):
        cross = i % 3 == 0
        y = i % 2
        g += [{"matchId": f"m{i}", "at": str(i), "group": "Match result", "cross": cross, "y": y,
               "p11": 0.5, "p12": 0.6 if y else 0.4, "pMarket": 0.55 if y else 0.45, "pMarketKind": "novig"},
              {"matchId": f"m{i}", "at": str(i), "group": "Total goals", "cross": cross, "y": y,
               "p11": 0.5, "p12": 0.6 if y else 0.4, "pMarket": 0.5, "pMarketKind": "single"}]
    r = D.compare_models(g)
    assert r["all"]["matches"] == 60 and r["cross"]["matches"] == 20 and r["domestic"]["matches"] == 40
    assert r["all"]["m12_minus_m11"]["mean"] < 0 and r["all"]["m12_minus_m11"]["hi95"] < 0
    assert r["all"]["m12_minus_market"]["n"] == 60                 # single-price lines excluded from market
    assert not r["thresholdMet"]


def test_compare_all_uses_identical_lines():
    g = []
    for i in range(40):
        y = i % 2
        row = {"matchId": f"m{i}", "at": str(i), "group": "Match result", "cross": True, "y": y, "p11": 0.5,
               "p12": 0.55 if y else 0.45, "p12a": 0.6 if y else 0.4, "p12b": 0.65 if y else 0.35, "p12s": 0.55 if y else 0.45, "p12h": 0.55 if y else 0.45,
               "pMarket": 0.5, "pMarketKind": "novig"}
        if i == 0:
            row.pop("p12b")          # one line lacks a candidate -> excluded for every model
        g.append(row)
    r = D.compare_all(g)
    assert all(r[k]["all"]["matches"] == 39 for k in r)
    assert r["p12b"]["all"]["m12_minus_m11"]["mean"] < r["p12a"]["all"]["m12_minus_m11"]["mean"] < r["p12"]["all"]["m12_minus_m11"]["mean"] < 0


# ---------------------------------------------------------------- shadow models can never decide
def _p2_match(i):
    m = priced_match(i)
    m["p2"] = {"date": "2026-11-25", "leagues": {"L1": {"mu_home": 1.5, "mu_away": 1.2, "eloMean": 1700, "eloMeanSeasonStart": 1690},
                                                  "L2": {"mu_home": 1.3, "mu_away": 1.1, "eloMean": 1550, "eloMeanSeasonStart": 1560}},
               "home": {"league": "L1", "history": [{"date": "2026-11-01", "venue": "H", "xgf": 2.5, "xga": 0.6}], "elo": 1850,
                        "prev": {"attack": 1.4, "defence": 0.8, "league": "L0", "leagueEloMean": 1800}, "promoted": False},
               "away": {"league": "L2" if i % 2 else "L1", "history": [{"date": "2026-11-01", "venue": "A", "xgf": 0.9, "xga": 1.9}],
                        "elo": 1600, "prev": {"attack": 0.9, "defence": 1.2, "league": "L3", "leagueEloMean": 1450}, "promoted": True}}
    return m


def _strip(x):
    """Remove every shadow field (prob12, prob12a, ...) at any depth."""
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if not k.startswith("prob12")}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def _decisions(res):
    """Everything production uses: rows, products, shadow-rule products and summary, without shadow-model fields."""
    return _strip([[m["markets"] for m in res["matches"]], res["products"], res["shadowProducts"], res["summary"]])


def test_shadow_models_cannot_change_any_production_decision(monkeypatch):
    ms = [_p2_match(i) for i in range(6)]
    payload = {"now": NOW, "matches": ms, "markets": STATUSES, "openBets": []}
    with_shadow = D.run(json.loads(json.dumps(payload)))
    assert all("prob12h" in r for m in with_shadow["matches"] for r in m["markets"])
    no_shadow = D.run({**json.loads(json.dumps(payload)), "matches": [{k: v for k, v in m.items() if k != "p2"} for m in ms]})
    assert _decisions(with_shadow) == _decisions(no_shadow)
    # even absurd shadow output (every probability 99.9%) changes nothing that production uses
    real = D.model12.run_candidates
    def absurd(m):
        out = real(m)
        for v in out.values():
            for x in v["markets"]:
                x["prob"] = 99.9
        return out
    monkeypatch.setattr(D.model12, "run_candidates", absurd)
    assert _decisions(D.run(json.loads(json.dumps(payload)))) == _decisions(no_shadow)
    # and a crashing shadow model is contained
    monkeypatch.setattr(D.model12, "run_candidates", lambda m: 1 / 0)
    crashed = D.run(json.loads(json.dumps(payload)))
    assert _decisions(crashed) == _decisions(no_shadow) and "error" in crashed["matches"][0]["model12"]


def test_decision_code_reads_shadow_fields_only_in_the_shadow_block():
    import inspect
    src = inspect.getsource(D.process_match) + inspect.getsource(D.build_products) + inspect.getsource(D.evaluate_line)
    shadow_start, shadow_end = src.index("shadow12 = None"), src.index("out = {")
    outside = src[:shadow_start] + src[shadow_end:]
    assert "prob12" not in outside and "model12" not in outside.replace('out["model12"] = shadow12', "")


# ---------------------------------------------------------------- 2.6/2.7: data requirements per market
ALL_ACTIVE = {g: {"status": "active"} for g in list(D.DATA_REQUIREMENTS)}
PROV_OK = {k: {"status": "verified", "source": "fbref.com", "at": NOW} for k in ("F1", "F1b", "F1c", "F2", "F3", "F4", "F5", "F6")}


def _rows(m, statuses=ALL_ACTIVE):
    return D.process_match(m, statuses, NOW, CFG)


def _core(rows):
    """Everything that is a probability, a price threshold or a quality score."""
    return [(r["key"], r["prob"], r["probLow"], r["probHigh"], r["pQ"], r["minOdds"], r["minOddsV1"], r["minOddsV2"],
             r["qualityScore"]) for r in rows]


def _blocks(r):
    return [w for w in r["v1"]["why"] if w.startswith("data:")]


def test_coverage_never_changes_probabilities_minimums_or_quality():
    base = _rows(football_match())
    for prov in ({}, PROV_OK, {**PROV_OK, "F2": {"status": "unreachable", "note": "sofascore 403"}},
                 {**PROV_OK, "F3": {"status": "unverified", "basis": "league average"}}):
        assert _core(_rows(football_match(provenance=prov))["markets"]) == _core(base["markets"])


@pytest.mark.parametrize("status", ["not_published", "not_available", "unreachable", "unverified"])
def test_every_non_verified_status_blocks_only_markets_that_require_the_item(status):
    out = _rows(football_match(provenance={**PROV_OK, "F2": {"status": status, "note": "tried sofascore, footystats"}}))
    ref = _rows(football_match(provenance=PROV_OK))
    for r, r0 in zip(out["markets"], ref["markets"]):
        if r["group"] in ("Corners", "Team corners"):
            assert not r["v1"]["post"] and not r["v2"]["post"] and _blocks(r)[0].startswith(f"data: F2 {status}")
        else:
            assert r["v1"] == r0["v1"] and r["v2"] == r0["v2"]


def test_verified_without_a_source_counts_as_unverified_and_blocks():
    out = _rows(football_match(provenance={**PROV_OK, "F3": {"status": "verified", "source": ""}}))
    cards = [r for r in out["markets"] if r["group"] in ("Cards", "Red card")]
    assert cards and all(_blocks(r) == ["data: F3 unverified"] for r in cards)
    assert out["coverage"]["items"]["F3"] == "unverified"


def test_unrecorded_required_item_is_not_treated_as_verified():
    prov = {k: v for k, v in PROV_OK.items() if k != "F5"}
    inp = football_match()["inputs"]
    inp["players"] = [{"name": "Ann", "team": "home", "sot_p90": 1.2, "shots_p90": 3.0, "exp_minutes": 90}]
    out = _rows(football_match(inputs=inp, provenance=prov))
    pl = [r for r in out["markets"] if r["group"].startswith("Player")]
    assert pl and all(_blocks(r) == ["data: F5 unverified"] for r in pl)


def test_legacy_statuses_map_onto_the_five():
    cases = {("ok", None): "verified", ("estimated", None): "unverified", ("missing", "not_published"): "not_published",
             ("missing", "unavailable"): "not_available", ("missing", "fetch_failed"): "unreachable",
             ("something odd", None): "unverified"}
    for (st, cls), want in cases.items():
        assert D._norm_status({"status": st, "class": cls, "source": "x"}) == want


def test_estimated_player_blocks_only_that_players_lines():
    inp = football_match()["inputs"]
    inp["players"] = [{"name": "Ann", "team": "home", "sot_p90": 1.2, "shots_p90": 3.0, "exp_minutes": 90},
                      {"name": "Bo", "team": "away", "sot_p90": 1.0, "shots_p90": 2.5, "exp_minutes": 90,
                       "estimated": True, "estimateBasis": "last season"}]
    out = _rows(football_match(inputs=inp, provenance=PROV_OK))
    seen = set()
    for r in out["markets"]:
        if r["selection"].startswith("Bo "):
            seen.add("Bo")
            assert not r["v1"]["post"] and "unverified" in _blocks(r)[0]
        if r["selection"].startswith("Ann "):
            seen.add("Ann")
            assert not _blocks(r)
    assert seen == {"Ann", "Bo"}


def test_missing_player_stats_do_not_touch_1x2():
    prov = {**PROV_OK, "F5": {"status": "not_available", "note": "no player data for this league"}}
    a = {r["key"]: r for r in _rows(football_match(provenance=prov))["markets"]}
    b = {r["key"]: r for r in _rows(football_match(provenance=PROV_OK))["markets"]}
    for k in a:
        if a[k]["group"] in ("Match result", "Total goals", "Both teams to score"):
            assert a[k]["v1"] == b[k]["v1"] and a[k]["v2"] == b[k]["v2"]


def test_scans_without_provenance_are_not_blocked():
    out = _rows(football_match())
    assert not any(_blocks(r) for r in out["markets"])
    assert out["coverage"]["recorded"] is False


def test_coverage_is_per_market_and_uses_only_items_that_market_needs():
    prov = {**PROV_OK, "F4": {"status": "not_published", "note": "referee not appointed"}}
    cov = _rows(football_match(provenance=prov))["coverage"]["byMarket"]
    assert cov["Cards"]["missing"][0] == {"item": "F4", "status": "not_published", "required": False}
    assert all(x["item"] != "F4" for x in cov["Match result"]["missing"])
    assert cov["Match result"]["required"] == ["F1"]


def test_unpriced_market_with_missing_required_item_is_listed():
    m = football_match()
    for side in ("home", "away"):
        for k in ("corners_for", "corners_against"):
            m["inputs"][side].pop(k)
    m["provenance"] = {**PROV_OK, "F2": {"status": "not_available"}}
    cov = _rows(m)["coverage"]["byMarket"]
    assert cov["Corners"]["priced"] is False and cov["Corners"]["missing"][0]["status"] == "not_available"
    assert cov["Match result"]["priced"] is True


def test_item_success_rates_per_run():
    ms = [football_match(id="a", provenance=PROV_OK),
          football_match(id="b", provenance={**PROV_OK, "F2": {"status": "unreachable"}, "F5": {"status": "not_available"}})]
    cov = D.run({"now": NOW, "matches": ms, "markets": ALL_ACTIVE, "openBets": []})["coverage"]
    assert cov["itemSuccess"]["F2"] == {"verified": 1, "total": 2, "pct": 50, "byStatus": {"verified": 1, "unreachable": 1}}
    assert cov["itemSuccess"]["F1"]["pct"] == 100 and cov["matchesRecorded"] == 2


def test_stale_reference_price_blocks_its_line_and_fresh_prices_do_not():
    mo = {"Total_goals_Over_2_5": 1.9, "Total_goals_Under_2_5": 1.95, "Match_result_Alpha": 2.6, "Match_result_Draw": 3.6, "Match_result_Beta": 3.2}
    fresh = {k: "2026-11-25T11:30:00Z" for k in mo}
    m = football_match(marketOdds=mo, oddsAt={**fresh, "Total_goals_Over_2_5": "2026-11-25T07:00:00Z"})
    rows = {r["key"]: r for r in _rows(m)["markets"]}
    ref = {r["key"]: r for r in _rows(football_match(marketOdds=mo, oddsAt=fresh))["markets"]}
    o = rows["Total_goals_Over_2_5"]
    assert o["oddsAgeMin"] == 300 and o["oddsStale"] is True
    assert not o["v1"]["post"] and not o["v2"]["post"] and any("reference price stale" in w for w in o["v1"]["why"])
    u = rows["Total_goals_Under_2_5"]          # fresh itself, but de-margined together with the stale Over price
    assert u["oddsStale"] is False and not u["v1"]["post"]
    for k in ("Match_result_Alpha", "Match_result_Draw", "Match_result_Beta"):   # other markets untouched
        assert (rows[k]["v1"], rows[k]["v2"]) == (ref[k]["v1"], ref[k]["v2"])
    for k in rows:                              # probabilities and thresholds never change
        assert (rows[k]["prob"], rows[k]["minOdds"], rows[k]["qualityScore"]) == (ref[k]["prob"], ref[k]["minOdds"], ref[k]["qualityScore"])


def test_failed_price_fetch_never_makes_old_odds_playable():
    mo = {"Match_result_Alpha": 2.6, "Match_result_Draw": 3.6, "Match_result_Beta": 3.2}
    old = {k: "2026-11-24T18:00:00Z" for k in mo}   # yesterday's prices reused after a failed fetch
    res = D.run({"now": NOW, "matches": [football_match(id="old", marketOdds=mo, oddsAt=old)], "markets": ALL_ACTIVE, "openBets": []})
    assert not any(p.get("key", "").startswith("Match_result") for p in res["products"])
    assert all(not r["v1"]["post"] for r in res["matches"][0]["markets"] if r["key"] in mo)


@pytest.mark.parametrize("fetched, want", [("2026-11-25T05:20:00Z", "verified"),     # 6 h 40 min old
                                           ("2026-11-24T17:00:00Z", "unverified"),   # 19 h old
                                           (None, "unverified"), ("garbage", "unverified")])
def test_repo_items_older_than_18_hours_are_never_verified(fetched, want):
    e = {"status": "verified", "source": "jarvis-data/data/football/teams_E0.json", "repoFetchedAt": fetched}
    assert D._norm_status(e, NOW) == want
    prov = {**PROV_OK, "F2": e}
    rows = _rows(football_match(provenance=prov))
    corners = [r for r in rows["markets"] if r["group"] in ("Corners", "Team corners")]
    assert corners and all(bool(_blocks(r)) == (want != "verified") for r in corners)
    assert rows["coverage"]["items"]["F2"] == want
    if want != "verified":
        assert "older than 18 h" in _blocks(corners[0])[0]


def test_web_sources_are_not_subject_to_the_repo_age_rule():
    e = {"status": "verified", "source": "fotmob.com", "at": "2026-11-20T10:00:00Z"}
    assert D._norm_status(e, NOW) == "verified"


def test_full_run_products_skip_blocked_markets_only():
    ms = [football_match(id=f"x{i}", marketOdds={"Match_result_Alpha": 2.6, "Match_result_Draw": 3.6, "Match_result_Beta": 3.2},
                         provenance={**PROV_OK, "F2": {"status": "unreachable"}}) for i in range(2)]
    res = D.run({"now": NOW, "matches": ms, "markets": ALL_ACTIVE, "openBets": []})
    ref = D.run({"now": NOW, "matches": [dict(m, provenance=PROV_OK) for m in ms], "markets": ALL_ACTIVE, "openBets": []})
    keys = lambda r: sorted(str(p.get("key") or [l.get("key") for l in p.get("legs", [])]) for p in r["products"])
    assert all("corner" not in k.lower() for k in keys(res))
    assert [k for k in keys(ref) if "corner" not in k.lower()] == keys(res)


def test_provenance_and_fresh_odds_times_never_change_products_apart_from_their_labels():
    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k not in ("oddsAgeMin", "oddsStale")}
        return [strip(v) for v in o] if isinstance(o, list) else o
    mo = {"Match_result_Alpha": 2.6, "Match_result_Draw": 3.6, "Match_result_Beta": 3.2,
          "Total_goals_Over_2_5": 1.9, "Total_goals_Under_2_5": 2.05}
    ms = [football_match(id=f"q{i}", marketOdds=mo) for i in range(3)]
    plain = D.run({"now": NOW, "matches": ms, "markets": ALL_ACTIVE, "openBets": []})
    rich = D.run({"now": NOW, "markets": ALL_ACTIVE, "openBets": [],
                  "matches": [dict(m, provenance=PROV_OK, oddsAt={k: "2026-11-25T10:30:00Z" for k in mo}) for m in ms]})
    assert strip(plain["products"]) == strip(rich["products"])
    assert strip(_decisions(plain)) == strip(_decisions(rich))


# ---------------------------------------------------------------- 2.9: product profile "hitrate"
def _payload(n=8, factor=1.12):
    ms = []
    for i in range(n):
        m = priced_match(i, odds_factor=factor)
        m["inputs"]["home"]["xgf"] = 1.2 + 0.2 * i          # a spread of favourites and underdogs
        m["inputs"]["away"]["xga"] = 1.0 + 0.1 * i
        ms.append(m)
    return {"now": NOW, "matches": ms, "markets": STATUSES, "openBets": []}


def _cfg(**kw):
    c = json.loads(json.dumps(CFG))
    c.update(kw)
    return c


def test_value_profile_is_unchanged_from_2_8():
    import importlib.util
    spec = importlib.util.spec_from_file_location("d28", "/tmp/decision_28.py")
    if not os.path.exists("/tmp/decision_28.py"):
        pytest.skip("2.8 reference not available")
    d28 = importlib.util.module_from_spec(spec); spec.loader.exec_module(d28)
    p = _payload()
    c28 = _cfg(profile="value", maxPerEvent=2)
    new, old = D.run(json.loads(json.dumps(p)), _cfg(profile="value")), d28.run(json.loads(json.dumps(p)), c28)
    # 3.3 changes which lines are shown (no quotas), never a probability, a minimum or a posting rule
    assert [m["markets"] for m in new["matches"]] == [m["markets"] for m in old["matches"]]
    old_keys = {(x["matchId"], x["line"]["key"]) for x in old["products"] if "line" in x}
    new_keys = {(x["matchId"], x["line"]["key"]) for x in new["products"] if "line" in x}
    assert old_keys <= new_keys                                   # every single 2.8 showed is still shown


def test_hitrate_profile_shows_only_likely_bets_and_changes_no_number():
    p = _payload()
    v = D.run(json.loads(json.dumps(p)), _cfg(profile="value"))
    h = D.run(json.loads(json.dumps(p)), _cfg(profile="hitrate"))
    assert [m["markets"] for m in v["matches"]] == [m["markets"] for m in h["matches"]]     # probabilities, minimums, rules
    singles = [x for x in h["products"] if x["tier"] not in ("acca", "builder", "combo") and x.get("kind") != "check"]
    assert all(x["line"]["prob"] >= 60 and x["tier"] in ("safe", "value") for x in singles)
    assert all(x["line"]["v1"]["post"] for x in singles)                                 # still passes the value rules
    assert not [x for x in h["products"] if x["tier"] == "acca"]
    assert all(x["confidence"] >= 30 for x in h["products"] if x["tier"] == "builder")
    assert h["profile"] == "hitrate" and h["otherProfileProducts"] == v["products"]
    assert v["otherProfileProducts"] == h["products"]


def test_hitrate_profile_never_posts_a_line_the_value_rules_reject():
    p = {"now": NOW, "matches": [priced_match(i, odds_factor=0.95) for i in range(8)], "markets": STATUSES,
         "openBets": []}                                       # every price below fair: nothing has value
    h = D.run(json.loads(json.dumps(p)), _cfg(profile="hitrate"))
    assert not [x for x in h["products"] if x["tier"] not in ("builder",) and x.get("kind") is None]
    # 3.2 extras never contain a priced line that fails the value rules: their legs are either posted lines or
    # lines without any public price (to be checked at BetMGM against Jarvis' minimum)
    for x in h["products"]:
        for r in ([x["line"]] if "line" in x else [l.get("line", l) for l in x.get("legs", [])]):
            assert r["v1"]["post"] or r["odds"] is None


# ---------------------------------------------------------------- 3.2: hit-rate extras
X_STATUS = dict(STATUSES, **{g: {"status": "trial"} for g in ("Corners", "Team corners", "Cards", "Player shots",
                                                                "Player shots on target")})


def x_match(i, players=True, **kw):
    m = football_match(**dict({"id": f"x{i}", "event": f"XH{i} – XA{i}", "start": "2026-11-25T20:00:00Z"}, **kw))
    m["inputs"]["home"].update(name=f"XH{i}", xgf=2.0, xga=1.3)
    m["inputs"]["away"].update(name=f"XA{i}", xgf=1.5, xga=1.8)
    if players:
        m["inputs"]["players"] = [{"name": f"Striker{i}", "team": "home", "exp_minutes": 90, "minutes": 1800, "shots_p90": 5.2, "sot_p90": 2.4,
                                   "status": "fit", "opp_factor": 1.0}]
    return m


def x_run(ms, **cfg):
    return D.run({"now": NOW, "matches": ms, "markets": X_STATUS, "openBets": []}, _cfg(profile="hitrate", **cfg))


def test_extras_produce_check_singles_combos_and_player_builders_without_changing_numbers():
    ms = [x_match(i) for i in range(3)] + [x_match(i, players=False) for i in range(3, 6)]
    h, v = x_run(json.loads(json.dumps(ms))), D.run({"now": NOW, "matches": json.loads(json.dumps(ms)), "markets": X_STATUS,
                                                     "openBets": []}, _cfg(profile="value"))
    assert [m["markets"] for m in h["matches"]] == [m["markets"] for m in v["matches"]]      # no probability or minimum moves
    kinds = {x.get("kind") for x in h["products"]}
    assert {"combo", "playerBuilder"} <= kinds
    hr = CFG["hitrate"]
    for x in h["products"]:
        if x.get("kind") == "check":
            r = x["line"]
            assert r["odds"] is None and r["prob"] >= hr["check"]["minProb"] * 100 and x["needsPrice"]
            assert hr["check"]["minOdds"][0] <= r["minOdds"] <= hr["check"]["minOdds"][1]
        if x.get("kind") == "combo":
            ps = [l["line"]["prob"] / 100 for l in x["legs"]]
            assert 2 <= len(ps) <= hr["combo"]["maxLegs"] and len({l["matchId"] for l in x["legs"]}) == len(ps)
            assert math.prod(ps) >= hr["combo"]["minJoint"] - 1e-9 and min(ps) >= hr["combo"]["legMinProb"]
            assert x["minOdds"] >= 1 / math.prod(ps)
        if x.get("kind") == "playerBuilder":
            g, pl = x["legs"]
            assert pl["group"].startswith("Player") and x["confidence"] / 100 == pytest.approx(g["prob"] * pl["prob"] / 1e4, abs=1e-3)
            assert hr["playerBuilder"]["odds"][0] <= x["minOdds"] <= hr["playerBuilder"]["odds"][1]
    # exposure: never more than maxPerEvent products touching one match
    # no cap per match: products depending on the same match say so
    mids = [D._mids(x) for x in h["products"]]
    assert all(set(x.get("sharedWith", [])) == {j for j in range(len(mids)) if j != i and mids[i] & mids[j]}
               for i, x in enumerate(h["products"]))
    # a match is used in one combo at most
    combo_m = [l["matchId"] for x in h["products"] if x.get("kind") == "combo" for l in x["legs"]]
    assert len(combo_m) == len(set(combo_m))


def test_extras_off_in_value_profile_and_switchable():
    ms = [x_match(i) for i in range(3)]
    v = D.run({"now": NOW, "matches": ms, "markets": X_STATUS, "openBets": []}, _cfg(profile="value"))
    assert not [x for x in v["products"] if x.get("kind")]
    off = x_run(json.loads(json.dumps(ms)), hitrate=dict(CFG["hitrate"], extras=False))
    assert not [x for x in off["products"] if x.get("kind")]


def test_extras_skip_a_family_where_the_model_disagrees_with_the_market():
    m = x_match(0)
    out = model.run({"sport": "football", **m["inputs"]})["markets"]
    # a corners price far from the model on one line -> no unpriced corner line of this match may be used
    m["marketOdds"] = {D.line_key("Corners", "Over 9.5"): 4.0, D.line_key("Corners", "Under 9.5"): 1.25}
    h = x_run([m])
    used = [x["line"] for x in h["products"] if x.get("kind") == "check"] + \
           [l["line"] for x in h["products"] if x.get("kind") == "combo" for l in x["legs"]] + \
           [l for x in h["products"] if x.get("kind") == "playerBuilder" for l in x["legs"]]
    assert not [r for r in used if r["group"] in ("Corners", "Team corners")]


def test_extras_respect_data_blocks_and_start_time():
    m = x_match(0)
    m["provenance"] = {"F1": {"status": "verified", "source": "s"}, "F5": {"status": "unreachable", "note": "x"},
                       "F2": {"status": "verified", "source": "s"}, "F3": {"status": "verified", "source": "s"}}
    h = x_run([m, x_match(1)])
    for x in h["products"]:
        rows = [x["line"]] if "line" in x else [l.get("line", l) for l in x.get("legs", [])]
        assert not [r for r in rows if r["group"].startswith("Player") and "x0" in D._mids(x)]
    late = x_match(2, start="2026-11-25T12:10:00Z")              # 10 minutes after NOW: too soon
    assert not [x for x in x_run([late])["products"]]


def test_no_bet_is_offered_twice_across_singles_checks_combos_and_builders():
    ms = [x_match(i) for i in range(4)]
    for m in ms:
        m["inputs"]["players"].append({"name": f"Wing{m['id']}", "team": "away", "exp_minutes": 90, "minutes": 1800, "shots_p90": 4.6,
                                       "sot_p90": 2.1, "status": "fit", "opp_factor": 1.0})
    h = x_run(ms)
    seen = set()
    for x in h["products"]:
        for mid, r in D._rows_of(x):
            k = (mid, D._gkey(r))
            assert k not in seen, k                 # one market group / one player per match, once
            seen.add(k)


def test_unpriced_corners_and_cards_only_from_the_calibrated_v2_model():
    st = dict(X_STATUS, **{g: {"status": "trial"} for g in ("Corners", "Team corners", "Cards")})
    def run_with(league):
        ms = []
        for i in range(3):
            m = x_match(i, players=False)
            m["inputs"]["home"].update(corners_for=7.5, corners_against=3.0, corners_n=7, cards_n=8)
            m["inputs"]["away"].update(corners_for=3.5, corners_against=6.5, corners_n=7, cards_n=8)
            if league:
                m["inputs"]["league"] = {"corners_home_pg": 5.4, "corners_away_pg": 4.5, "yellow_pg": 4.1}
            ms.append(m)
        return D.run({"now": NOW, "matches": ms, "markets": st, "openBets": []}, _cfg(profile="hitrate"))
    rows = lambda res: [r for x in res["products"] for _, r in D._rows_of(x)]
    old = rows(run_with(False))
    assert not [r for r in old if r["group"] in ("Corners", "Team corners", "Cards")]
    new = run_with(True)
    assert all(m["expected"].get("setPieceModel", "").startswith("v2") for m in new["matches"])
    assert [r for r in rows(new) if r["group"] in ("Corners", "Team corners", "Cards")]


def test_market_probabilities_are_injected_from_a_complete_match_result_set():
    m = priced_match(3)
    out = D.process_match(m, STATUSES, NOW, CFG)
    mk = D.market_1x2(dict(m["inputs"]), m["marketOdds"])
    assert mk and abs(mk["ph"] + mk["pa"]) < 1
    m2 = priced_match(3)
    m2["marketOdds"].pop(D.line_key("Match result", "Draw"))
    assert D.market_1x2(dict(m2["inputs"]), m2["marketOdds"]) is None


def test_cross_league_elo_moves_goal_rates_and_builder_grid_follows():
    m = football_match()
    base = model.goal_rates(dict(m["inputs"], sport="football"))
    same = model.goal_rates(dict(m["inputs"], sport="football", league_elo={"home": 1500, "away": 1500}))
    assert same == pytest.approx(base)
    weak_home = dict(m["inputs"], sport="football", league_elo={"home": 1433, "away": 1614.5})
    lh, la = model.goal_rates(weak_home)
    f = math.exp(model.P["elo_c"] * (1433 - 1614.5) / 400)
    assert (lh, la) == pytest.approx((base[0] * f, base[1] / f))
    assert D.football_lambdas(weak_home) == pytest.approx((lh, la))          # builder grid uses the same rates
    p = {x["selection"]: x["prob"] for x in model.run(weak_home)["markets"] if x["group"] == "Match result"}
    p0 = {x["selection"]: x["prob"] for x in model.run(dict(m["inputs"], sport="football"))["markets"] if x["group"] == "Match result"}
    assert p["Alpha"] < p0["Alpha"]


def test_36_line_window_and_favourites():
    m = {"expected": {"corners": 9.6, "cards": 4.2, "corners_home": 5.5, "corners_away": 4.1}, "grid": {"home": "A"}}
    near = lambda g, sel: D._near(m, {"group": g, "selection": sel}, CFG)
    assert near("Corners", "Under 11.5") and not near("Corners", "Under 12.5")
    assert near("Cards", "Over 3.5") and not near("Cards", "Under 6.5")
    assert near("Team corners", "A over 4.5") and not near("Team corners", "A over 2.5")
    assert near("Total goals", "Over 1.5")
    ms = [priced_match(i) for i in range(3)]
    res = D.run({"now": NOW, "matches": ms, "markets": STATUSES, "openBets": [], "config": {"mode": "active"}})
    assert isinstance(res["favourites"], list)
    assert len({f["matchId"] for f in res["favourites"]}) == len(res["favourites"])
    assert all(f["prob"] >= 72 for f in res["favourites"])
    per = {}
    for b in res["products"]:
        for mid in D._mids(b):
            per[mid] = per.get(mid, 0) + 1
    assert all(v <= 2 for v in per.values())


def test_38_market_anchor():
    D.DEFAULT_CONFIG["marketAnchor"] = True
    try:
        cfg = D.load_config()
        ms = [priced_match(i) for i in range(3)]
        res = D.run({"now": NOW, "matches": ms, "markets": STATUSES, "openBets": [], "config": {"mode": "active"}})
        rows = [r for m in res["matches"] for r in m["markets"]]
        anchored = [r for r in rows if r.get("anchored")]
        assert anchored, "complete Pinnacle sets anchor the probability"
        for r in anchored:
            assert r["prob"] == r["pMarket"] and "probModel" in r and r["gap"] == 0
            if r.get("odds") is not None:
                assert not r["v1"]["post"]      # Pinnacle-only price: never a value single
        assert not [b for b in res["products"] if "line" in b and not b.get("kind")]
        # a typed bookmaker price that beats the minimum does post
        m = priced_match(0)
        key = next(r["key"] for r in anchored if r["key"].startswith("Match_result") and r["key"] in m["marketOdds"])
        prow = next(r for r in anchored if r["key"] == key)
        m["mgmLines"] = {key: round(prow["minOdds"] + 0.3, 2)}
        res2 = D.run({"now": NOW, "matches": [m], "markets": STATUSES, "openBets": [], "config": {"mode": "active"}})
        r2 = next(r for r in res2["matches"][0]["markets"] if r["key"] == key)
        assert "reference" not in " ".join(r2["v1"]["why"])
    finally:
        D.DEFAULT_CONFIG["marketAnchor"] = False


def test_39_the_price_hunt_needs_pinnacle_to_have_moved_toward_the_pick():
    """research/line_drift.json: beating Pinnacle's fair price pays +1.5% closing-line value on its own, +18.5%
    where Pinnacle itself has moved >= 2 points toward the pick. So no movement, no hunt."""
    D.DEFAULT_CONFIG["marketAnchor"] = True
    try:
        cfg = D.load_config()
        ms = [priced_match(i) for i in range(4)]
        quiet = D.run({"now": NOW, "matches": [dict(m) for m in ms], "markets": STATUSES, "openBets": [],
                       "config": {"mode": "active"}})
        assert quiet["hunts"] == [], "nothing has moved, so nothing is hunted"
        # give one long-odds outcome of each match a drift, two of them big enough
        picked = []
        for m, move in zip(ms, (0.035, 0.021, 0.005, -0.040)):
            key = max((k for k in m["marketOdds"] if k.startswith("Match_result")), key=lambda k: m["marketOdds"][k])
            m["drift"] = {key: {"move": move, "first": 0.20, "since": "2026-11-25T09:00:00Z"}}
            picked.append((m["id"], key, move))
        res = D.run({"now": NOW, "matches": ms, "markets": STATUSES, "openBets": [], "config": {"mode": "active"}})
        got = {(h["matchId"], h["key"]) for h in res["hunts"]}
        for mid, key, move in picked:
            fair_ok = any(r["key"] == key and cfg["hunt"]["fair"][0] <= 100 / r["prob"] <= cfg["hunt"]["fair"][1]
                          for mm in res["matches"] if mm["id"] == mid for r in mm["markets"])
            assert ((mid, key) in got) == (move >= cfg["hunt"]["driftMin"] and fair_ok), (mid, move)
        for h in res["hunts"]:
            assert h["move"] >= 100 * cfg["hunt"]["driftMin"] and h["fair"] >= cfg["hunt"]["fair"][0]
            assert h["minOdds"] >= h["fair"]          # the hunt never asks for less than Pinnacle's fair price
        assert len(res["hunts"]) <= cfg["hunt"]["max"]
    finally:
        D.DEFAULT_CONFIG["marketAnchor"] = False


def test_39_only_lines_a_bookmaker_quotes_are_offered():
    """Pinnacle quoted corner totals of 8.5-11.5 on 117 matches and never 12.5, and the owner found 12.5 corners
    and 6.5 cards missing at all three of his bookmakers. Such lines are no longer offered, however likely."""
    cfg = D.load_config()
    rng = cfg["hitrate"]["offerLines"]
    m = {"expected": {"corners": 11.0, "cards": 5.2, "corners_home": 5.8, "corners_away": 5.2},
         "grid": {"home": "Alpha", "away": "Beta"}}
    inside = [("Corners", "Over 10.5"), ("Cards", "Over 4.5"), ("Team corners", "Alpha over 4.5")]
    outside = [("Corners", "Over 12.5"), ("Corners", "Under 7.5"), ("Cards", "Over 6.5"),
               ("Team corners", "Alpha over 6.5")]
    for group, sel in inside:
        assert D._near(m, {"group": group, "selection": sel}, cfg), (group, sel)
    for group, sel in outside:
        assert not D._near(m, {"group": group, "selection": sel}, cfg), (group, sel)
    # the window around the expected count still applies inside the quoted range
    assert not D._near(dict(m, expected=dict(m["expected"], corners=8.6)),
                       {"group": "Corners", "selection": "Over 11.5"}, cfg)
    # and a market without a quoted range (goals) is untouched
    assert D._near(m, {"group": "Total goals", "selection": "Over 2.5"}, cfg)
    assert all(rng[g][0] <= rng[g][1] for g in rng)
