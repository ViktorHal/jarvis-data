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
        st = B.stats(bets)
        self.assertEqual(st["Cards Under"]["n"], 10)
        self.assertEqual(st["Cards Under"]["playable"], 1)
        self.assertEqual(B.verdict("Cards Under", st), "lower")      # 10% playable: not below 10%
        bets += [pick("Cards", "Under 5.5", 1.45, svsOdds=1.2) for _ in range(5)]
        self.assertEqual(B.verdict("Cards Under", B.stats(bets)), "hide")

    def test_four_unplayable_observations_are_enough(self):
        """28 Nov 2026: on 27 Nov all ten observations were unplayable at 84-91% of the minimum, and eight-per-kind
        meant the same picks filled the board again the next day."""
        shots = [pick("Player shots", "Hall 2+", 1.28, mgmOdds=1.10 + i / 100) for i in range(4)]
        self.assertEqual(B.verdict("Player shots 2+", B.stats(shots)), "hide")
        self.assertEqual(B.verdict("Player shots 2+", B.stats(shots[:3])), "show")   # three is not yet evidence
        # a kind that merely keeps just missing is lowered, not hidden
        near = [pick("Corners", "Under 10.5", 1.40, mgmOdds=1.38) for _ in range(6)]
        self.assertEqual(B.verdict("Corners Under", B.stats(near)), "lower")
        # and a line his bookmakers do not carry at all goes at once
        gone = [pick("Corners", "Over 8.5", 1.40, notOffered=["BetMGM", "Bet365"]) for _ in range(2)]
        self.assertEqual(B.verdict("Corners Over", B.stats(gone)), "hide")
        self.assertEqual(B.verdict("Corners Over", B.stats(gone[:1])), "show")
        # a kind the bookmakers do pay is untouched
        good = [pick("Total goals", "Over 1.5", 1.25, mgmOdds=1.28) for _ in range(5)]
        self.assertEqual(B.verdict("Total goals Over", B.stats(good)), "show")

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
