"""ClubElo: one daily snapshot of every club's Elo rating (api.clubelo.com/<date>, CSV, free).

Writes data/elo/latest.csv and data/elo/latest.json {club: {elo, country, level}} plus the snapshot date.
A rating is a pre-match input only for matches on a LATER date than the snapshot.
"""
import csv
import io
from datetime import date

from common import Log, get_text, write_json, write_text, now_iso


def parse(text):
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        try:
            out[r["Club"]] = {"elo": round(float(r["Elo"]), 1), "country": r.get("Country"), "level": r.get("Level")}
        except (KeyError, ValueError):
            continue
    return out


def run(log: Log, today=None):
    d = (today or date.today()).isoformat()
    url = f"http://api.clubelo.com/{d}"
    try:
        try:
            text = get_text(url, timeout=25, retries=1)    # clubelo.com does not answer GitHub's servers today;
        except Exception:                                   # keep the attempt short so it never slows the run
            url = f"https://api.clubelo.com/{d}"
            text = get_text(url, timeout=25, retries=1)
        clubs = parse(text)
        if not clubs:
            raise ValueError("empty snapshot")
        write_text("elo/latest.csv", text)
        write_json("elo/latest.json", {"meta": {"snapshotDate": d, "fetchedAt": now_iso(), "source": url,
                   "note": "use only for matches played after snapshotDate"}, "clubs": clubs})
        log.ok("clubelo", url, len(clubs))
    except Exception as e:
        log.fail("clubelo", url, e)
