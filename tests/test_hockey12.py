"""Hockey model 1.2: State, match blocks, and its use in decision.py 3.0 (with 1.1 as the fallback/shadow)."""
import hashlib
import json
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
import decision as D  # noqa: E402
import hockey12 as H  # noqa: E402
import model  # noqa: E402

NOW = "2026-11-26T12:00:00Z"
CFG = D.load_config()


def small_state():
    st = H.State()
    games = [("20252026", "2026-04-01", ("A", 31, 27, 3, 2), ("B", 27, 31, 2, 3), ("Ga", 25, 27), ("Gb", 28, 31)),
             ("20252026", "2026-04-03", ("B", 35, 22, 4, 1), ("C", 22, 35, 1, 4), ("Gb", 21, 22), ("Gc", 31, 35)),
             ("20262027", "2026-10-08", ("C", 30, 30, 2, 2), ("A", 30, 30, 2, 2), ("Gc", 28, 30), ("Ga", 28, 30)),
             ("20262027", "2026-11-25", ("A", 29, 33, 1, 3), ("B", 33, 29, 3, 1), ("Ga", 30, 33), ("Gb", 28, 29))]
    for season, day, r1, r2, g1, g2 in games:
        st.update(season, day, [r1, r2], [g1, g2])
    return st


def block(**kw):
    snap = small_state().snapshot()
    b = H.match_block(snap, "A", "B", "Ga", "Gb", False, True)
    b.update(kw)
    return b


def prov_h6(at="2026-11-26T10:20:00Z", status="verified"):
    return {"status": status, "source": "jarvis-data/data/nhl/h12_state.json", "repoCommit": "abc", "repoFetchedAt": at, "at": at}


def hockey_match(h12=None, prov=None, **kw):
    m = {"id": "2026-11-26-a-b", "event": "A – B", "sport": "Ice hockey", "start": "2026-11-27T00:00:00Z",
         "inputs": {"event": "A – B", "home": {"name": "A", "gf": 3.1, "ga": 2.9}, "away": {"name": "B", "gf": 2.8, "ga": 3.2},
                    "players": [{"name": "Star", "sog_pg": 3.4, "points_pg": 1.1}]},
         "flags": {"defaulted": 0, "stale": 0, "lineup": "probable", "priceSources": 1, "primarySource": True},
         "marketOdds": {}}
    if h12 is not None:
        m["h12"] = h12
    if prov is not None:
        m["provenance"] = prov
    m.update(kw)
    return m


# ---------------------------------------------------------------- State
def test_snapshot_roundtrip_is_exact():
    st = small_state()
    st2 = H.State.from_snapshot(json.loads(json.dumps(st.snapshot())))
    L = st.league()
    assert st2.league() == L
    for t in ("A", "B", "C"):
        assert vars_of(st2.teams[t]) == vars_of(st.teams[t])
    assert st2.goalies == st.goalies
    st.update("20262027", "2026-11-26", [("B", 30, 28, 2, 1), ("C", 28, 30, 1, 2)], [("Gb", 27, 28)])
    st2.update("20262027", "2026-11-26", [("B", 30, 28, 2, 1), ("C", 28, 30, 1, 2)], [("Gb", 27, 28)])
    assert st2.snapshot() == st.snapshot()


def vars_of(t):
    return (t.sf, t.sa, t.gf, t.ga, t.n, t.last_date)


def test_season_carry_caps_evidence():
    st = H.State()
    for i in range(40):
        st.update("20252026", f"2026-01-{1 + i % 28:02d}", [("A", 30, 30, 3, 3), ("B", 30, 30, 3, 3)], [])
    n_before = st.teams["A"].n
    st.start_season("20262027")
    assert st.teams["A"].n == pytest.approx(min(n_before, 20) * 2 / 3)
    assert st.teams["A"].sf / st.teams["A"].n == pytest.approx(30)     # per-game rates are unchanged


def test_block_rates_equal_direct_rates():
    st = small_state()
    b = H.match_block(st.snapshot(), "A", "B", "Ga", "Gb", False, True)
    direct = H.goal_rates(st.teams["A"], st.teams["B"], tuple(st.goalies["Ga"]), tuple(st.goalies["Gb"]),
                          st.league(), H.PARAMS, False, True)
    assert H.rates_from_block(json.loads(json.dumps(b))) == pytest.approx(direct, abs=1e-12)


def test_unknown_named_goalie_gets_league_prior_and_no_starter_uses_team():
    snap = small_state().snapshot()
    b = H.match_block(snap, "A", "B", "Nobody", None)
    assert b["goalieHome"] == {"name": "Nobody", "saves": 0.0, "shotsAgainst": 0.0, "inData": False}
    assert b["goalieAway"] is None


def test_back_to_back_raises_opponent_goals():
    b0, b1 = block(b2bAway=False), block(b2bAway=True)
    h0, a0 = H.rates_from_block(b0)
    h1, a1 = H.rates_from_block(b1)
    # 1.3 sharpens the two rates after the back-to-back factor, so the gap widens rather than one rate moving
    assert h1 > h0 and a1 < a0
    assert h1 / a1 > h0 / a0


def test_13_sharpening_keeps_the_sum_and_widens_the_gap():
    lh, la = H.sharpen(3.3, 2.5, 1.3)
    assert lh + la == pytest.approx(3.3 + 2.5)
    assert lh > 3.3 and la < 2.5
    assert H.sharpen(3.0, 3.0, 1.4) == pytest.approx((3.0, 3.0))
    assert H.sharpen(3.3, 2.5, 1.0) == pytest.approx((3.3, 2.5))
    assert H.PARAMS["sharpen"] == 1.4 and H.PARAMS_SHL["sharpen"] == 1.3


def test_better_goalie_lowers_goals_against():
    good, bad = block(goalieAway={"name": "x", "saves": 2900, "shotsAgainst": 3100}), block(goalieAway={"name": "y", "saves": 2700, "shotsAgainst": 3100})
    assert H.rates_from_block(good)[0] < H.rates_from_block(bad)[0]


def test_price_restores_global_draw_inflation():
    old = model.P["hockey_draw_inflation"]
    H.price(3.0, 2.8)
    assert model.P["hockey_draw_inflation"] == old


# ---------------------------------------------------------------- decision.py 3.0
def run(m, cfg=None):
    return D.process_match(m, {}, NOW, cfg or CFG)


def test_decision_uses_12_with_fresh_verified_inputs():
    b = block()
    out = run(hockey_match(b, {"H6": prov_h6()}))
    assert out["modelVersion"] == "1.2-hockey"
    assert out["hockeyModel"]["used"] == "1.2"
    lh, la = H.rates_from_block(b)
    ref = H.price(lh, la, ("A", "B"), players=hockey_match()["inputs"]["players"])["markets"]
    ref = {D.line_key(x["group"], x["selection"]): x["prob"] for x in ref}
    for r in out["markets"]:
        assert r["prob"] == ref[r["key"]]
        assert "prob11" in r                                   # 1.1 kept beside it
    p11 = {D.line_key(x["group"], x["selection"]): x["prob"] for x in model.run(dict(hockey_match()["inputs"], sport="hockey"))["markets"]}
    assert all(r["prob11"] == p11[r["key"]] for r in out["markets"])


def test_12_draw_is_higher_than_independent_poisson():
    out = run(hockey_match(block(), {"H6": prov_h6()}))
    draw = next(r for r in out["markets"] if r["group"] == "60-minute result" and r["selection"] == "Draw")["prob"]
    lh, la = H.rates_from_block(block())
    import math
    pois = lambda k, l: math.exp(-l) * l ** k / math.factorial(k)
    raw = sum(pois(i, lh) * pois(i, la) for i in range(12))
    assert draw / 100 > raw * 1.3


def test_player_lines_unchanged_by_12():
    out = run(hockey_match(block(), {"H6": prov_h6()}))
    for r in out["markets"]:
        if r["group"].startswith("Player"):
            assert r["prob"] == r["prob11"]


@pytest.mark.parametrize("h12,prov,cfg,reason", [
    (None, {"H6": prov_h6()}, None, "no h12 inputs"),
    ("block", None, None, "H6 provenance missing"),
    ("block", {"H6": prov_h6(at="2026-11-25T12:00:00Z")}, None, "H6 unverified"),        # 24 h old: never verified
    ("block", {"H6": prov_h6(status="unreachable")}, None, "H6 unreachable"),
    ("params", {"H6": prov_h6()}, None, "h12 params differ from hockey12.py"),
    ("block", {"H6": prov_h6()}, {"hockeyModel": "1.1"}, "hockeyModel set to 1.1"),
])
def test_fallback_to_11_is_recorded(h12, prov, cfg, reason):
    b = block() if h12 == "block" else block(params=dict(H.PARAMS, half_life=25.0)) if h12 == "params" else None
    out = run(hockey_match(b, prov), dict(CFG, **(cfg or {})))
    assert out["modelVersion"] == "1.1"
    assert out["hockeyModel"] == {"used": "1.1", "fallbackReason": reason}
    p11 = {D.line_key(x["group"], x["selection"]): x["prob"] for x in model.run(dict(hockey_match()["inputs"], sport="hockey"))["markets"]}
    assert all(r["prob"] == p11[r["key"]] for r in out["markets"])


def test_h6_replaces_h1_as_required_item_under_12():
    prov = {"H6": prov_h6(), "H1": {"status": "unreachable", "note": "x"}, "H5": {"status": "verified", "source": "s"}}
    out = run(hockey_match(block(), prov))
    ml = [r for r in out["markets"] if r["group"] == "Moneyline (incl. OT)"]
    assert ml and not any("data: H1" in w for r in ml for w in r["v1"]["why"])
    assert out["coverage"]["byMarket"]["Moneyline (incl. OT)"]["required"] == ["H6"]
    assert out["coverage"]["items"]["H6"] == "verified"
    # under 1.1 the same missing H1 blocks the team markets
    out11 = run(hockey_match(block(), prov), dict(CFG, hockeyModel="1.1"))
    assert all(any("data: H1" in w for w in r["v1"]["why"]) for r in out11["markets"] if r["group"] == "Moneyline (incl. OT)")


def test_input_hash_covers_h12_and_football_hash_unchanged():
    a = run(hockey_match(block(), {"H6": prov_h6()}))["decision"]["inputHash"]
    b = run(hockey_match(block(b2bAway=False), {"H6": prov_h6()}))["decision"]["inputHash"]
    assert a != b
    m = {"id": "f", "event": "x", "sport": "Football", "inputs": {"home": {"xgf": 1.5, "xga": 1.2}, "away": {"xgf": 1.1, "xga": 1.4}},
         "flags": {}, "marketOdds": {}}
    assert D.process_match(m, {}, NOW, CFG)["decision"]["inputHash"] == D.input_hash(
        {"inputs": m["inputs"], "flags": {}, "marketOdds": {}, "mgmLines": {}})


def test_football_untouched_by_hockey_switch():
    m = {"id": "f", "event": "x", "sport": "Football", "inputs": {"home": {"xgf": 1.5, "xga": 1.2}, "away": {"xgf": 1.1, "xga": 1.4}},
         "flags": {}, "marketOdds": {}}
    a, b = D.process_match(m, {}, NOW, CFG), D.process_match(m, {}, NOW, dict(CFG, hockeyModel="1.1"))
    assert a["markets"] == b["markets"] and "hockeyModel" not in a


def test_robot_copy_of_hockey12_is_identical():
    other = os.path.join(ROOT, "..", "jarvis-data", "fetch", "hockey12.py")
    if not os.path.exists(other):
        pytest.skip("jarvis-data not checked out next to jarvis")
    h = lambda p: hashlib.sha256(open(p, encoding="utf-8").read().encode()).hexdigest()
    assert h(other) == h(os.path.join(ROOT, "hockey12.py"))


def shl_block(**kw):
    st = H.State(league="shl")
    for r1, r2 in ((("A", 31, 27, 3, 2), ("B", 27, 31, 2, 3)), (("B", 30, 25, 2, 1), ("A", 25, 30, 1, 2))):
        st.update("2026-27", "2026-11-20", [r1, r2], [("Ga", 25, 27), ("Gb", 28, 31)])
    b = H.match_block(st.snapshot(), "A", "B", "Ga", "Gb")
    b.update(kw)
    return b


def test_shl_block_uses_shl_parameters():
    b = shl_block()
    assert b["leagueCode"] == "shl" and b["params"] == H.PARAMS_SHL
    out = run(hockey_match(b, {"H6": prov_h6()}))
    assert out["hockeyModel"]["used"] == "1.2" and out["hockeyModel"]["league"] == "shl"
    lh, la = H.rates_from_block(b)
    ref = {D.line_key(x["group"], x["selection"]): x["prob"]
           for x in H.price(lh, la, ("A", "B"), H.PARAMS_SHL, players=hockey_match()["inputs"]["players"])["markets"]}
    assert all(r["prob"] == ref[r["key"]] for r in out["markets"])
    # the same numbers priced with NHL parameters would differ (draw inflation 1.25 vs 1.5)
    nhl = {D.line_key(x["group"], x["selection"]): x["prob"] for x in H.price(lh, la, ("A", "B"), H.PARAMS)["markets"]}
    assert ref["60_minute_result_Draw"] != nhl["60_minute_result_Draw"]


def test_shl_block_with_nhl_params_or_unknown_league_falls_back():
    out = run(hockey_match(shl_block(params=dict(H.PARAMS)), {"H6": prov_h6()}))
    assert out["hockeyModel"] == {"used": "1.1", "fallbackReason": "h12 params differ from hockey12.py"}
    out = run(hockey_match(shl_block(leagueCode="khl"), {"H6": prov_h6()}))
    assert out["hockeyModel"]["fallbackReason"] == "h12 league khl has no 1.2 parameters"
