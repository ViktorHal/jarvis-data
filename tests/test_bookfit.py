import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jarvis"))
import bookfit as B  # noqa: E402
import decision as D  # noqa: E402


def pick(group, sel, mo, **kw):
    return dict({"source": "jarvis", "needsPrice": True, "marketType": group, "pick": sel, "minOdds": mo}, **kw)


class BookFit(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(B.kind_of("Player shots on target", "Füllkrug 2+"), "Player shots on target 2+")
        self.assertEqual(B.kind_of("Corners", "Under 11.5"), "Corners Under")
        self.assertEqual(B.kind_of("Team goals", "Lille over 1.5"), "Team goals Over")

    def test_stats_and_verdict(self):
        bets = [pick("Cards", "Under 5.5", 1.45, mgmOdds=1.30) for _ in range(9)] + [pick("Cards", "Under 5.5", 1.45, b365Odds=1.50)]
        bets += [pick("Corners", "Over 8.5", 1.40, notOffered=["BetMGM"]) for _ in range(3)]
        st = B.stats(bets)
        self.assertEqual(st["Cards Under"]["n"], 10)
        self.assertEqual(st["Cards Under"]["playable"], 1)
        self.assertEqual(B.verdict("Cards Under", st), "lower")      # 10% playable: not below 10%
        self.assertEqual(B.verdict("Corners Over", st), "show")       # too few observations
        bets += [pick("Cards", "Under 5.5", 1.45, svsOdds=1.2) for _ in range(5)]
        self.assertEqual(B.verdict("Cards Under", B.stats(bets)), "hide")

    def test_filter_board(self):
        bets = [pick("Cards", "Under 5.5", 1.45, mgmOdds=1.30) for _ in range(12)]
        board = {"products": [{"rank": 1, "kind": "check", "line": {"group": "Cards", "selection": "Under 4.5", "odds": None}},
                              {"rank": 2, "line": {"group": "Total goals", "selection": "Over 1.5", "odds": 1.4}}]}
        out = B.filter_board(board, bets)
        self.assertEqual([p["rank"] for p in out["products"]], [2])
        self.assertEqual(out["hidden"][0]["kinds"], ["Cards Under"])

    def test_no_all_shots_1plus(self):
        self.assertFalse(D._offerable({"group": "Player shots", "selection": "Abline 1+"}))
        self.assertTrue(D._offerable({"group": "Player shots", "selection": "Abline 2+"}))
        self.assertTrue(D._offerable({"group": "Player shots on target", "selection": "Abline 1+"}))


if __name__ == "__main__":
    unittest.main()
