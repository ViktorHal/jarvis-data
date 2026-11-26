# jarvis-data

Data robot for J.A.R.V.I.S., a personal betting assistant. A GitHub Actions job fetches free, public
sports data three times a day and commits it here as plain files, so Jarvis' scans read exact numbers
instead of skimming web pages.

Personal, non-commercial use. No bookmaker websites are accessed; prices come from football-data.co.uk.

## Sources

| Source | What | Files |
|---|---|---|
| [football-data.co.uk](https://www.football-data.co.uk) | 2026/27 results, shots, shots on target, corners, fouls, cards, referees, prices (incl. Pinnacle and closing prices), upcoming fixtures with prices | `data/football/` |
| NHL.com public JSON (`api-web.nhle.com`, `api.nhle.com/stats/rest`) | standings with last 10, shots, PP/PK, goalies, skaters, schedule, who played yesterday | `data/nhl/` |
| [stats.swehockey.se](https://stats.swehockey.se) | SHL 2026/27: every game with period scores (regulation, overtime, shootout), team table with last 10, home/away, back-to-back; today's and tomorrow's games | `data/shl/` |
| [statz.ai](https://statz.ai) | team xG and xGA 2026/27: season, home, away, last 5 (statz.ai's own xG model) | `data/xg/` |
| [ClubElo](http://clubelo.com) | daily Elo snapshot for every club (currently unreachable from GitHub's servers) | `data/elo/` |

`data/manifest.json` records, for every source and run, whether the fetch worked, how many rows it
returned and when. A failed source never deletes the last good file; every file carries `fetchedAt`.

## Run

```
python -m unittest discover -s tests   # offline tests (no network)
python fetch/run_all.py                # fetch everything into data/
```

Schedule: `.github/workflows/fetch.yml` (05:20, 11:20 and 17:20 Stockholm, plus manual runs).
