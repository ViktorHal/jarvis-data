"""Hockey 1.2 robot: replay on a seed equals one straight replay; match helper copies exact numbers."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "fetch"))
import h12_match  # noqa: E402
import h12_nhl  # noqa: E402
import hockey12 as H  # noqa: E402

FIX = json.load(open(os.path.join(HERE, "fixtures", "nhl_games_40.json")))


class Replay(unittest.TestCase):
    def setUp(self):
        self.games = h12_nhl.games_from_rows(FIX["team"], FIX["goalie"], "20252026")

    def test_pairs_games_and_starters(self):
        self.assertEqual(len(self.games), 40)
        self.assertTrue(all(g["gs_h"] and g["gs_a"] for g in self.games))
        self.assertTrue(all(g["gs_h"]["teamAbbrev"] == g["habbr"] for g in self.games))

    def test_seed_plus_rest_equals_straight_replay(self):
        full = h12_nhl.replay(H.State(), self.games).snapshot()
        cut = self.games[19]["date"]
        first = [g for g in self.games if g["date"] <= cut]
        seed = json.loads(json.dumps(h12_nhl.replay(H.State(), first).snapshot()))
        rest_ids = {g["id"] for g in self.games if g["date"] > cut}
        st, _ = h12_nhl.build(seed, [r for r in FIX["team"] if r["gameId"] in rest_ids],
                              [r for r in FIX["goalie"] if r["gameId"] in rest_ids], "20252026")
        self.assertEqual(json.loads(json.dumps(st.snapshot())), json.loads(json.dumps(full)))

    def test_overlap_with_seed_is_refused(self):
        seed = json.loads(json.dumps(h12_nhl.replay(H.State(), self.games).snapshot()))
        with self.assertRaises(ValueError):
            h12_nhl.build(seed, FIX["team"], FIX["goalie"], "20252026")

    def test_real_seed_file_is_loadable(self):
        seed = json.load(open(os.path.join(HERE, "..", "data", "nhl", "h12_seed.json")))
        st = H.State.from_snapshot(seed)
        self.assertEqual(st.games, 9781)
        self.assertEqual(seed["params"], H.PARAMS)


class Match(unittest.TestCase):
    def setUp(self):
        self.snap = json.loads(json.dumps(h12_nhl.replay(H.State(), h12_nhl.games_from_rows(FIX["team"], FIX["goalie"], "20252026")).snapshot()))
        self.snap["meta"] = {"fetchedAt": "2026-11-26T10:20:00Z"}

    def test_team_lookup_by_name_abbrev_and_without_accents(self):
        name = next(k for k in self.snap["teams"] if "Montr" in k)
        self.assertEqual(h12_match.find_team(self.snap, name), name)
        self.assertEqual(h12_match.find_team(self.snap, "MTL"), name)
        self.assertEqual(h12_match.find_team(self.snap, "montreal canadiens"), name)
        self.assertIsNone(h12_match.find_team(self.snap, "Nowhere"))

    def test_abbreviation_prefers_current_franchise(self):
        snap = json.load(open(os.path.join(HERE, "..", "data", "nhl", "h12_seed.json")))
        self.assertEqual(h12_match.find_team(snap, "UTA"), "Utah Mammoth")

    def test_block_copies_exact_numbers_and_flags_b2b(self):
        T = self.snap["teams"]
        h, a = sorted(T)[:2]
        prev = T[h]["lastDate"]
        from datetime import date, timedelta
        day = (date.fromisoformat(prev) + timedelta(days=1)).isoformat()
        b = h12_match.block(self.snap, h, a, day)
        self.assertTrue(b["b2bHome"])
        for f in ("sf", "sa", "gf", "ga", "n"):
            self.assertEqual(b["home"][f], T[h][f])
        self.assertEqual(b["params"], self.snap["params"])
        self.assertFalse(b["stateBehind"])                       # fetched 26 Nov, game long before
        far = h12_match.block(self.snap, h, a, "2026-11-26")       # fetched 10:20 UTC on game day: complete
        self.assertFalse(far["stateBehind"])
        self.assertFalse(far["b2bHome"])
        nxt = h12_match.block(self.snap, h, a, "2026-11-27")       # 26 Nov games not finished at fetch
        self.assertTrue(nxt["stateBehind"])
        self.assertFalse(h12_match.block(self.snap, h, a, "2026-11-26", league="shl")["stateBehind"])
        self.assertTrue(h12_match.block(self.snap, h, a, "2026-11-27", league="shl")["stateBehind"])


if __name__ == "__main__":
    unittest.main()


class Shl(unittest.TestCase):
    def test_seed_and_replay(self):
        import h12_shl
        import shl_games
        seed = json.load(open(os.path.join(HERE, "..", "data", "shl", "h12_seed.json"), encoding="utf-8"))
        self.assertEqual(seed["leagueCode"], "shl")
        self.assertEqual(seed["params"], H.PARAMS_SHL)
        games = [shl_games.parse_game(open(os.path.join(HERE, "fixtures", f"shl_game_{i}.html"), encoding="utf-8").read(), i)
                 for i in ("1109941", "1109942", "1109998")]
        st = h12_shl.build(seed, games)
        self.assertEqual(st.games, seed["games"] + 3)
        self.assertEqual(st.teams["Färjestad BK"].last_date, "2026-10-15")
        self.assertEqual(st.goalies["Lassinantti, Joel"][1] - seed["goalies"].get("Lassinantti, Joel", [0, 0])[1], 32)
        snap = json.loads(json.dumps(st.snapshot()))
        snap["meta"] = {"fetchedAt": "2026-11-26T10:20:00Z"}
        b = h12_match.block(snap, "FBK", "Luleå HF", "2026-11-27", "Thelin, Melker", "-", league="shl")
        self.assertEqual((b["leagueCode"], b["home"]["team"], b["goalieAway"]), ("shl", "Färjestad BK", None))
        with self.assertRaises(ValueError):
            h12_shl.build(json.loads(json.dumps(st.snapshot())), games)
