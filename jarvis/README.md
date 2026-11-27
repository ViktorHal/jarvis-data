# Jarvis pipeline code

`decision.py` is the reviewed and tested decision pipeline. The Jarvis scans use this file only when its
sha256 (first 16 hex characters) equals `brain/decision.sha16` in the Jarvis database, so a change here is never
run until the owner's database names its hash. `fetch/hockey12.py` is pinned the same way by `brain/hockey12.sha16`.

## The modules

| File | What it does |
|---|---|
| `decision.py` | the whole decision pipeline: prices every market, de-margins the bookmakers' prices, decides what may be posted and at which minimum price, and builds the products (singles, combos, same-match builders, the favourites list and the price hunt) |
| `model.py` | model 1.1, the production pricing model: goals (Dixon-Coles), corners and cards v2, player shots and shots on target |
| `model12.py` | shadow football model 1.2 (team strength indices + Elo). Stored next to 1.1's numbers for comparison, never decides a posted bet - `tests/test_decision.py` enforces that |
| `../fetch/hockey12.py` | hockey model 1.2/1.3: decay-weighted shots and goals per team, starting goalie, 1.3 sharpening |
| `settle.py` | settles bets and grades unplayed lines from `data/results/results.json` |
| `stage.py` | preliminary or final call: the final call is the last scheduled scan at least 30 minutes before kick-off |
| `bookfit.py` | learns from the log which pick types his bookmakers actually price, and hides the kinds they never offer |

## Version 3.9 (27 Nov 2026) - the market anchor and the price hunt

Both changes come from backtests in `research/`, not from opinion:

* **3.8 market anchor** (`research/value_backtest.py`, 23,335 matches). The model's own value bets lost 6.7-8.8%
  at a soft bookmaker, and Pinnacle's de-margined price beat the model on log-loss in every season - the best blend
  weight for the model was zero. So where Pinnacle prices a complete market, Pinnacle's no-vig probability **is** the
  probability (`anchored: true`; the model's own is kept as `probModel` for the calibration report). A line whose only
  price is Pinnacle's can never be a value single, because he cannot bet at Pinnacle.
* **3.9 price hunt** (`research/line_drift.py`, 24,581 matches). A soft bookmaker merely paying more than Pinnacle's
  fair price is a weak signal: closing-line value +1.5%. The same bet taken **only where Pinnacle's own de-margined
  price has moved toward the pick by at least two points** gave 260 bets, +26.2% return and +18.5% closing-line
  value, positive in all four complete seasons. `fetch/autoprice.py` therefore keeps `data/auto/drift.json` (the
  first and latest de-margined Pinnacle probability per line, hourly), and `hunts()` lists only lines the market is
  moving toward. Caveat: the backtest saw the whole opening-to-closing move, a scan sees only the part so far.

## Tests

`python -m pytest tests -q` from the repository root (168 tests, no network). The same suite runs in GitHub Actions
before every fetch, odds and results job, so a code change that breaks it never writes data.

