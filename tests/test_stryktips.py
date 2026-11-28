"""The 16-row system is a decision rule, so it is tested like one (28 Nov 2026)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "research"))
import stryktips_system as S  # noqa: E402


def coupon(matches):
    return {"round": "test", "system": {"signs": ["1"] * len(matches)},
            "matches": [{"no": i + 1, "home": f"H{i}", "away": f"A{i}", "p": p, "people": q}
                        for i, (p, q) in enumerate(matches)]}


def write(tmp, matches):
    import json
    json.dump(coupon(matches), open(tmp, "w"))
    return S.load(tmp)


class Systems(unittest.TestCase):
    TMP = "/tmp/_test_stryktips.json"

    def tearDown(self):
        if os.path.exists(self.TMP):
            os.remove(self.TMP)

    def even(self, n=13):
        return [({"1": 45.0, "X": 27.0, "2": 28.0}, {"1": 45, "X": 27, "2": 28}) for _ in range(n)]

    def test_the_budget_is_never_exceeded_and_every_match_is_covered(self):
        _, ms = write(self.TMP, self.even())
        for lam in (0, 1, 3, 20):
            sysm = S.best_for(ms, lam)
            rows = 1
            for sub in sysm:
                rows *= len(sub)
                self.assertTrue(1 <= len(sub) <= 3)
            self.assertLessEqual(rows, S.BUDGET)
            self.assertEqual(len(sysm), len(ms))

    def test_with_the_public_in_step_with_the_model_it_maximises_the_chance(self):
        """No value to be had anywhere: the best system is then simply the likeliest signs."""
        _, ms = write(self.TMP, self.even())
        d = S.pick(ms)["recommended"]
        self.assertEqual(d["p13"], max(x["p13"] for x in S.frontier(ms)))

    def test_a_double_goes_where_the_public_is_wrong_not_where_the_match_is_open(self):
        ms = self.even(13)
        ms[0] = ({"1": 40.0, "X": 27.0, "2": 33.0}, {"1": 40, "X": 27, "2": 33})     # open, public agrees
        ms[1] = ({"1": 52.0, "X": 23.0, "2": 25.0}, {"1": 75, "X": 13, "2": 12})     # public far too keen
        _, ms = write(self.TMP, ms)
        rec = S.pick(ms)["recommended"]
        self.assertIn(len(rec["signs"][1]), (2, 3), "the match the public has wrong is covered")
        self.assertGreater(rec["valueRatio"], S.describe(ms, S.best_for(ms, 20))["valueRatio"])

    def test_the_floors_keep_most_of_the_chance(self):
        ms = self.even(13)
        for i in (2, 4, 6, 8):
            ms[i] = ({"1": 50.0, "X": 25.0, "2": 25.0}, {"1": 80, "X": 10, "2": 10})
        _, ms = write(self.TMP, ms)
        p = S.pick(ms)
        self.assertGreaterEqual(p["recommended"]["p13"], p["floors"]["p13"])
        self.assertGreaterEqual(p["recommended"]["p10plus"], p["floors"]["p10plus"])
        self.assertLess(p["alternatives"]["maxValue"]["p13"], p["recommended"]["p13"])

    def test_the_chance_of_k_or_more_is_a_proper_distribution(self):
        _, ms = write(self.TMP, self.even(13))
        sysm = [("1",)] * 13
        d = S.correct_dist(ms, sysm)
        self.assertAlmostEqual(d[13], 0.45 ** 13, places=12)
        for a, b in zip((13, 12, 11), (12, 11, 10)):
            self.assertLess(d[a], d[b])


if __name__ == "__main__":
    unittest.main()


class Budget(unittest.TestCase):
    """24 rows, not 16 (owner, 28 Nov 2026): the eighth extra krona buys a full cover, and that is the first
    point at which covering a draw is worth anything at all."""
    TMP = "/tmp/_test_stryktips_budget.json"

    def tearDown(self):
        if os.path.exists(self.TMP):
            os.remove(self.TMP)

    def test_the_default_budget_is_24_and_the_flag_overrides_it(self):
        self.assertEqual(S.BUDGET, 24)
        ms = [({"1": 45.0, "X": 27.0, "2": 28.0}, {"1": 45, "X": 27, "2": 28}) for _ in range(13)]
        import json
        json.dump(coupon(ms), open(self.TMP, "w"))
        _, ms = S.load(self.TMP)
        rows = lambda sysm: __import__("math").prod(len(x) for x in sysm)
        self.assertLessEqual(rows(S.best_for(ms, 3)), 24)
        self.assertLessEqual(rows(S.best_for(ms, 3, budget=16)), 16)
        self.assertLessEqual(rows(S.best_for(ms, 3, budget=8)), 8)

    def test_a_full_cover_appears_once_the_budget_allows_it(self):
        """With 24 rows the best system for the chance of 13 covers one match completely - draw included."""
        ms = [({"1": 45.0, "X": 27.0, "2": 28.0}, {"1": 45, "X": 27, "2": 28}) for _ in range(13)]
        import json
        json.dump(coupon(ms), open(self.TMP, "w"))
        _, ms = S.load(self.TMP)
        at24 = S.best_for(ms, 20, budget=24)
        at16 = S.best_for(ms, 20, budget=16)
        self.assertTrue(any(len(x) == 3 for x in at24), "a full cover at 24 rows")
        self.assertFalse(any(len(x) == 3 for x in at16), "no full cover fits in 16 rows beside three doubles")
        self.assertTrue(any("X" in x for x in at24), "and that is how a draw gets played")
        d24, d16 = S.correct_dist(ms, at24), S.correct_dist(ms, at16)
        self.assertGreater(d24[13], d16[13])
        self.assertGreater(d24[10], d16[10])
