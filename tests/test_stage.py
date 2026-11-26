import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jarvis"))
import stage as S  # noqa: E402

F = lambda s: S.final_call(s).strftime("%Y-%m-%dT%H:%M")


class Stage(unittest.TestCase):
    def test_saturday_slate(self):           # Stockholm = UTC+1 in winter
        self.assertEqual(F("2026-11-28T12:30:00Z"), "2026-11-28T10:44")   # 13:30 -> 11:44
        self.assertEqual(F("2026-11-28T15:00:00Z"), "2026-11-28T12:44")   # 16:00 -> 13:44
        self.assertEqual(F("2026-11-28T17:30:00Z"), "2026-11-28T16:44")   # 18:30 -> 17:44
        self.assertEqual(F("2026-11-28T20:00:00Z"), "2026-11-28T19:10")   # 21:00 -> 20:10
        self.assertEqual(F("2026-11-28T19:30:00Z"), "2026-11-28T16:44")   # 20:30 -> 17:44 (20:10 is too close)

    def test_night_and_early(self):
        self.assertEqual(F("2026-11-29T00:00:00Z"), "2026-11-28T19:10")   # NHL 01:00 -> 20:10 the evening before
        self.assertEqual(F("2026-11-28T07:00:00Z"), "2026-11-27T19:10")   # 08:00 -> 20:10 the evening before

    def test_stage(self):
        self.assertEqual(S.stage_for("2026-11-28T20:00:00Z", "2026-11-28T06:45:00Z")[0], "preliminary")
        self.assertEqual(S.stage_for("2026-11-28T20:00:00Z", "2026-11-28T19:12:00Z")[0], "final")


if __name__ == "__main__":
    unittest.main()
