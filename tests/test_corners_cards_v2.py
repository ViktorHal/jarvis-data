"""model.py corners/cards v2 computes exactly what the backtest (research/corners_cards_v2.py) tested, times
the calibration multipliers fitted on 27 Nov 2026 (research/cc_fit.json, research/team_corners_fit.json)."""
import math
import os
import random
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "research"))
import model  # noqa: E402
import corners_cards_v2 as V  # noqa: E402

PC = {"k": model.P["cc_k"], "w": model.P["cc_w"], "b": model.P["cc_b"], "c": 0.0, "s": 1.0}
PY = {"kt": model.P["cards_k"], "kr": model.P["cards_k"], "wt": model.P["cards_wt"], "wr": model.P["cards_wr"],
      "d": model.P["cards_d"], "sy": 1.0}


def sample(rnd):
    x = {"cf_h": rnd.uniform(3, 8), "ca_h": rnd.uniform(3, 7), "n_h": rnd.randint(5, 9), "cf_a": rnd.uniform(3, 8),
         "ca_a": rnd.uniform(3, 7), "n_a": rnd.randint(5, 9), "lh": rnd.uniform(4.8, 5.8), "la": rnd.uniform(4.0, 4.8),
         "y_h": rnd.uniform(1.2, 2.6), "y_a": rnd.uniform(1.2, 2.6), "ny": 0, "ly": rnd.uniform(3.5, 4.8),
         "ref_y": rnd.uniform(3, 5.5), "ref_n": rnd.randint(5, 15), "ph": rnd.uniform(0.15, 0.7), "pov": 0.5}
    x["pa"] = rnd.uniform(0.1, 0.85 - x["ph"])
    x["ny"] = min(x["n_h"], x["n_a"])
    return x


def as_input(x):
    return {"sport": "football", "home": {"name": "H", "xgf": 1.5, "xga": 1.2, "corners_for": x["cf_h"], "corners_against": x["ca_h"],
                                          "corners_n": x["n_h"], "cards": x["y_h"], "cards_n": x["n_h"]},
            "away": {"name": "A", "xgf": 1.2, "xga": 1.5, "corners_for": x["cf_a"], "corners_against": x["ca_a"],
                     "corners_n": x["n_a"], "cards": x["y_a"], "cards_n": x["n_a"]},
            "league": {"corners_home_pg": x["lh"], "corners_away_pg": x["la"], "yellow_pg": x["ly"]},
            "referee": {"yellow_pg": x["ref_y"], "games": x["ref_n"]}, "market": {"ph": x["ph"], "pa": x["pa"]}}


def test_model_matches_backtest():
    rnd = random.Random(7)
    for _ in range(50):
        x = sample(rnd)
        out = model.run(as_input(x))
        e = out["expected"]
        mh0, ma0 = V.corners(x, PC)
        mh, ma = mh0 * model.P["cc_home_mult"], ma0 * model.P["cc_away_mult"]
        assert e["corners_home"] == pytest.approx(round(mh, 2), abs=0.006)
        assert e["corners_away"] == pytest.approx(round(ma, 2), abs=0.006)
        assert e["corners"] == pytest.approx(round((0.5 * (mh0 + ma0) + 0.5 * (x["lh"] + x["la"])) * model.P["cc_total_mult"], 1), abs=0.051)
        assert e["cards"] == pytest.approx(round(V.cards(x, PY) * model.P["cards_mult"], 2), abs=0.006)
        assert e["setPieceModel"] == "v2" and e["cardsModel"] == "v2"
        p = {(r["group"], r["selection"]): r["prob"] for r in out["markets"]}
        q = 1 - V.cdf_table(mh, model.P["cc_team_r"])[4]
        if ("Team corners", "H over 4.5") in p:
            assert p[("Team corners", "H over 4.5")] == pytest.approx(
                round((0.5 + model.P["cc_team_shrink"] * (q - 0.5)) * 100, 1), abs=0.051)


def test_without_league_block_the_old_model_is_used():
    rnd = random.Random(3)
    x = sample(rnd)
    m = as_input(x)
    m.pop("league")
    e = model.run(m)["expected"]
    assert "setPieceModel" not in e
    assert e["corners"] == round((x["cf_h"] + x["ca_a"]) / 2 + (x["cf_a"] + x["ca_h"]) / 2, 1)


def test_favourite_gets_more_corners():
    rnd = random.Random(5)
    x = sample(rnd)
    fav, dog = dict(x, ph=0.7, pa=0.1), dict(x, ph=0.1, pa=0.7)
    a, b = model.run(as_input(fav))["expected"], model.run(as_input(dog))["expected"]
    assert a["corners_home"] > b["corners_home"] and a["corners_away"] < b["corners_away"]


def test_player_shots_match_the_backtested_negative_binomial():
    import player_shots_backtest as PB
    m = {"sport": "football", "home": {"name": "H", "xgf": 1.5, "xga": 1.2}, "away": {"name": "A", "xgf": 1.2, "xga": 1.5},
         "players": [{"name": "Striker", "team": "home", "shots_p90": 3.4, "sot_p90": 1.3, "exp_minutes": 78}]}
    p = {(r["group"], r["selection"]): r["prob"] for r in model.run(m)["markets"]}
    x = {"shots_p90": 3.4, "sot_p90": 1.3, "avg_start_min": 78}
    P = {"minutes": "avg", "scale": 1.0, "r": 3}
    assert p[("Player shots on target", "Striker 1+")] == pytest.approx(round(PB.p_atleast(1, PB.lam_of(x, "sot", P), 3) * 100, 1), abs=0.051)
    assert p[("Player shots", "Striker 2+")] == pytest.approx(round(PB.p_atleast(2, PB.lam_of(x, "shots", P), 3) * 100, 1), abs=0.051)
