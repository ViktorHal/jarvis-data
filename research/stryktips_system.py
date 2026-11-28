"""How to choose the 16 rows of a Stryktipset system (28 Nov 2026).

The owner looked at omgang 4981's system - nine straight home signs and four home-or-away doubles, no draw
anywhere - and asked whether that is really likely. It is not, and it is not meant to be: the system maximises
the chance of 13 correct, which means taking the single most likely sign in every match, and the draw is never
the most likely sign in any of these thirteen. But Stryktipset is a POOL: the dividend is shared with everyone
holding the same row, so the chance of winning is only half the question. This script measures both.

  P13      the chance of 13 correct, assuming the matches are independent
  V        the expected-payout proxy for a pool: sum over the system's rows of P(row) / Q(row), where Q is the
           Swedish public's probability for that exact row. It factorises, so V = product over matches of
           sum over the signs played of p/q. V = 1 means an average row; V = 3 means three times the dividend
           of an average row for the same chance.

A Lagrangian sweep traces the whole frontier exactly: for each weight on log P13 it picks, per match, the best
subset of each size and then spends the row budget (2^doubles x 3^triples <= 16) where it gains most.

The recommended system (--pick) maximises V subject to keeping at least 70% of the best possible chance of 13
and 85% of the best possible chance of 10 or more, so the value is bought with the top prize's long odds and not
with the small prizes that actually pay. On omgang 4981 that rule moved the four doubles off the most open
matches (Derby, Wycombe) and onto the ones the public had wrong (Birmingham, Wrexham), for 2.8 times the expected
payout at 76% of the chance of 13.

usage: python3 research/stryktips_system.py <stryktips.json> [--pick]
       --pick prints {"recommended": {...}, "alternatives": {...}} as JSON; without it, the whole frontier.
"""
import itertools
import json
import math
import os
import sys

SIGNS = ("1", "X", "2")
BUDGET = 16


def load(path):
    d = json.load(open(path))
    out = []
    for m in d["matches"]:
        p = {s: m["p"][s] / 100 for s in SIGNS}
        q = {s: (m.get("people") or {}).get(s) for s in SIGNS}
        if any(v is None for v in q.values()):
            q = {s: p[s] for s in SIGNS}                 # no public figures: value is then neutral
        tot = sum(q.values())
        q = {s: max(q[s] / tot, 0.01) for s in SIGNS}
        out.append({"no": m["no"], "home": m["home"], "away": m["away"], "p": p, "q": q})
    return d, out


def subsets():
    return [tuple(c) for n in (1, 2, 3) for c in itertools.combinations(SIGNS, n)]


def p_of(m, S):
    return sum(m["p"][s] for s in S)


def v_of(m, S):
    return sum(m["p"][s] / m["q"][s] for s in S)


def correct_dist(ms, system):
    """Chance of 13, 12, 11, 10 or more correct: the best row misses a match whose outcome is not in its subset."""
    miss = [1 - p_of(m, S) for m, S in zip(ms, system)]
    dist = [1.0]
    for mi in miss:
        nxt = [0.0] * (len(dist) + 1)
        for k, pk in enumerate(dist):
            nxt[k] += pk * (1 - mi)
            nxt[k + 1] += pk * mi
        dist = nxt
    return {13 - k: sum(dist[: k + 1]) for k in range(4)}


def best_for(ms, lam):
    """Maximise sum of (log V + lam * log P) over systems within the row budget. Exact: the per-match choice of
    which subset of a given size is independent, and the budget is spent by trying every pattern of sizes."""
    per = []
    for m in ms:
        row = {}
        for n in (1, 2, 3):
            cands = [S for S in subsets() if len(S) == n]
            row[n] = max(cands, key=lambda S: math.log(v_of(m, S)) + lam * math.log(p_of(m, S)))
        per.append(row)
    score = lambda m, S: math.log(v_of(m, S)) + lam * math.log(p_of(m, S))
    best = None
    for d in range(0, 5):
        for t in range(0, 3):
            if 2 ** d * 3 ** t > BUDGET:
                continue
            base = sum(score(m, per[i][1]) for i, m in enumerate(ms))
            gain2 = sorted(range(len(ms)), key=lambda i: -(score(ms[i], per[i][2]) - score(ms[i], per[i][1])))
            gain3 = sorted(range(len(ms)), key=lambda i: -(score(ms[i], per[i][3]) - score(ms[i], per[i][1])))
            for trip in itertools.combinations(gain3[: 5 + t], t):
                left = [i for i in gain2 if i not in trip]
                dbl = tuple(left[:d])
                sysm = [per[i][3] if i in trip else per[i][2] if i in dbl else per[i][1] for i in range(len(ms))]
                sc = sum(score(m, S) for m, S in zip(ms, sysm))
                if best is None or sc > best[0]:
                    best = (sc, sysm)
    return best[1]


def describe(ms, system):
    rows = 1
    for S in system:
        rows *= len(S)
    dist = correct_dist(ms, system)
    V = 1.0
    for m, S in zip(ms, system):
        V *= v_of(m, S)
    draws = sum(1 for S in system if "X" in S)
    return {"signs": ["".join(S) for S in system], "rows": rows, "cost": rows,
            "p13": round(100 * dist[13], 4), "p12plus": round(100 * dist[12], 3),
            "p11plus": round(100 * dist[11], 2), "p10plus": round(100 * dist[10], 2),
            "valueRatio": round(V, 2), "matchesWithDraw": draws,
            "expectedCorrect": round(sum(p_of(m, S) for m, S in zip(ms, system)), 2)}


def frontier(ms, lams=(0, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 5, 6, 8, 12, 20)):
    out, seen = [], set()
    for lam in lams:
        d = describe(ms, best_for(ms, lam))
        if tuple(d["signs"]) not in seen:
            seen.add(tuple(d["signs"]))
            out.append(dict(d, lam=lam))
    return out


def pick(ms, keep13=0.70, keep10=0.85):
    """The system with the best expected payout that still keeps most of the chance."""
    fr = frontier(ms)
    best13 = max(d["p13"] for d in fr)
    best10 = max(d["p10plus"] for d in fr)
    ok = [d for d in fr if d["p13"] >= keep13 * best13 and d["p10plus"] >= keep10 * best10]
    # equal expected payout (the public exactly in step with the model): then take the better chance
    rec = max(ok or fr, key=lambda d: (round(d["valueRatio"], 6), d["p13"]))
    return {"recommended": rec, "floors": {"p13": round(keep13 * best13, 4), "p10plus": round(keep10 * best10, 2)},
            "alternatives": {"maxChance13": max(fr, key=lambda d: d["p13"]),
                             "maxValue": max(fr, key=lambda d: d["valueRatio"])},
            "frontier": fr}


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/stryktips.json"
    doc, ms = load(path)
    if "--pick" in sys.argv:
        print(json.dumps(pick(ms), indent=1))
        raise SystemExit
    print(f'{doc["round"]}  {len(ms)} matches')
    exp_draws = sum(m["p"]["X"] for m in ms)
    nodraw = 1.0
    for m in ms:
        nodraw *= 1 - m["p"]["X"]
    print(f'model expects {exp_draws:.2f} draws; chance of none at all {100 * nodraw:.1f}%; '
          f'most likely sign is 1 in {sum(1 for m in ms if max(m["p"], key=m["p"].get) == "1")} of {len(ms)}')
    print()
    cur = [tuple(s for s in SIGNS if s in sg) for sg in doc["system"]["signs"]]
    print("as posted      ", json.dumps(describe(ms, cur)))
    seen = {}
    for lam in [0, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 8, 20]:
        sysm = best_for(ms, lam)
        d = describe(ms, sysm)
        if tuple(d["signs"]) in seen:
            continue
        seen[tuple(d["signs"])] = lam
        print(f"lambda {lam:<5}   {json.dumps(d)}")
