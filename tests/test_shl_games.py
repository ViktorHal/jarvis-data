import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "fetch"))
import shl_games as S  # noqa: E402


def page(gid):
    return open(os.path.join(HERE, "fixtures", f"shl_game_{gid}.html"), encoding="utf-8").read()


class Game(unittest.TestCase):
    def test_regulation_game(self):
        g = S.parse_game(page("1109941"), "1109941")
        self.assertEqual((g["home"], g["away"], g["date"], g["time"]), ("Frölunda HC", "Växjö Lakers HC", "2026-09-19", "15:15"))
        self.assertEqual((g["goals"], g["shots"], g["overtime"], g["shootout"]), ([5, 4], [25, 27], False, False))
        self.assertEqual(g["starters"][0], {"name": "Johansson, Lars", "saves": 23, "shotsAgainst": 27})
        self.assertEqual(g["starters"][1], {"name": "Åhman, Adam", "saves": 20, "shotsAgainst": 25})

    def test_shootout_is_dropped(self):
        g = S.parse_game(page("1109998"), "1109998")
        self.assertTrue(g["shootout"])
        self.assertEqual(g["goals"], [1, 1])                  # 2-1 after the shootout: 1-1 without it
        self.assertEqual(g["final"], [2, 1])
        self.assertEqual(g["shots"], [32, 14])                # 33 on the page includes the shootout attempt
        self.assertEqual(g["starters"][1], {"name": "Lassinantti, Joel", "saves": 31, "shotsAgainst": 32})
        self.assertEqual(g["starters"][1]["shotsAgainst"] - g["starters"][1]["saves"], g["goals"][0])

    def test_overtime_counts(self):
        g = S.parse_game(page("1109942"), "1109942")
        self.assertEqual((g["goals"], g["reg"], g["overtime"], g["shootout"]), ([4, 5], [4, 4], True, False))
        for i in (0, 1):
            st = g["starters"][i]
            self.assertEqual(st["shotsAgainst"], g["shots"][1 - i])
            self.assertEqual(st["shotsAgainst"] - st["saves"], g["goals"][1 - i])

    def test_schedule_ids_and_regular_season(self):
        sched = open(os.path.join(HERE, "fixtures", "shl_schedule.html"), encoding="utf-8").read()
        ids = S.game_ids(sched)
        self.assertEqual(ids[0], ("1109941", "2026-09-19"))
        opt = ('<option id="/ScheduleAndResults/Overview/17556" selected="selected" value="x">Play Out SHL'
               '<option id="/ScheduleAndResults/Overview/17557" value="x">SM-slutspel SHL'
               '<option id="/ScheduleAndResults/Overview/15977" value="x">SHL')
        self.assertEqual(S.resolve_regular(opt, "17556"), "15977")
        self.assertEqual(S.resolve_regular("", "10371"), "10371")


if __name__ == "__main__":
    unittest.main()
