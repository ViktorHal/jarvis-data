"""Offline tests: every fetcher runs against saved sample responses (no network)."""
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fetch"))
import common  # noqa: E402
import elo  # noqa: E402
import football  # noqa: E402
import nhl  # noqa: E402

HDR = "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,Referee,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR,B365H,B365D,B365A,PSH,PSD,PSA"
E0 = "\n".join([HDR,
    "E0,15/08/2026,20:00,Spurs,Arsenal,2,1,H,1,0,H,M Oliver,14,10,6,3,10,12,7,4,2,3,0,0,3.1,3.5,2.2,3.2,3.6,2.25",
    "E0,22/08/2026,15:00,Arsenal,Chelsea,1,1,D,0,1,A,A Taylor,18,8,5,2,9,11,9,2,1,2,0,1,1.8,3.8,4.5,1.85,3.9,4.6",
    "E0,29/08/2026,15:00,Chelsea,Spurs,0,3,A,0,1,A,M Oliver,9,15,2,8,14,8,,,4,1,1,0,2.4,3.4,2.9,2.45,3.5,2.95",
    "E0,05/09/2026,15:00,Spurs,Chelsea,,,,,,,,,,,,,,,,,,,,2.0,3.5,3.6,2.05,3.6,3.7",
])
FIX = HDR + "\nE0,28/11/2026,20:00,Spurs,Fulham,,,,,,,S Hooper,,,,,,,,,,,,,1.7,4.0,4.6,1.72,4.1,4.8"
STAND = {"standings": [
    {"teamAbbrev": {"default": "OTT"}, "teamName": {"default": "Ottawa Senators"}, "gamesPlayed": 25, "goalFor": 81, "goalAgainst": 71,
     "l10GamesPlayed": 10, "l10GoalsFor": 32, "l10GoalsAgainst": 26, "homeGamesPlayed": 13, "homeGoalsFor": 45, "homeGoalsAgainst": 33,
     "roadGamesPlayed": 12, "roadGoalsFor": 36, "roadGoalsAgainst": 38},
    {"teamAbbrev": {"default": "FLA"}, "teamName": {"default": "Florida Panthers"}, "gamesPlayed": 24, "goalFor": 76, "goalAgainst": 71,
     "l10GamesPlayed": 10, "l10GoalsFor": 32, "l10GoalsAgainst": 32}]}
SUMMARY = {"data": [{"teamFullName": "Ottawa Senators", "shotsForPerGame": 31.2, "shotsAgainstPerGame": 27.9, "powerPlayPct": 0.24, "penaltyKillPct": 0.81}]}
SCORE_Y = {"games": [{"homeTeam": {"abbrev": "FLA"}, "awayTeam": {"abbrev": "OTT"}, "startTimeUTC": "2026-11-26T00:00:00Z", "gameState": "OFF"}]}
GOALIES = {"data": [{"goalieFullName": "Linus Ullmark", "teamAbbrevs": "OTT", "gamesPlayed": 14, "gamesStarted": 14, "savePct": 0.941, "goalsAgainstAverage": 1.43, "shotsAgainst": 400}]}
SKATERS = {"total": 2, "data": [{"skaterFullName": "Tim Stützle", "teamAbbrevs": "OTT", "positionCode": "C", "gamesPlayed": 25, "shots": 75, "pointsPerGame": 1.1, "timeOnIcePerGame": 1200},
                    {"skaterFullName": "Callup", "teamAbbrevs": "OTT", "positionCode": "D", "gamesPlayed": 2, "shots": 3}]}
ELO = "Rank,Club,Country,Level,Elo,From,To\n1,Arsenal,ENG,1,2050.3,2026-11-20,2026-11-30\n2,Spurs,ENG,1,1850.7,2026-11-20,2026-11-30\n"


class Fake:
    def __init__(self, routes, fail=()):
        self.routes, self.fail, self.calls = routes, fail, []

    def __call__(self, url, **kw):
        self.calls.append(url)
        if any(f in url for f in self.fail):
            raise urllib.error.URLError("simulated outage")
        for key, body in self.routes.items():
            if key in url:
                return (body if isinstance(body, str) else json.dumps(body)).encode("utf-8")
        raise urllib.error.HTTPError(url, 404, "not found", None, None)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._data, self._get = common.DATA, common.get
        common.DATA = self.tmp.name

    def tearDown(self):
        common.DATA, common.get = self._data, self._get
        self.tmp.cleanup()

    def read(self, rel):
        with open(os.path.join(self.tmp.name, rel), encoding="utf-8") as f:
            return json.load(f)


class TestFootball(Base):
    def test_team_and_referee_tables(self):
        common.get = Fake({"/E0.csv": E0, "fixtures.csv": FIX})
        log = common.Log()
        football.run(log)
        t = self.read("football/teams_E0.json")
        self.assertEqual(t["meta"]["matches"], 3)                 # unplayed match skipped
        spurs = t["teams"]["Spurs"]
        self.assertEqual(spurs["all"]["games"], 2)
        self.assertEqual(spurs["all"]["goals_for_pg"], 2.5)
        self.assertEqual(spurs["all"]["sot_for_pg"], 7.0)
        # corners missing in Chelsea-Spurs: counted over the one match that has them, never guessed
        self.assertEqual(spurs["all"]["corners_games"], 1)
        self.assertEqual(spurs["all"]["corners_for_pg"], 7.0)
        self.assertEqual(spurs["home"]["games"], 1)
        self.assertEqual(spurs["away"]["shots_for_pg"], 15.0)
        self.assertEqual(spurs["lastMatch"], "2026-08-29")
        r = self.read("football/referees_E0.json")["referees"]
        self.assertEqual(r["M Oliver"], {"games": 2, "yellow_pg": 5.0, "red_rate": 0.5, "fouls_pg": 22.0})
        fx = self.read("football/fixtures.json")["fixtures"]
        self.assertEqual(fx[0]["home"], "Spurs")
        self.assertEqual(fx[0]["prices"]["PSH"], 1.72)
        self.assertEqual(fx[0]["referee"], "S Hooper")
        self.assertEqual(log.sources["football-data E0"]["status"], "ok")
        self.assertEqual(log.sources["football-data SP1"]["status"], "failed")   # 404 in the fake

    def test_failing_league_does_not_stop_others_or_delete_old_files(self):
        common.get = Fake({"/E0.csv": E0, "fixtures.csv": FIX})
        football.run(common.Log())
        before = self.read("football/teams_E0.json")
        common.get = Fake({"fixtures.csv": FIX}, fail=("/E0.csv",))
        log = common.Log()
        football.run(log)
        self.assertEqual(log.sources["football-data E0"]["status"], "failed")
        self.assertEqual(log.sources["football-data fixtures"]["status"], "ok")
        self.assertEqual(self.read("football/teams_E0.json"), before)            # last good file kept


class TestNHL(Base):
    def test_tables(self):
        common.get = Fake({"standings/now": STAND, "team/summary": SUMMARY, "score/2026-11-25": SCORE_Y,
                           "goalie/summary": GOALIES, "skater/summary": SKATERS, "score/2026-11-26": {"games": []},
                           "score/2026-11-27": {"games": []}})
        log = common.Log()
        nhl.run(log, today=date(2026, 11, 26))
        t = self.read("nhl/teams.json")["teams"]
        self.assertEqual(t["OTT"]["gf_pg"], 3.24)
        self.assertEqual(t["OTT"]["l10_ga_pg"], 2.6)
        self.assertEqual(t["OTT"]["shots_for_pg"], 31.2)
        self.assertTrue(t["OTT"]["played_yesterday"])
        self.assertTrue(t["FLA"]["played_yesterday"])
        self.assertNotIn("shots_for_pg", t["FLA"])                               # unmatched: left out, not invented
        self.assertEqual(self.read("nhl/goalies.json")["goalies"]["Linus Ullmark"]["sv_pct"], 0.941)
        sk = self.read("nhl/skaters.json")["skaters"]
        self.assertEqual(sk["Tim Stützle"]["shots_pg"], 3.0)
        self.assertNotIn("Callup", sk)
        self.assertTrue(any("start=0" in c for c in common.get.calls if "skater" in c))
        self.assertTrue(all(s["status"] == "ok" for s in log.sources.values()))


class TestElo(Base):
    def test_snapshot(self):
        common.get = Fake({"clubelo.com/2026-11-26": ELO})
        log = common.Log()
        elo.run(log, today=date(2026, 11, 26))
        js = self.read("elo/latest.json")
        self.assertEqual(js["clubs"]["Spurs"]["elo"], 1850.7)
        self.assertEqual(js["meta"]["snapshotDate"], "2026-11-26")


class TestRunAll(Base):
    def test_manifest_with_total_outage(self):
        import run_all
        common.get = Fake({}, fail=("http",))
        run_all.main()
        m = self.read("manifest.json")
        self.assertEqual(m["ok"], 0)
        self.assertTrue(m["failed"] > 10)


if __name__ == "__main__":
    unittest.main()
