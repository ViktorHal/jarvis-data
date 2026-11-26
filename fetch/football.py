"""football-data.co.uk: results, shots, shots on target, corners, fouls, cards, referees and
bookmaker prices (incl. closing prices) per match, plus upcoming fixtures with prices.

Writes, per league:
  data/football/raw/<code>.csv          the source file as downloaded (2026/27 season)
  data/football/teams_<code>.json       team table: overall / home / away / last 6 averages
  data/football/referees_<code>.json    referee table: games, yellow and red cards per game
  data/football/fixtures.csv / fixtures.json   upcoming matches with pre-match prices
No xG: football-data.co.uk does not publish it (Jarvis gets xG elsewhere).
"""
import csv
import io
from datetime import datetime

from common import Log, get_text, write_json, write_text, now_iso

SEASON = "2627"   # 2026/27
BASE = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
FIXTURES = "https://www.football-data.co.uk/fixtures.csv"
LEAGUES = {
    "E0": "Premier League", "E1": "Championship", "SP1": "La Liga", "I1": "Serie A", "D1": "Bundesliga",
    "F1": "Ligue 1", "N1": "Eredivisie", "B1": "Belgian Pro League", "P1": "Primeira Liga",
    "T1": "Super Lig", "G1": "Super League Greece", "SC0": "Scottish Premiership", "SP2": "La Liga 2",
    "I2": "Serie B", "D2": "2. Bundesliga", "F2": "Ligue 2",
}
# columns per match: home value, away value
STATS = {"goals": ("FTHG", "FTAG"), "shots": ("HS", "AS"), "sot": ("HST", "AST"), "corners": ("HC", "AC"),
         "fouls": ("HF", "AF"), "yellow": ("HY", "AY"), "red": ("HR", "AR")}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _date(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except (ValueError, AttributeError):
            continue
    return None


def parse_matches(text):
    """CSV text -> list of played matches (dicts) with ISO dates, sorted by date."""
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        d = _date(r.get("Date", ""))
        if not d or not r.get("HomeTeam") or _num(r.get("FTHG")) is None:
            continue
        r["_date"] = d
        out.append(r)
    return sorted(out, key=lambda r: (r["_date"], r.get("Time") or ""))


def _blank():
    return {"games": 0, **{f"{k}_for": 0.0 for k in STATS}, **{f"{k}_against": 0.0 for k in STATS},
            **{f"_{k}_n": 0 for k in STATS}}


def _add(acc, r, side):
    """Add one match to a team accumulator from the team's side ('H' or 'A')."""
    acc["games"] += 1
    for k, (h, a) in STATS.items():
        mine, theirs = (h, a) if side == "H" else (a, h)
        vm, vt = _num(r.get(mine)), _num(r.get(theirs))
        if vm is None or vt is None:
            continue                      # a column missing for this match: not counted, never guessed
        acc[f"{k}_for"] += vm
        acc[f"{k}_against"] += vt
        acc[f"_{k}_n"] += 1


def _per_game(acc):
    out = {"games": acc["games"]}
    for k in STATS:
        n = acc[f"_{k}_n"]
        out[f"{k}_for_pg"] = round(acc[f"{k}_for"] / n, 3) if n else None
        out[f"{k}_against_pg"] = round(acc[f"{k}_against"] / n, 3) if n else None
        out[f"{k}_games"] = n
    return out


def team_table(matches):
    teams = {}
    for r in matches:
        for side, name in (("H", r["HomeTeam"]), ("A", r["AwayTeam"])):
            t = teams.setdefault(name, {"all": _blank(), "home": _blank(), "away": _blank(), "log": []})
            _add(t["all"], r, side)
            _add(t["home" if side == "H" else "away"], r, side)
            gf, ga = (r["FTHG"], r["FTAG"]) if side == "H" else (r["FTAG"], r["FTHG"])
            t["log"].append({"date": r["_date"], "venue": side, "opp": r["AwayTeam"] if side == "H" else r["HomeTeam"],
                             "gf": int(float(gf)), "ga": int(float(ga)),
                             **{k: _num(r.get(STATS[k][0] if side == "H" else STATS[k][1])) for k in ("shots", "sot", "corners", "yellow")}})
    out = {}
    for name, t in teams.items():
        last6 = _blank()
        for r in [m for m in matches if name in (m["HomeTeam"], m["AwayTeam"])][-6:]:
            _add(last6, r, "H" if r["HomeTeam"] == name else "A")
        out[name] = {"all": _per_game(t["all"]), "home": _per_game(t["home"]), "away": _per_game(t["away"]),
                     "last6": _per_game(last6), "lastMatch": t["log"][-1]["date"], "log": t["log"][-10:]}
    return out


def referee_table(matches):
    refs = {}
    for r in matches:
        name = (r.get("Referee") or "").strip()
        y = [_num(r.get("HY")), _num(r.get("AY"))]
        rd = [_num(r.get("HR")), _num(r.get("AR"))]
        if not name or None in y:
            continue
        x = refs.setdefault(name, {"games": 0, "yellow": 0.0, "red": 0.0, "fouls": 0.0, "_f": 0})
        x["games"] += 1
        x["yellow"] += sum(y)
        x["red"] += sum(v for v in rd if v is not None)
        f = [_num(r.get("HF")), _num(r.get("AF"))]
        if None not in f:
            x["fouls"] += sum(f)
            x["_f"] += 1
    return {n: {"games": x["games"], "yellow_pg": round(x["yellow"] / x["games"], 3),
                "red_rate": round(x["red"] / x["games"], 3),
                "fouls_pg": round(x["fouls"] / x["_f"], 2) if x["_f"] else None} for n, x in refs.items()}


PRICE_COLS = ["B365H", "B365D", "B365A", "PSH", "PSD", "PSA", "AvgH", "AvgD", "AvgA", "MaxH", "MaxD", "MaxA",
              "B365>2.5", "B365<2.5", "P>2.5", "P<2.5", "Avg>2.5", "Avg<2.5", "AHh", "B365AHH", "B365AHA"]


def parse_fixtures(text):
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        d = _date(r.get("Date", ""))
        if not d or not r.get("HomeTeam"):
            continue
        out.append({"league": r.get("Div"), "date": d, "time": r.get("Time"), "home": r["HomeTeam"],
                    "away": r["AwayTeam"], "referee": r.get("Referee") or None,
                    "prices": {c: _num(r.get(c)) for c in PRICE_COLS if _num(r.get(c)) is not None}})
    return out


def run(log: Log):
    for code, name in LEAGUES.items():
        url = BASE.format(season=SEASON, code=code)
        try:
            text = get_text(url)
            matches = parse_matches(text)
            if not matches:
                raise ValueError("no played matches in file")
            write_text(f"football/raw/{code}.csv", text)
            meta = {"league": name, "code": code, "season": "2026/27", "source": url, "fetchedAt": now_iso(),
                    "matches": len(matches), "lastMatchDate": matches[-1]["_date"],
                    "note": "per-game averages over matches where the column exists; no xG in this source"}
            write_json(f"football/teams_{code}.json", {"meta": meta, "teams": team_table(matches)})
            write_json(f"football/referees_{code}.json", {"meta": meta, "referees": referee_table(matches)})
            log.ok(f"football-data {code}", url, len(matches))
        except Exception as e:  # one league failing never stops the others
            log.fail(f"football-data {code}", url, e)
    try:
        text = get_text(FIXTURES)
        fx = parse_fixtures(text)
        write_text("football/fixtures.csv", text)
        write_json("football/fixtures.json", {"meta": {"source": FIXTURES, "fetchedAt": now_iso(),
                   "note": "football-data.co.uk updates this file a few times a week; prices are a snapshot, check fetchedAt"},
                   "fixtures": fx})
        log.ok("football-data fixtures", FIXTURES, len(fx))
    except Exception as e:
        log.fail("football-data fixtures", FIXTURES, e)
