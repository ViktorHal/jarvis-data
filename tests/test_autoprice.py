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
        board, payload = A.build(odds["meta"]["fetchedAt"])
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
