"""The odds feed that ends the price checking: budget first, then the parsing (28 Nov 2026)."""
import json
import inspect
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
             {"key": "svenskaspel_se", "markets": [
                 {"key": "totals", "outcomes": [{"name": "Over", "point": 2.5, "price": 1.85},
                                                {"name": "Under", "point": 2.5, "price": 1.95},
                                                {"name": "Over", "point": 1.5, "price": 1.28}]},
                 {"key": "h2h", "outcomes": [{"name": "Leeds United", "price": 1.55},
                                             {"name": "Draw", "price": 4.10},
                                             {"name": "Coventry City", "price": 6.50}]}]},
             {"key": "betmgm_se", "markets": [
                 {"key": "totals", "outcomes": [{"name": "Over", "point": 2.5, "price": 1.80},
                                                {"name": "Under", "point": 2.5, "price": 2.00}]}]},
             {"key": "betsson", "markets": [          # a Swedish book, but not one he has an account at
                 {"key": "totals", "outcomes": [{"name": "Over", "point": 2.5, "price": 2.10},
                                                {"name": "Under", "point": 2.5, "price": 1.75}]}]},
             {"key": "sketchy_book", "markets": [
                 {"key": "totals", "outcomes": [{"name": "Over", "point": 2.5, "price": 9.99},
                                                {"name": "Under", "point": 2.5, "price": 9.99}]}]}]}


class Parsing(unittest.TestCase):
    def test_only_the_books_he_can_play_and_only_complete_lines(self):
        p = O.parse_event(EVENT)
        self.assertEqual(sorted(p["books"]), ["betmgm_se", "betsson", "svenskaspel_se"])   # unknown book dropped
        self.assertEqual(p["books"]["svenskaspel_se"]["totals"]["2.5"], {"over": 1.85, "under": 1.95})
        self.assertNotIn("1.5", p["books"]["svenskaspel_se"]["totals"])           # over without under: dropped
        self.assertEqual(p["books"]["svenskaspel_se"]["h2h"],
                         {"home": 1.55, "draw": 4.10, "away": 6.50})
        self.assertNotIn("h2h", p["books"]["betmgm_se"])
        self.assertEqual(p["home"], "Leeds United")

    def test_the_two_books_he_plays_at_carry_the_region_suffix(self):
        # The first live run returned only betsson and nordicbet because the filter asked for "betmgm" and
        # "svenskaspel". The region names them betmgm_se and svenskaspel_se, and both must be in WANT_BOOKS.
        for b in ("betmgm_se", "svenskaspel_se"):
            self.assertIn(b, O.WANT_BOOKS)
            self.assertIn(b, O.HIS_BOOKS)
        self.assertNotIn("betmgm", O.WANT_BOOKS)
        self.assertNotIn("svenskaspel", O.WANT_BOOKS)
        for b in O.HIS_BOOKS:               # a book he cannot bet at must never decide a price
            self.assertIn(b, O.WANT_BOOKS)

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

    def test_market_priced_products_first_then_the_rest_of_the_window(self):
        now = datetime.now(timezone.utc)
        got = O.leagues_wanted(self.board(), now, hours=10)
        # The Premier League has a market-priced product, so it is asked for first. The Championship match
        # has only a model-priced product, but a league with no price at all is a league Jarvis cannot find
        # a bet in, so it fills the day's remaining allowance. Serie A starts outside the window.
        self.assertEqual(got, ["England - Premier League", "England - Championship"])
        self.assertIn("Italy - Serie A", O.leagues_wanted(self.board(), now, hours=40))

    def test_the_fill_is_ordered_by_measured_edge_not_by_match_count(self):
        soon = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        board = {"products": [], "matches": [
            {"id": "a", "league": "Spain - La Liga", "start": soon},        # 7.6, three matches
            {"id": "b", "league": "Spain - La Liga", "start": soon},
            {"id": "c", "league": "Spain - La Liga", "start": soon},
            {"id": "d", "league": "Greece - Super League", "start": soon},  # 12.1, one match
            {"id": "e", "league": "Mars - Division 2", "start": soon}]}     # unmeasured, one match
        got = O.leagues_wanted(board, datetime.now(timezone.utc), hours=10)
        self.assertEqual(got[:2], ["Greece - Super League", "Spain - La Liga"])
        self.assertEqual(got[-1], "Mars - Division 2")      # an unlisted league sits below every measured one


class DayAllowance(unittest.TestCase):
    """A day's allowance, with what an empty hour does not spend rolling forward.

    Not named Budget: a second class of that name would shadow the cap tests above and they would stop
    running without a word, which is how a cap quietly stops being a cap.
    """

    def setUp(self):
        self.path = os.path.join(common.DATA, "oddsapi", "usage.json")
        self.saved = open(self.path).read() if os.path.exists(self.path) else None

    def tearDown(self):
        if self.saved is None:
            if os.path.exists(self.path):
                os.remove(self.path)
        else:
            open(self.path, "w").write(self.saved)

    def test_the_three_hours_share_fourteen_credits_a_day(self):
        u, spent = {}, 0
        for h in sorted(O.DAY_BUDGET):
            n = datetime(2026, 11, 29, h, tzinfo=timezone.utc)
            a = O.allowance(u, n)
            self.assertGreater(a, 0)
            for _ in range(a):
                self.assertTrue(O.take_credits(u, "2026-11", 1, "2026-11-29"))
            spent += a
        self.assertEqual(spent, O.DAY)
        self.assertEqual(u["2026-11-29"], O.DAY)
        self.assertLessEqual(O.DAY * 31, O.CAP)             # a full month stays inside the cap

    def test_an_unused_hour_rolls_forward_rather_than_being_lost(self):
        u = {}
        first = sorted(O.DAY_BUDGET)[0]
        self.assertEqual(O.allowance(u, datetime(2026, 11, 29, first, tzinfo=timezone.utc)), O.DAY_BUDGET[first])
        last = sorted(O.DAY_BUDGET)[-1]                     # nothing spent all day
        self.assertEqual(O.allowance(u, datetime(2026, 11, 29, last, tzinfo=timezone.utc)), O.DAY)

    def test_the_monthly_cap_still_wins(self):
        u = {"2026-11": O.CAP - 2}
        self.assertEqual(O.allowance(u, datetime(2026, 11, 29, 5, tzinfo=timezone.utc)), 2)
        u = {"2026-11": O.CAP}
        self.assertEqual(O.allowance(u, datetime(2026, 11, 29, 5, tzinfo=timezone.utc)), 0)

    def test_a_day_counter_is_kept_for_six_weeks_then_dropped(self):
        u = {f"2026-10-{d:02d}": 1 for d in range(1, 29)} | {f"2026-11-{d:02d}": 1 for d in range(1, 29)}
        O.take_credits(u, "2026-11", 1, "2026-11-29")
        days = [k for k in u if len(k) == 10]
        self.assertEqual(len(days), 45)
        self.assertIn("2026-11-29", u)
        self.assertNotIn("2026-10-01", u)

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
             "books": {"svenskaspel_se": {"h2h": {"home": 1.55, "draw": 4.1, "away": 6.5},
                                          "totals": {"2.5": {"over": 1.85, "under": 1.95}}},
                       "betmgm_se": {"totals": {"2.5": {"over": 1.8, "under": 2.0}}},
                       "betsson": {"totals": {"2.5": {"over": 2.10, "under": 1.75}}}}}]})

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
        self.assertEqual(bo[self.D.line_key("Match result", "Leeds")], {"svenskaspel_se": 1.55})
        self.assertEqual(bo[self.D.line_key("Match result", "Draw")], {"svenskaspel_se": 4.1})
        self.assertEqual(bo[self.D.line_key("Total goals", "Over 2.5")],
                         {"svenskaspel_se": 1.85, "betmgm_se": 1.8})
        self.assertEqual(ms[0]["bookOddsAt"], "2026-11-28T15:30:00Z")

    def test_a_book_he_has_no_account_at_is_kept_apart_from_the_price_that_decides(self):
        # Betsson's 2.10 is the best Over 2.5 price in Sweden, but he cannot bet it. It must not reach
        # bookOdds (which decides), only otherBooks (which the weekly report reads).
        self.feed("2026-11-28T15:30:00Z")
        ms = [self.match()]
        self.A.attach_book_odds(ms, self.A.Repo(), self.now(), self.D)
        k = self.D.line_key("Total goals", "Over 2.5")
        self.assertNotIn("betsson", ms[0]["bookOdds"][k])
        self.assertEqual(ms[0]["otherBooks"][k], ("betsson", 2.10))

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


class BoardWindow(unittest.TestCase):
    """The board must keep the lines his own bookmakers priced, whatever their probability."""

    def setUp(self):
        import importlib
        self.A = importlib.import_module("autoprice")

    @staticmethod
    def line(prob, post=False, odds=None, bookPrice=None, sel="x"):
        return {"prob": prob, "selection": sel, "odds": odds, "bookPrice": bookPrice,
                "v1": {"post": post}, "v2": {"post": False}}

    def test_a_book_priced_line_survives_the_board_slice(self):
        # 30 strong lines nobody can bet, plus the one his own bookmaker priced at 29 %: the old slice kept
        # the 25 strongest and dropped the only bettable line on the match.
        noise = [self.line(96 - i * 0.1, sel=f"n{i}") for i in range(30)]
        book = self.line(29.1, bookPrice=3.55, odds=3.55, sel="Draw")
        keep = self.A.board_lines(noise + [book])
        self.assertIn(book, keep)
        self.assertEqual(len(keep), 30)
        self.assertEqual([x["prob"] for x in keep], sorted((x["prob"] for x in keep), reverse=True))

    def test_what_the_slice_keeps_when_it_has_to_choose(self):
        posted = self.line(12.0, post=True, bookPrice=9.0, sel="post")
        book = self.line(29.1, bookPrice=3.55, odds=3.55, sel="book")
        strong = self.line(88.0, sel="strong")
        weak = self.line(11.0, odds=2.0, sel="weak")      # a Pinnacle price he cannot bet, and a long shot
        keep = self.A.board_lines([strong, weak, book, posted], cap=2)
        self.assertEqual([x["selection"] for x in keep], ["book", "post"])   # by worth, then by probability
        self.assertNotIn("weak", [x["selection"] for x in self.A.board_lines([strong, weak, book, posted])])
