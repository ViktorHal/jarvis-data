"""Independent check of the data chain: jarvis-data repo file -> provenance values -> model inputs.

Input: a JSON file with a list of match documents as stored in the Jarvis database (fields used: id, event,
league, sport, created/updated, inputs, provenance, coverage, oddsAt, marketOdds, marketOddsSecondary,
markets (prob12, prob12a, prob12b, prob12s, prob12h), p2, decision.at) and a clone of jarvis-data with
history. For every provenance item whose source is "jarvis-data/...":
  - the file is read AT THE COMMIT the scan recorded (repoCommit), never at today's HEAD;
  - every number in provenance.values must equal the file's number for that team/referee exactly;
  - the model input derived from it must equal that number (corners, cards, referee, NHL goals);
  - the file's age at the moment of use is computed (decision.at - repoFetchedAt).
Prints a JSON report for points 1-8 of the owner's control list.
"""
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime

FOOT_ITEMS = ("F1", "F1b", "F1c", "F2", "F3", "F4", "F5", "F6")
HOCKEY_ITEMS = ("H1", "H1b", "H2", "H3", "H4", "H5", "H6")
# model input <- file field (team-level items; side = home/away team of the match)
INPUT_MAP = {"F2": {"corners_for": "corners_for_pg", "corners_against": "corners_against_pg"},
             "F3": {"cards": "yellow_for_pg"}}


def _t(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def file_at(repo, commit, path):
    """File content at a commit (path relative to the repo root, e.g. data/nhl/teams.json)."""
    rel = path.split("jarvis-data/", 1)[-1].split(" ")[0]
    if not rel.startswith("data/"):
        rel = "data/" + rel
    out = subprocess.run(["git", "-C", repo, "show", f"{commit}:{rel}"], capture_output=True, text=True)
    if out.returncode:
        raise FileNotFoundError(f"{rel} at {commit}: {out.stderr.strip()[:120]}")
    return json.loads(out.stdout)


def lookup(js, team, split):
    """Value table for a team (football 'teams' with splits; NHL 'teams' flat; referees; goalies; skaters)."""
    for coll in ("teams", "referees", "goalies", "skaters"):
        if coll in js and team in js[coll]:
            row = js[coll][team]
            return row.get(split or "all", row) if isinstance(row.get("all"), dict) else row
    return None


def check_h6(m, js, vals):
    """Hockey 1.2: provenance values == the h12 state file at repoCommit, and the match's h12 input == values."""
    out = []
    for side in ("home", "away"):
        v = vals.get(side) or {}
        row = (js.get("teams") or {}).get(v.get("team"))
        if row is None:
            out.append(f"H6/{side}: '{v.get('team')}' not in file")
            continue
        for k in ("sf", "sa", "gf", "ga", "n", "lastDate"):
            if row.get(k) != v.get(k):
                out.append(f"H6/{side}: {k} used {v.get(k)} but file has {row.get(k)}")
    for side in ("goalieHome", "goalieAway"):
        g = vals.get(side)
        if g:
            rec = (js.get("goalies") or {}).get(g["name"], [0.0, 0.0])
            if [g["saves"], g["shotsAgainst"]] != list(rec):
                out.append(f"H6/{side}: {g['name']} used {g['saves']}/{g['shotsAgainst']} but file has {rec}")
    if vals.get("league") != js.get("league"):
        out.append("H6: league constants differ from the file")
    h12 = m.get("h12") or {}
    for k in ("home", "away", "goalieHome", "goalieAway", "b2bHome", "b2bAway", "league"):
        x, y = h12.get(k), vals.get(k)
        if isinstance(x, dict) and isinstance(y, dict):
            x, y = {a: b for a, b in x.items() if a != "team"}, {a: b for a, b in y.items() if a != "team"}
        if x != y:
            out.append(f"H6: model input h12.{k} differs from the recorded values")
    if h12.get("params") != js.get("params"):
        out.append("H6: h12 params differ from the file")
    return out


def check_match(m, repo):
    res = {"id": m.get("id"), "event": m.get("event"), "league": m.get("league"), "sport": m.get("sport"),
           "items": {}, "mismatches": [], "repoItems": 0, "maxAgeH": None, "stalePresentedAsVerified": []}
    prov = m.get("provenance") or {}
    used_at = (m.get("decision") or {}).get("at") or m.get("updated")
    items = HOCKEY_ITEMS if str(m.get("sport", "")).lower().startswith("ice") else FOOT_ITEMS
    cov = (m.get("coverage") or {}).get("items") or {}
    for it in items:
        e = prov.get(it)
        res["items"][it] = cov.get(it) or (e or {}).get("status") or "unrecorded"
        if not isinstance(e, dict) or not str(e.get("source", "")).startswith("jarvis-data"):
            continue
        res["repoItems"] += 1
        age = None
        if _t(used_at) and _t(e.get("repoFetchedAt")):
            age = round((_t(used_at) - _t(e["repoFetchedAt"])).total_seconds() / 3600, 2)
            res["maxAgeH"] = age if res["maxAgeH"] is None else max(res["maxAgeH"], age)
        if (age is None or age > 18) and res["items"][it] == "verified":
            res["stalePresentedAsVerified"].append(it)
        try:
            js = file_at(repo, e.get("repoCommit", "HEAD"), e["source"])
        except Exception as ex:
            res["mismatches"].append(f"{it}: file not readable ({ex})")
            continue
        vals = e.get("values") or {}
        if not vals:
            res["mismatches"].append(f"{it}: no values recorded (audit trail missing)")
            continue
        if it == "H6":
            res["mismatches"] += check_h6(m, js, vals)
            continue
        sides = vals if any(k in vals for k in ("home", "away")) else {"_": vals}
        for side, v in sides.items():
            team = v.get("team") or e.get("team") if side == "_" else v.get("team")
            sjs = js
            if isinstance(v, dict) and str(v.get("source", "")).startswith("jarvis-data"):
                try:                                    # cross-league ties: each side names its own file
                    sjs = file_at(repo, e.get("repoCommit", "HEAD"), v["source"])
                except Exception as ex:
                    res["mismatches"].append(f"{it}/{side}: file not readable ({ex})")
                    continue
            row = lookup(sjs, team, v.get("split"))
            if row is None:
                res["mismatches"].append(f"{it}/{side}: '{team}' not in file")
                continue
            for k, x in v.items():
                if k in ("team", "split", "source"):
                    continue
                if row.get(k) != x:
                    res["mismatches"].append(f"{it}/{side}: {k} used {x} but file has {row.get(k)}")
            # file -> model input
            if it in INPUT_MAP and side in ("home", "away"):
                inp = (m.get("inputs") or {}).get(side) or {}
                for ik, fk in INPUT_MAP[it].items():
                    if fk in row and ik in inp and inp[ik] != row[fk]:
                        res["mismatches"].append(f"{it}/{side}: input {ik}={inp[ik]} but file {fk}={row[fk]}")
    # odds: repo prices must never be the reference
    srcs = m.get("marketSources") or {}
    # football-data's fixture snapshot must never be the reference; hourly Pinnacle prices from the repo may be
    res["repoPricesAsReference"] = sorted(k for k, s in srcs.items()
                                          if ("football-data" in str(s) or "fixtures" in str(s)) and "pinnacle" not in str(s).lower())
    oa = m.get("oddsAt") or {}
    res["pinnacleAgeMin"] = {k: round((_t(used_at) - _t(oa.get(k))).total_seconds() / 60) for k, s in srcs.items()
                             if "pinnacle" in str(s).lower() and _t(used_at) and _t(oa.get(k))}
    # shadow models
    rows = m.get("markets") or []
    res["shadow"] = {f: sum(1 for r in rows if r.get(f) is not None) for f in ("prob12", "prob12a", "prob12b", "prob12s", "prob12h")}
    res["p2"] = bool(m.get("p2"))
    return res


def report(matches, repo, before=None):
    per = [check_match(m, repo) for m in matches]
    by_league = defaultdict(lambda: Counter())
    for r in per:
        for it, st in r["items"].items():
            by_league[r["league"]][f"{it}:{st}"] += 1
        by_league[r["league"]]["matches"] += 1
    cov = [((m.get("coverage") or {}).get("pct")) for m in matches]
    cov = [c for c in cov if c is not None]
    return {"matches": len(per), "avgCoverageAfter": round(sum(cov) / len(cov)) if cov else None,
            "avgCoverageBefore": before, "repoItemsChecked": sum(r["repoItems"] for r in per),
            "mismatches": {r["event"]: r["mismatches"] for r in per if r["mismatches"]},
            "stalePresentedAsVerified": {r["event"]: r["stalePresentedAsVerified"] for r in per if r["stalePresentedAsVerified"]},
            "repoPricesAsReference": {r["event"]: r["repoPricesAsReference"] for r in per if r["repoPricesAsReference"]},
            "fileAgeHours": {r["event"]: r["maxAgeH"] for r in per if r["maxAgeH"] is not None},
            "shadow": {r["event"]: dict(r["shadow"], p2=r["p2"]) for r in per},
            "byLeague": {k: dict(v) for k, v in by_league.items()}, "perMatch": per}


if __name__ == "__main__":
    ms = json.load(open(sys.argv[1]))
    print(json.dumps(report(ms, sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else None), indent=1, ensure_ascii=False))
