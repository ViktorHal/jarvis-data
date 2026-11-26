"""Offline tests: every fetcher runs against saved sample responses (no network)."""
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, timezone

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


class TestSHL(Base):
    PAGE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "shl_schedule.html"), encoding="utf-8").read()

    def test_parse_real_page_rows(self):
        import shl
        g = shl.parse_schedule(self.PAGE)
        self.assertEqual(len(g), 10)
        hv = next(x for x in g if x["home"] == "HV 71")                  # team names with digits
        self.assertEqual((hv["hg"], hv["ag"], hv["reg_hg"], hv["reg_ag"], hv["overtime"], hv["shootout"]), (4, 5, 4, 4, True, False))
        so = next(x for x in g if x["home"] == "Frölunda HC" and x["away"] == "Färjestad BK")
        self.assertEqual((so["hg"], so["ag"], so["reg_hg"], so["reg_ag"], so["shootout"]), (3, 2, 2, 2, True))
        lr = next(x for x in g if x["away"] == "Rögle BK")
        self.assertEqual((lr["date"], lr["startUTC"], lr["hg"], lr["ag"]), ("2026-09-19", "2026-09-19T16:00:00Z", 4, 2))
        fut = [x for x in g if not x["played"]]
        self.assertEqual(len(fut), 2)
        self.assertEqual(fut[0]["startUTC"], "2026-11-26T18:00:00Z")     # 19:00 Stockholm in winter

    def test_team_table_excludes_shootout_goal(self):
        import shl
        t = shl.team_table(shl.parse_schedule(self.PAGE), date(2026, 9, 23))
        fr = t["Frölunda HC"]
        self.assertEqual(fr["gp"], 2)
        self.assertEqual(fr["gf_pg"], 3.5)        # 5 + 2 (shootout goal excluded) = 7 / 2
        self.assertEqual(fr["reg_gf_pg"], 3.5)    # 5 + 2
        self.assertTrue(fr["played_yesterday"])   # played 22 Sep
        self.assertEqual(t["Rögle BK"]["ga_pg"], 4.0)

    def test_run_rejects_a_page_without_games(self):
        import shl
        common.get = Fake({"stats.swehockey.se": "<html>maintenance</html>"})
        log = common.Log()
        shl.run(log, today=date(2026, 11, 26))
        self.assertEqual(log.sources["shl schedule"]["status"], "failed")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "shl/teams.json")))


class TestXG(Base):
    PAGE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "statz_xg.html"), encoding="utf-8").read()

    def test_parse_four_tables(self):
        import xg
        season, upd, sp = xg.parse(self.PAGE)
        self.assertEqual(season, "2026/2027")
        self.assertEqual(set(sp), {"all", "home", "away", "last5"})
        self.assertTrue(upd.startswith("Updated 23 November 2026"))
        row = next(iter(sp["all"].values()))
        self.assertAlmostEqual(row["xg_pg"], round(row["xg"] / row["played"], 3))

    def test_wrong_season_is_rejected(self):
        import xg
        common.get = Fake({"statz.ai": self.PAGE.replace("2026/2027", "2025/2026")})
        log = common.Log()
        xg.time.sleep = lambda s: None
        xg.run(log)
        self.assertTrue(all(s["status"] == "failed" for s in log.sources.values()))
        self.assertIn("not 2026/27", log.sources["xg premier-league"]["error"])


class TestPlayersAndGoalies(Base):
    FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

    def test_player_rows(self):
        import players
        season, rows = players.parse(open(os.path.join(self.FIX, "ps_sot.html"), encoding="utf-8").read())
        self.assertEqual(season, "2026/2027")
        self.assertEqual(rows[0], ("154421", "Erling Haaland", "Manchester City", 980, 19.0, 1.74))
        self.assertTrue(all(r[2] for r in rows))                      # every player has a club
        self.assertEqual(rows[1][2], "Everton")                       # club without abbreviation

    def test_players_merge_and_season_check(self):
        import players
        players.time.sleep = lambda s: None
        page = open(os.path.join(self.FIX, "ps_sot.html"), encoding="utf-8").read()
        common.get = Fake({"premier-league/stats/shots-on-target": page})
        log = common.Log()
        players.run(log)
        p = self.read("players/premier-league.json")["players"]["Erling Haaland"]
        self.assertEqual((p["sot"], p["sot_p90"], p["minutes"]), (19.0, 1.74, 980))
        self.assertNotIn("shots_p90", p)                              # total-shots page failed: not guessed
        self.assertEqual(log.sources["players premier-league total-shots"]["status"], "failed")
        common.get = Fake({"premier-league/stats/shots-on-target": page.replace("2026/2027", "2025/2026")})
        log = common.Log()
        players.run(log)
        self.assertIn("not 2026/2027", log.sources["players premier-league shots-on-target"]["error"])

    def test_shl_goalies(self):
        import shl
        upd, g = shl.parse_goalies(open(os.path.join(self.FIX, "shl_goalies.html"), encoding="utf-8").read())
        self.assertEqual(len(g), 12)
        self.assertEqual(g["Thelin, Melker"]["team"], "FBK")
        self.assertEqual(g["Thelin, Melker"]["sv_pct"], 0.951)
        self.assertEqual(g["Thelin, Melker"]["gpi"], 9.0)


class TestPinnacle(Base):
    SOCCER = {"sport_id": 1, "last": 123, "events": [
        {"event_id": 1, "league_id": 1980, "league_name": "England - Premier League", "home": "Tottenham Hotspur", "away": "Fulham",
         "starts": "2026-11-28T20:00:00Z", "periods": {"num_0": {"money_line": {"home": 1.7, "draw": 4.1, "away": 4.9},
                                                                "totals": {"2.5": {"points": 2.5, "over": 1.85, "under": 2.02}}}}},
        {"event_id": 2, "league_name": "Brazil - Serie B", "home": "A", "away": "B", "periods": {"num_0": {}}},
        {"event_id": 9, "league_id": 1980, "league_name": "England - Premier League", "home": "Tottenham Hotspur", "away": "Fulham",
         "periods": {"num_0": {"money_line": {"home": 1.5, "draw": None, "away": 2.6}, "totals": {}}}}]}
    HOCKEY = {"sport_id": 4, "events": [{"event_id": 3, "league_name": "NHL", "home": "Ottawa Senators", "away": "Boston Bruins",
                                         "periods": {"num_0": {"money_line": {"home": 1.9, "away": 1.95}}}}]}

    def setUp(self):
        super().setUp()
        import pinnacle
        self.p = pinnacle
        self.calls = []

    def fake(self, url, **kw):
        self.calls.append((url, kw.get("headers")))
        return json.dumps(self.SOCCER if "sport_id=1" in url else self.HOCKEY).encode()

    def test_fetch_filters_scope_and_sends_key_in_header(self):
        common.get = self.fake
        self.p.get = self.fake
        log = common.Log()
        self.p.run(log, key="SECRET123", now=datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc))
        odds = self.read("pinnacle/odds.json")
        self.assertEqual([e["event_id"] for e in odds["events"]], [1, 3])           # Brazil Serie B dropped
        self.assertEqual(odds["events"][0]["periods"]["num_0"]["money_line"]["draw"], 4.1)
        self.assertTrue(all(h == {"x-portal-apikey": "SECRET123"} for _, h in self.calls))
        self.assertTrue(all("SECRET123" not in u for u, _ in self.calls))           # never in the URL
        self.assertNotIn("SECRET123", json.dumps(odds))
        self.assertEqual(self.read("pinnacle/usage.json"), {"2026-11-26": 2})

    def test_hard_cap_never_exceeded(self):
        common.get = self.fake
        self.p.get = self.fake
        day = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
        for _ in range(60):                       # 60 runs x 2 requests would be 120
            self.p.run(common.Log(), key="K", now=day)
        self.assertEqual(len(self.calls), self.p.CAP)
        self.assertEqual(self.read("pinnacle/usage.json")["2026-11-26"], self.p.CAP)
        self.p.run(common.Log(), key="K", now=datetime(2026, 11, 27, 1, 0, tzinfo=timezone.utc))
        self.assertEqual(len(self.calls), self.p.CAP + 2)                          # new UTC day, new budget

    def test_failed_request_still_counts_and_key_is_masked(self):
        def boom(url, **kw):
            raise urllib.error.URLError("bad key SECRET123")
        common.get = boom
        self.p.get = boom
        log = common.Log()
        self.p.run(log, key="SECRET123", now=datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(self.read("pinnacle/usage.json")["2026-11-26"], 2)
        self.assertNotIn("SECRET123", json.dumps(log.sources))

    def test_no_key_no_request(self):
        common.get = self.fake
        self.p.get = self.fake
        log = common.Log()
        self.p.run(log, key="")
        self.assertEqual(self.calls, [])
        self.assertEqual(log.sources["pinnacle"]["status"], "failed")
