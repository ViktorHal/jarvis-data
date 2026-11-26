"""Settle Jarvis' bets and grade his match lines BY CODE from data/results/results.json (owner, 26 Nov 2026).

usage:
  python3 jarvis/settle.py bets <bets.json>        bets.json = [bet documents with id]   -> JSON on stdout
  python3 jarvis/settle.py matches <matches.json>  matches.json = [matches documents with id] -> JSON on stdout
Output per item: {"id", "outcome": "hit"|"miss"|"void"|null, "outcomeNote", "legs": [...], "result": {...}} or
{"id", "outcome": null, "why": "..."} when the code cannot settle it (no result yet, unknown market, extra time,
player not identified). The scheduled results task writes what this returns and checks only the nulls by hand.

Conventions (as BetMGM / Bet365 settle them):
  football goal, corner and card markets: 90 minutes plus stoppage time; a match that went to extra time or penalties
    is left for a manual check (FotMob's score then includes extra time)
  cards: yellow + red cards as FotMob counts them (a second yellow followed by a red may count differently at a
    bookmaker: a line within one card of the total when a red was shown is left for a manual check)
  player shots / shots on target: own goals excluded, blocked shots are shots but not on target; a player who did not
    play at all is void
  hockey: "Moneyline (incl. OT)" includes overtime and the shootout; "60-minute result", "Total goals (60 min)" and
    "Puck line" use the regulation-time score
"""
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "fetch"))
try:
    from autoprice import ALIASES, fold, match_name, _tok_score  # noqa: E402
except Exception:  # pragma: no cover - settle.py copied alone
    import unicodedata
    ALIASES = {}

    def fold(s):
        return "".join(c for c in unicodedata.normalize("NFKD", str(s or "")) if not unicodedata.combining(c)).lower().strip()

    def _tok_score(a, b):
        ta, tb = set(re.split(r"\W+", fold(a))) - {""}, set(re.split(r"\W+", fold(b))) - {""}
        return len(ta & tb) / max(len(ta), len(tb), 1)

    def match_name(q, names):
        best = max(names, key=lambda n: _tok_score(q, n), default=None)
        return best if best and _tok_score(q, best) >= 0.5 else None

RESULTS = os.path.join(os.path.dirname(HERE), "data", "results", "results.json")
GROUPS = ["Match result", "Double chance", "Draw no bet", "Total goals", "Both teams to score", "Team goals",
          "Half with most goals", "Corners", "Team corners", "Cards", "Red card", "Player shots", "Player shots on target",
          "60-minute result", "Moneyline (incl. OT)", "Puck line", "Total goals (60 min)", "Player shots on goal",
          "Player points", "Moneyline", "Spread", "Total points"]
SAN = lambda s: re.sub(r"[^A-Za-z0-9]+", "_", s)
G_BY_SAN = {SAN(g): g for g in GROUPS}
OU = re.compile(r"^(Over|Under)_(\d+)_(\d+)$")


def _t(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def split_event(ev):
    for sep in (" – ", " - ", " — ", " v ", " vs "):
        if sep in str(ev or ""):
            a, b = str(ev).split(sep, 1)
            return a.strip(), b.strip()
    return None, None


def name_score(a, b):
    fa = fold(a)
    alts = [a] + ALIASES.get(fa, [])
    return max(_tok_score(x, b) for x in alts)


def find_result(event, start, results, sport=None):
    """The finished match for a bet: kick-off within 3 hours and both team names matching."""
    h, a = split_event(event)
    st = _t(start)
    if not h or not st:
        return None, "event or start not readable"
    best = None
    for r in results:
        rt = _t(r.get("start"))
        if not rt or abs((rt - st).total_seconds()) > 3 * 3600:
            continue
        if sport and ((sport == "hockey") != (r["sport"] in ("nhl", "shl"))):
            continue
        names_h = [r["home"]] + ([r["homeAbbrev"]] if r.get("homeAbbrev") else [])
        names_a = [r["away"]] + ([r["awayAbbrev"]] if r.get("awayAbbrev") else [])
        sh = max(name_score(h, n) for n in names_h)
        sa = max(name_score(a, n) for n in names_a)
        if sh >= 0.5 and sa >= 0.5 and (best is None or sh + sa > best[0]):
            best = (sh + sa, r)
    return (best[1], None) if best else (None, "no finished result found yet")


def _side(sel_s, home, away):
    """'home' / 'away' for a sanitized selection that starts with a team name (as the bet names them)."""
    hs, as_ = SAN(home).strip("_"), SAN(away).strip("_")
    if sel_s.startswith(hs) and not (as_.startswith(hs) and sel_s.startswith(as_)):
        return "home", sel_s[len(hs):]
    if sel_s.startswith(as_):
        return "away", sel_s[len(as_):]
    return None, sel_s


def _ou(kind, total, line):
    if total is None:
        return None
    if kind == "Over":
        return 1 if total > line else 0
    return 1 if total < line else 0


def settle_line(group, sel_s, pick, home, away, r):
    """-> (value, note): 1 hit, 0 miss, 0.5 void/push, None = cannot settle by code (note says why)."""
    hockey = r["sport"] in ("nhl", "shl")
    if not hockey and r.get("ended") in ("AET", "Pen", "AP"):
        return None, "extra time / penalties: settle by hand"
    sc = r.get("score") or [None, None]
    if hockey:
        reg = r.get("reg") or [None, None]
    if None in sc:
        return None, "score missing"
    hg, ag = sc
    m = OU.match(sel_s)
    line = float(f"{m.group(2)}.{m.group(3)}") if m else None
    if group == "Match result" or group == "60-minute result":
        x, y = (reg if group == "60-minute result" else sc)
        if sel_s == "Draw":
            return (1 if x == y else 0), f"{x}-{y}"
        side, _ = _side(sel_s, home, away)
        if not side:
            return None, "team not identified"
        win = x > y if side == "home" else y > x
        return (1 if win else 0), f"{x}-{y}"
    if group in ("Moneyline (incl. OT)", "Moneyline"):
        side, _ = _side(sel_s, home, away)
        if not side:
            return None, "team not identified"
        return (1 if (hg > ag) == (side == "home") else 0), f"{hg}-{ag} {r.get('ended', '')}".strip()
    if group == "Double chance":
        side, rest = _side(sel_s, home, away)
        if rest == "_or_draw":
            ok = hg >= ag if side == "home" else ag >= hg
        else:
            ok = hg != ag
        return (1 if ok else 0), f"{hg}-{ag}"
    if group == "Draw no bet":
        side, _ = _side(sel_s, home, away)
        if hg == ag:
            return 0.5, f"{hg}-{ag} (void)"
        return (1 if (hg > ag) == (side == "home") else 0), f"{hg}-{ag}"
    if group in ("Total goals", "Total goals (60 min)") and m:
        tot = sum(reg) if group == "Total goals (60 min)" else hg + ag
        return _ou(m.group(1), tot, line), f"{tot} goals"
    if group == "Both teams to score":
        y = hg > 0 and ag > 0
        return (1 if y == (sel_s == "Yes") else 0), f"{hg}-{ag}"
    if group in ("Team goals", "Team corners"):
        side, rest = _side(sel_s, home, away)
        mm = re.match(r"^_over_(\d+)_(\d+)$", rest)
        if not side or not mm:
            return None, "team line not identified"
        ln = float(f"{mm.group(1)}.{mm.group(2)}")
        if group == "Team goals":
            v = hg if side == "home" else ag
            return (1 if v > ln else 0), f"{v} goals"
        c = r.get("corners")
        if not c:
            return None, "corners missing"
        v = c[0] if side == "home" else c[1]
        return (1 if v > ln else 0), f"{v} corners"
    if group == "Corners" and m:
        c = r.get("corners")
        if not c:
            return None, "corners missing"
        return _ou(m.group(1), sum(c), line), f"{sum(c)} corners"
    if group == "Cards" and m:
        y, rd = r.get("yellow"), r.get("red")
        if not y:
            return None, "cards missing"
        tot = sum(y) + sum(rd or [0, 0])
        if sum(rd or [0, 0]) and abs(tot - line) <= 1:
            return None, f"{tot} cards incl. a red: count rules differ, settle by hand"
        return _ou(m.group(1), tot, line), f"{tot} cards"
    if group == "Puck line":
        mm = re.search(r"([+-])\s?(\d+(?:\.\d+)?)", pick or "")
        side, _ = _side(sel_s, home, away)
        if not side or not mm:
            return None, "puck line sign not readable"
        hcap = float(mm.group(1) + mm.group(2))
        x, y = reg
        diff = (x - y) if side == "home" else (y - x)
        return (1 if diff + hcap > 0 else 0), f"{x}-{y} after 60 min"
    if group in ("Player shots", "Player shots on target"):
        mm = re.match(r"^(.*)_(\d+)_$", sel_s)
        if not mm:
            return None, "player line not readable"
        name, n = mm.group(1).replace("_", " "), int(mm.group(2))
        if not r.get("playersKnown"):
            return None, "line-ups missing in the result"
        cand = {p["name"]: p for p in r.get("players", [])}
        hit = match_name(name, cand)
        if not hit:
            return 0.5, f"{name} did not play (void)"
        p = cand[hit]
        v = p["shots"] if group == "Player shots" else p["sot"]
        return (1 if v >= n else 0), f"{hit} {v} {'shots' if group == 'Player shots' else 'on target'}"
    return None, f"market {group} not settled by code"


def parse_key(key):
    """Line key -> (group, sanitized selection). decision.line_key collapses the "__" separator into one "_" when the
    group ends in a bracket ("Moneyline (incl. OT)") or the selection starts with a letter outside A-Z ("Örebro")."""
    if not key:
        return None, None
    for g in sorted(GROUPS, key=len, reverse=True):
        sg = SAN(g).rstrip("_")
        if key.startswith(sg + "_"):
            return g, key[len(sg):].lstrip("_")
    return None, None


def settle_leg(leg, results, fallback_event=None, fallback_start=None, sport=None):
    group, sel_s = parse_key(leg.get("key"))
    if not group:
        return None, "no line key", None
    ev = leg.get("event") or fallback_event
    r, why = find_result(ev, leg.get("start") or fallback_start, results, sport)
    if not r:
        return None, why, None
    h, a = split_event(ev)
    v, note = settle_line(group, sel_s, leg.get("pick"), h, a, r)
    return v, note, r


def _sport(b):
    s = str(b.get("sport") or "").lower()
    return "hockey" if "hockey" in s else "football" if "foot" in s else None


def settle_bet(b, results):
    legs = b.get("legs") if isinstance(b.get("legs"), list) and b["legs"] else None
    if not legs:
        v, note, r = settle_leg(b, results, b.get("event"), b.get("start"), _sport(b))
        if v is None:
            return {"id": b.get("id"), "outcome": None, "why": note}
        return {"id": b.get("id"), "outcome": {1: "hit", 0: "miss", 0.5: "void"}[v], "outcomeNote": note,
                "result": {"id": r["id"], "home": r["home"], "away": r["away"], "score": r["score"]}}
    outs, notes = [], []
    for lg in legs:
        ev = lg.get("event") or (b.get("event") if not str(b.get("event", "")).startswith("Jarvis") else None)
        v, note, _ = settle_leg(lg, results, ev, lg.get("start") or b.get("start"), _sport(lg) or _sport(b))
        outs.append(v)
        notes.append(note)
    leg_out = [None if v is None else {1: "hit", 0: "miss", 0.5: "void"}[v] for v in outs]
    if 0 in outs:
        res = "miss"
    elif None in outs:
        return {"id": b.get("id"), "outcome": None, "why": "; ".join(n for v, n in zip(outs, notes) if v is None), "legs": leg_out}
    elif all(v == 0.5 for v in outs):
        res = "void"
    else:
        res = "hit"
    return {"id": b.get("id"), "outcome": res, "outcomeNote": "; ".join(notes), "legs": leg_out}


def grade_match(m, results):
    """Every line of a matches document: graded {key: 1|0|0.5} plus a result summary."""
    r, why = find_result(m.get("event"), m.get("start"), results, _sport(m))
    if not r:
        return {"id": m.get("id"), "graded": None, "why": why}
    h, a = split_event(m.get("event"))
    graded, skipped = {}, 0
    for row in m.get("markets") or []:
        g, s = parse_key(row.get("key"))
        if not g:
            continue
        v, _ = settle_line(g, s, row.get("selection"), h, a, r)
        if v is None:
            skipped += 1
        else:
            graded[row["key"]] = v
    res = f'{r["home"]} {r["score"][0]}-{r["score"][1]} {r["away"]}'
    if r.get("corners"):
        res += f', {sum(r["corners"])} corners'
    if r.get("yellow"):
        res += f', {sum(r["yellow"]) + sum(r.get("red") or [0, 0])} cards'
    if r.get("ended") and r["ended"] not in ("FT", "REG"):
        res += f' ({r["ended"]})'
    return {"id": m.get("id"), "result": res, "graded": graded, "notSettledByCode": skipped}


def load_results(path=RESULTS):
    return json.load(open(path, encoding="utf-8"))["matches"]


def main(argv):
    kind, path = argv[0], argv[1]
    items = json.load(open(path, encoding="utf-8"))
    res = load_results(argv[2] if len(argv) > 2 else RESULTS)
    fn = settle_bet if kind == "bets" else grade_match
    print(json.dumps([fn(x, res) for x in items], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
