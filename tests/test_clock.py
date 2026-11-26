import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fetch"))
import clock  # noqa: E402

T = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class Clock(unittest.TestCase):
    def run_tick(self, now, last):
        sent = []
        last = dict({"results.yml": T(now)}, **last)      # results ran just now unless a test says otherwise
        clock.tick(T(now), last=lambda wf: last.get(wf), dispatch=sent.append)
        return sent

    def test_missed_results_slot(self):
        self.assertEqual(self.run_tick("2026-11-26T22:20", {"odds.yml": T("2026-11-26T21:36"), "fetch.yml": T("2026-11-26T16:21"),
                                                            "results.yml": T("2026-11-26T21:06")}), ["results.yml"])
        self.assertEqual(self.run_tick("2026-11-26T22:12", {"odds.yml": T("2026-11-26T21:36"), "fetch.yml": T("2026-11-26T16:21"),
                                                            "results.yml": T("2026-11-26T21:06")}), [])

    def test_missed_odds_slot_is_started_once(self):
        self.assertEqual(self.run_tick("2026-11-26T13:46", {"odds.yml": T("2026-11-26T12:36"), "fetch.yml": T("2026-11-26T10:21")}), ["odds.yml"])
        self.assertEqual(self.run_tick("2026-11-26T13:48", {"odds.yml": T("2026-11-26T13:46"), "fetch.yml": T("2026-11-26T10:21")}), [])

    def test_waits_for_githubs_own_schedule_first(self):
        self.assertEqual(self.run_tick("2026-11-26T13:40", {"odds.yml": T("2026-11-26T12:36"), "fetch.yml": T("2026-11-26T10:21")}), [])

    def test_missed_fetch_slot(self):
        self.assertEqual(self.run_tick("2026-11-26T16:31", {"odds.yml": T("2026-11-26T15:36"), "fetch.yml": T("2026-11-26T12:57")}), ["fetch.yml"])
        # morning slot across midnight bookkeeping: at 02:00 the last slot is yesterday 16:20, already run
        self.assertEqual(self.run_tick("2026-11-27T02:00", {"odds.yml": None, "fetch.yml": T("2026-11-26T16:21")}), [])

    def test_no_odds_at_night_and_no_late_catch_up(self):
        self.assertEqual(self.run_tick("2026-11-27T03:00", {"odds.yml": T("2026-11-26T22:36"), "fetch.yml": T("2026-11-26T16:21")}), [])
        # a fetch slot missed by more than 3 hours is left for the next slot
        self.assertEqual(self.run_tick("2026-11-26T14:00", {"odds.yml": T("2026-11-26T13:36"), "fetch.yml": T("2026-11-26T09:00")}), [])


if __name__ == "__main__":
    unittest.main()
