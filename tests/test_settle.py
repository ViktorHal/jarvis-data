import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jarvis"))
import settle as S  # noqa: E402

FB = {"sport": "football", "id": "6112495", "start": "2026-11-26T17:45:00Z", "home": "Heart of Midlothian", "away": "Monaco",
      "score": [2, 3], "ended": "FT", "corners": [4, 6], "yellow": [2, 2], "red": [0, 0], "playersKnown": True,
      "players": [{"name": "Lawrence Shankland", "team": "home", "started": True, "shots": 3, "sot": 1},
                  {"name": "Folarin Balogun", "team": "away", "started": True, "shots": 2, "sot": 2}]}
OLY = {"sport": "football", "id": "6112266", "start": "2026-11-26T17:45:00Z", "home": "Olympiacos", "away": "Milan",
       "score": [2, 4], "ended": "FT", "corners": [6, 1], "yellow": [3, 2], "red": [0, 0], "playersKnown": True, "players": []}
NHL = {"sport": "nhl", "id": "1", "start": "2026-11-26T00:00:00Z", "home": "Bruins", "away": "Jets", "homeAbbrev": "BOS",
       "awayAbbrev": "WPG", "score": [2, 3], "ended": "OT", "reg": [2, 2]}
SHL = {"sport": "shl", "id": "x", "start": "2026-11-26T18:00:00Z", "home": "Timrå IK", "away": "Färjestad BK",
       "score": [2, 3], "ended": "SO", "reg": [2, 2]}
RES = [FB, OLY, NHL, SHL]


def bet(event, key, start="2026-11-26T17:45:00Z", **kw):
    return dict({"id": "b", "event": event, "key": key, "start": start, "sport": "Football"}, **kw)


class Settle(unittest.TestCase):
    def test_football_markets(self):
        self.assertEqual(S.settle_bet(bet("Hearts – Monaco", "Cards__Under_5_5"), RES)["outcome"], "hit")
        self.assertEqual(S.settle_bet(bet("Hearts – Monaco", "Total_goals__Over_2_5"), RES)["outcome"], "hit")
        self.assertEqual(S.settle_bet(bet("Hearts – Monaco", "Both_teams_to_score__Yes"), RES)["outcome"], "hit")
        self.assertEqual(S.settle_bet(bet("Hearts – Monaco", "Match_result__Hearts"), RES)["outcome"], "miss")
        self.assertEqual(S.settle_bet(bet("Hearts – Monaco", "Corners__Under_9_5"), RES)["outcome"], "miss")
        self.assertEqual(S.settle_bet(bet("Hearts – Monaco", "Team_goals__Monaco_over_1_5"), RES)["outcome"], "hit")
        self.assertEqual(S.settle_bet(bet("Hearts – Monaco", "Double_chance__Hearts_or_draw"), RES)["outcome"], "miss")
        self.assertEqual(S.settle_bet(bet("Olympiacos – AC Milan", "Cards__Over_3_5"), RES)["outcome"], "hit")

    def test_players(self):
        self.assertEqual(S.settle_bet(bet("Hearts – Monaco", "Player_shots__Lawrence_Shankland_3_"), RES)["outcome"], "hit")
        self.assertEqual(S.settle_bet(bet("Hearts – Monaco", "Player_shots_on_target__Lawrence_Shankland_2_"), RES)["outcome"], "miss")
        self.assertEqual(S.settle_bet(bet("Hearts – Monaco", "Player_shots__Abline_1_"), RES)["outcome"], "void")

    def test_hockey(self):
        b = bet("Boston Bruins – Winnipeg Jets", "Moneyline_incl_OT__Winnipeg_Jets", start="2026-11-26T00:00:00Z", sport="Ice hockey")
        self.assertEqual(S.settle_bet(b, RES)["outcome"], "hit")
        b = bet("Boston Bruins – Winnipeg Jets", "60_minute_result__Draw", start="2026-11-26T00:00:00Z", sport="Ice hockey")
        self.assertEqual(S.settle_bet(b, RES)["outcome"], "hit")
        b = bet("Timrå IK – Färjestad BK", "Moneyline_incl_OT__Timr_IK", start="2026-11-26T18:00:00Z", sport="Ice hockey")
        self.assertEqual(S.settle_bet(b, RES)["outcome"], "miss")
        b = bet("Timrå IK – Färjestad BK", "Total_goals_60_min__Under_4_5", start="2026-11-26T18:00:00Z", sport="Ice hockey")
        self.assertEqual(S.settle_bet(b, RES)["outcome"], "hit")

    def test_combo_and_unknown(self):
        b = {"id": "c", "event": "Jarvis Safe Combo", "start": "2026-11-26T17:45:00Z", "sport": "Mixed",
             "legs": [{"event": "Hearts – Monaco", "key": "Cards__Under_5_5", "start": "2026-11-26T17:45:00Z", "sport": "Football"},
                      {"event": "Olympiacos – Milan", "key": "Corners__Under_12_5", "start": "2026-11-26T17:45:00Z", "sport": "Football"}]}
        out = S.settle_bet(b, RES)
        self.assertEqual(out["outcome"], "hit")
        self.assertEqual(out["legs"], ["hit", "hit"])
        b["legs"].append({"event": "Lyon – Lillestrom", "key": "Cards__Under_5_5", "start": "2026-11-26T20:00:00Z"})
        self.assertIsNone(S.settle_bet(b, RES)["outcome"])
        self.assertIsNone(S.settle_bet(bet("Hearts – Monaco", "Half_with_most_goals__Tie"), RES)["outcome"])

    def test_extra_time_and_red_cards_left_for_hand(self):
        r = dict(FB, ended="AET")
        self.assertIsNone(S.settle_bet(bet("Hearts – Monaco", "Total_goals__Over_2_5"), [r])["outcome"])
        r = dict(FB, red=[1, 0], yellow=[2, 2])
        self.assertIsNone(S.settle_bet(bet("Hearts – Monaco", "Cards__Under_5_5"), [r])["outcome"])

    def test_grade_match(self):
        m = {"id": "m", "event": "Hearts – Monaco", "start": "2026-11-26T17:45:00Z", "sport": "Football",
             "markets": [{"key": "Total_goals__Over_2_5", "selection": "Over 2.5"}, {"key": "Corners__Over_9_5", "selection": "Over 9.5"},
                         {"key": "Half_with_most_goals__Tie", "selection": "Tie"}]}
        g = S.grade_match(m, RES)
        self.assertEqual(g["graded"], {"Total_goals__Over_2_5": 1, "Corners__Over_9_5": 1})
        self.assertEqual(g["notSettledByCode"], 1)
        self.assertIn("2-3", g["result"])


if __name__ == "__main__":
    unittest.main()
