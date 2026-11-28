"""The odds feed that ends the price checking: budget first, then the parsing (28 Nov 2026)."""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "fetch"))
import common  # noqa: E402
import oddsapi as O  # noqa: E402

SPORTS = [{"key": "soccer_epl", "title": "EPL", "active": True},
          {"key": "soccer_efl_champ", "title": "Championship", "active": True},
          {"key": "icehockey_sweden_hockey_league", "title": "SHL - Sweden Hockey League", "active": True},
          {"key": "soccer_italy_serie_a", "title": "Serie A - Italy", "active": True}]

EVENT = {"id": "abc", "commence_time": "2026-11-28T20:00:00Z", "home_team": "Leeds United",
         "away_team": "Coventry City",
         "bookmakers": [
             {"key": "svenskaspel", "markets": [
                 {"key": "totals", "outcomes": [{"name": "Over", "point": 2.5, "price": 1.85},
                                                {"name": "Under", "point": 2.5, "price": 1.95},
                                                {"name": "Over", "point": 1.5, "price": 1.28}]},
                 {"key": "h2h", "outcomes": [{"name": "Leeds United", "price": 1.55},
                                             {"name": "Draw", "price": 4.10},
                                             {"name": "Coventry City", "price": 6.50}]}]},
             {"key": "betmgm", "markets": [
                 {"key": "totals", "outcomes": [{"name": "Over", "point": 2.5, "price": 1.80},
                                                {"name": "Under", "point": 2.5, "price": 2.00}]}]},
             {"key": "sketchy_book", "markets": [
                 {"key": "totals", "outcomes": [{"name": "Over", "point": 2.5, "price": 9.99},
                                                {"name": "Under", "point": 2.5, "price": 9.99}]}]}]}


class Parsing(unittest.TestCase):
    def test_only_the_books_he_can_play_and_only_complete_lines(self):
        p = O.parse_event(EVENT)
        self.assertEqual(sorted(p["books"]), ["betmgm", "svenskaspel"])          # the unknown book is dropped
        self.assertEqual(p["books"]["svenskaspel"]["totals"]["2.5"], {"over": 1.85, "under": 1.95})
        self.assertNotIn("1.5", p["books"]["svenskaspel"]["totals"])              # over without under: dropped
        self.assertEqual(p["books"]["svenskaspel"]["h2h"],
                         {"home": 1.55, "draw": 4.10, "away": 6.50})
        self.assertNotIn("h2h", p["books"]["betmgm"])
        self.assertEqual(p["home"], "Leeds United")

    def test_an_event_no_wanted_book_prices_is_dropped(self):
        ev = dict(EVENT, bookmakers=[{"key": "sketchy_book", "markets": []}])
        self.assertIsNone(O.parse_event(ev))

    def test_league_names_find_the_api_keys(self):
        self.assertEqual(O.match_key("England - Premier League", SPORTS), "soccer_epl")
        self.assertEqual(O.match_key("England - Championship", SPORTS), "soccer_efl_champ")
        self.assertEqual(O.match_key("Sweden - SHL", SPORTS), "icehockey_sweden_hockey_league")
        self.assertIsNone(O.match_key("Narnia - First Division", SPORTS))


class Budget(unittest.TestCase):
    """500 credits a month is the whole constraint: the robot must never spend the 451st."""

    def setUp(self):
        self.path = os.path.join(common.DATA, "oddsapi", "usage.json")
        self.saved = open(self.path).read() if os.path.exists(self.path) else None

    def tearDown(self):
        if self.saved is None:
            if os.path.exists(self.path):
                os.remove(self.path)
        else:
            open(self.path, "w").write(self.saved)

    def test_the_cap_holds_and_is_written_before_the_request(self):
        u = {}
        self.assertTrue(O.take_credits(u, "2026-11", 2))
        self.assertEqual(u["2026-11"], 2)
        self.assertEqual(json.load(open(self.path))["2026-11"], 2)          # on disk before any request
        u["2026-11"] = O.CAP - 1
        self.assertFalse(O.take_credits(u, "2026-11", 2), "a request that would pass the cap is refused")
        self.assertEqual(u["2026-11"], O.CAP - 1, "and costs nothing")
        self.assertTrue(O.take_credits(u, "2026-11", 1), "exactly the cap is allowed")
        self.assertEqual(u["2026-11"], O.CAP)
        self.assertFalse(O.take_credits(u, "2026-11", 1))
        self.assertTrue(O.take_credits(u, "2026-12", 1), "a new month starts fresh")

    def test_old_months_are_dropped_but_six_are_kept(self):
        u = {f"2026-{m:02d}": 1 for m in range(1, 12)}
        O.take_credits(u, "2026-12", 1)
        self.assertEqual(len(u), 6, "six months of history is kept")
        self.assertIn("2026-12", u)
        self.assertNotIn("2026-01", u)


class Aiming(unittest.TestCase):
    """Credits are spent only on matches the scan could actually post."""

    def board(self):
        soon = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        late = (datetime.now(timezone.utc) + timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"matches": [{"id": "m1", "league": "England - Premier League", "start": soon},
                            {"id": "m2", "league": "Italy - Serie A", "start": late},
                            {"id": "m3", "league": "England - Championship", "start": soon}],
                "products": [{"matchId": "m1", "line": {"anchored": "grid"}, "start": soon},
                             {"matchId": "m2", "line": {"anchored": True}, "start": late},
                             {"matchId": "m3", "line": {"prob": 80}, "start": soon}]}

    def test_only_market_priced_products_inside_the_window(self):
        now = datetime.now(timezone.utc)
        got = O.leagues_wanted(self.board(), now, hours=10)
        self.assertEqual(got, ["England - Premier League"])          # m2 too late, m3 priced by the model
        self.assertIn("Italy - Serie A", O.leagues_wanted(self.board(), now, hours=40))

    def test_a_run_without_a_key_writes_nothing_and_says_so(self):
        saved = os.environ.pop("ODDSAPI_KEY", None)
        try:
            log = common.Log()
            O.run(log)
            self.assertEqual(log.sources["oddsapi"]["status"], "failed")
            self.assertIn("ODDSAPI_KEY", log.sources["oddsapi"]["error"])
        finally:
            if saved is not None:
                os.environ["ODDSAPI_KEY"] = saved


if __name__ == "__main__":
    unittest.main()


class Attaching(unittest.TestCase):
    """autoprice puts those prices on the matches, in Jarvis' own line keys."""

    PATH = "oddsapi/odds.json"

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "fetch"))
        sys.path.insert(0, os.path.join(ROOT, "jarvis"))
        import autoprice
        import decision
        self.A, self.D = autoprice, decision
        self.full = os.path.join(common.DATA, self.PATH)
        self.saved = open(self.full).read() if os.path.exists(self.full) else None

    def tearDown(self):
        if self.saved is None:
            if os.path.exists(self.full):
                os.remove(self.full)
        else:
            open(self.full, "w").write(self.saved)

    def feed(self, at, home="Leeds United", away="Coventry City"):
        common.write_json(self.PATH, {"meta": {"fetchedAt": at}, "events": [
            {"home": home, "away": away, "start": "2026-11-28T20:00:00Z", "league": "England - Premier League",
             "books": {"svenskaspel": {"h2h": {"home": 1.55, "draw": 4.1, "away": 6.5},
                                       "totals": {"2.5": {"over": 1.85, "under": 1.95}}},
                       "betmgm": {"totals": {"2.5": {"over": 1.8, "under": 2.0}}}}}]})

    def match(self, hockey=False):
        m = {"id": "m1", "sport": "Football", "start": "2026-11-28T20:00:00Z",
             "inputs": {"home": {"name": "Leeds"}, "away": {"name": "Coventry"}},
             "auto": {"names": {"home": "Leeds United", "away": "Coventry City"}}}
        if hockey:
            m.update(sport="Ice hockey", h12={}, inputs={"home": {"name": "Leeds"}, "away": {"name": "Coventry"}})
        return m

    def now(self):
        return datetime(2026, 11, 28, 16, 0, tzinfo=timezone.utc)

    def test_the_prices_land_on_jarvis_line_keys(self):
        self.feed("2026-11-28T15:30:00Z")
        ms = [self.match()]
        self.assertEqual(self.A.attach_book_odds(ms, self.A.Repo(), self.now(), self.D), 1)
        bo = ms[0]["bookOdds"]
        self.assertEqual(bo[self.D.line_key("Match result", "Leeds")], {"svenskaspel": 1.55})
        self.assertEqual(bo[self.D.line_key("Match result", "Draw")], {"svenskaspel": 4.1})
        self.assertEqual(bo[self.D.line_key("Total goals", "Over 2.5")], {"svenskaspel": 1.85, "betmgm": 1.8})
        self.assertEqual(ms[0]["bookOddsAt"], "2026-11-28T15:30:00Z")

    def test_a_stale_feed_is_ignored(self):
        self.feed("2026-11-28T06:00:00Z")           # ten hours old
        ms = [self.match()]
        self.assertEqual(self.A.attach_book_odds(ms, self.A.Repo(), self.now(), self.D), 0)
        self.assertNotIn("bookOdds", ms[0])

    def test_hockey_totals_are_never_used_because_they_include_overtime(self):
        self.feed("2026-11-28T15:30:00Z")
        ms = [self.match(hockey=True)]
        self.A.attach_book_odds(ms, self.A.Repo(), self.now(), self.D)
        bo = ms[0]["bookOdds"]
        self.assertIn(self.D.line_key("Moneyline (incl. OT)", "Leeds"), bo)
        self.assertFalse([k for k in bo if k.startswith("Total_goals")])

    def test_an_unknown_fixture_is_left_alone(self):
        self.feed("2026-11-28T15:30:00Z", home="Real Madrid", away="Barcelona")
        ms = [self.match()]
        self.assertEqual(self.A.attach_book_odds(ms, self.A.Repo(), self.now(), self.D), 0)
