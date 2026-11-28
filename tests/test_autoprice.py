import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fetch"))
import autoprice as A  # noqa: E402


class AutoPrice(unittest.TestCase):
    def test_names(self):
        fd = ["Man United", "Man City", "Ath Bilbao", "Ath Madrid", "Sociedad", "Coventry", "Lincoln", "Nott'm Forest", "Dundee", "Dundee United"]
        self.assertEqual(A.match_name("Manchester United", fd), "Man United")
        self.assertEqual(A.match_name("Manchester City", fd), "Man City")
        self.assertEqual(A.match_name("Athletic Bilbao", fd), "Ath Bilbao")
        self.assertEqual(A.match_name("Atletico Madrid", fd), "Ath Madrid")
        self.assertEqual(A.match_name("Real Sociedad", fd), "Sociedad")
        self.assertEqual(A.match_name("Coventry City", fd), "Coventry")
        self.assertEqual(A.match_name("Lincoln City", fd), "Lincoln")
        self.assertEqual(A.match_name("Dundee FC", fd), "Dundee")
        self.assertEqual(A.match_name("Dundee United", fd), "Dundee United")
        self.assertIsNone(A.match_name("Omonia Nicosia", fd))
        self.assertEqual(A.match_name("Kasimpasa", ["Kasımpaşa", "Konyaspor"]), "Kasımpaşa")

    def test_market_fit(self):
        D, model = A.load_jarvis()
        lh, la = A.fit_market_rates(0.78, 0.07, 0.62, model)
        g = model.score_grid(lh, la)
        n = len(g)
        ph = sum(g[i][j] for i in range(n) for j in range(n) if i > j)
        self.assertAlmostEqual(ph, 0.78, delta=0.01)
        self.assertGreater(lh, la)

    def test_build_runs_on_repo_data(self):
        odds = A.load("pinnacle/odds.json")
        if not odds or not odds.get("events"):
            self.skipTest("no odds file")
        # Against COPIES of the stored record: build() updates drift.json and closing.json, and running it
        # here on the real files would add an observation to every live entry on every test run.
        import shutil
        copies = "auto/_test_build_drift.json"
        for src, dst in (("auto/drift.json", copies), ("auto/closing.json", "auto/_test_build_closing.json")):
            full_src, full_dst = os.path.join(A.DATA, src), os.path.join(A.DATA, dst)
            if os.path.exists(full_src):
                shutil.copy2(full_src, full_dst)
            self.addCleanup(lambda p=full_dst: os.path.exists(p) and os.remove(p))
        board, payload = A.build(odds["meta"]["fetchedAt"], drift_path=copies)
        self.assertEqual(len(payload["matches"]), board["meta"]["matches"])
        per = {}
        for i, b in enumerate(board["products"]):
            self.assertEqual(b["rank"], i + 1)
            for mid in {b.get("matchId")} | {l.get("matchId") for l in b.get("legs") or [] if isinstance(l, dict)}:
                if mid:
                    per[mid] = per.get(mid, 0) + 1
        self.assertTrue(all(v <= 2 for v in per.values()))


if __name__ == "__main__":
    unittest.main()


class Drift(unittest.TestCase):
    """3.9: how Pinnacle's own de-margined price has moved since the first time today it was seen."""
    PATH = "auto/_test_drift.json"

    def setUp(self):
        self.full = os.path.join(A.DATA, self.PATH)
        if os.path.exists(self.full):
            os.remove(self.full)

    tearDown = setUp

    @staticmethod
    def match(h, d, a, start="2026-11-27T20:00:00Z"):
        return [{"id": "m1", "start": start,
                 "marketOdds": {"Match_result_Home": h, "Match_result_Draw": d, "Match_result_Away": a},
                 "priceSets": [["Match_result_Home", "Match_result_Draw", "Match_result_Away"]]}]

    def test_first_and_last_are_kept_and_the_move_is_de_margined(self):
        t0 = A.datetime(2026, 11, 27, 9, 0, tzinfo=A.timezone.utc)
        first = A.drift_update(self.match(2.00, 3.40, 4.00), t0, self.PATH)
        self.assertAlmostEqual(first["m1|Match_result_Home"]["first"], 0.4789, places=3)
        self.assertEqual(first["m1|Match_result_Home"]["n"], 1)
        # the home price shortens: Pinnacle has moved toward the home side
        later = A.drift_update(self.match(1.80, 3.50, 4.50), t0 + A.timedelta(hours=2), self.PATH)
        e = later["m1|Match_result_Home"]
        self.assertEqual(e["n"], 2)
        self.assertAlmostEqual(e["first"], 0.4789, places=3)      # never overwritten
        self.assertGreater(e["last"] - e["first"], 0.02)          # a real move, two points or more
        self.assertLess(later["m1|Match_result_Away"]["last"] - later["m1|Match_result_Away"]["first"], 0)

    def test_an_incomplete_price_set_is_ignored(self):
        now = A.datetime(2026, 11, 27, 9, 0, tzinfo=A.timezone.utc)
        self.assertEqual(A.drift_update(self.match(2.00, None, 4.00), now, self.PATH), {})

    def test_old_matches_drop_out(self):
        t0 = A.datetime(2026, 11, 27, 9, 0, tzinfo=A.timezone.utc)
        A.drift_update(self.match(2.00, 3.40, 4.00, start="2026-11-20T20:00:00Z"), t0, self.PATH)
        self.assertEqual(A.drift_update([], t0 + A.timedelta(hours=1), self.PATH), {})


class Closing(unittest.TestCase):
    """3.9: the last Pinnacle probability before kick-off, kept so realised closing-line value can be measured."""
    DRIFT, CLOSE = "auto/_test_drift2.json", "auto/_test_close.json"

    def setUp(self):
        for f in (self.DRIFT, self.CLOSE):
            p = os.path.join(A.DATA, f)
            if os.path.exists(p):
                os.remove(p)

    tearDown = setUp

    def run_at(self, h, d, a, now, start="2026-11-27T20:00:00Z"):
        ms = [{"id": "m1", "start": start,
               "marketOdds": {"Match_result_Home": h, "Match_result_Draw": d, "Match_result_Away": a},
               "priceSets": [["Match_result_Home", "Match_result_Draw", "Match_result_Away"]]}]
        return A.closing_update(A.drift_update(ms, now, self.DRIFT), now, self.CLOSE)

    def test_nothing_is_recorded_before_kick_off_then_written_once(self):
        t0 = A.datetime(2026, 11, 27, 18, 0, tzinfo=A.timezone.utc)
        self.assertEqual(self.run_at(2.00, 3.40, 4.00, t0), {})
        self.run_at(1.80, 3.50, 4.60, t0 + A.timedelta(hours=1, minutes=50))
        after = self.run_at(1.80, 3.50, 4.60, t0 + A.timedelta(hours=2, minutes=5))
        e = after["m1|Match_result_Home"]
        self.assertAlmostEqual(e["open"], 0.47887, places=4)
        self.assertAlmostEqual(e["close"], 0.52477, places=4)
        # a later run must never move a recorded closing price
        self.assertEqual(self.run_at(1.50, 4.00, 5.50, t0 + A.timedelta(hours=3)), after)

    def test_old_closing_prices_drop_out(self):
        t0 = A.datetime(2026, 11, 27, 21, 0, tzinfo=A.timezone.utc)
        self.assertTrue(self.run_at(2.00, 3.40, 4.00, t0))
        self.assertEqual(A.closing_update({}, t0 + A.timedelta(days=A.CLOSE_KEEP_D + 1), self.CLOSE), {})

