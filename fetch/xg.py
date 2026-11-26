"""Team xG from statz.ai league xG pages (public HTML; robots.txt allows /competitions/).

Each page has four tables: Home, Away, Last 5 and the full season, with goals, xG, goals against and xGA.
Writes data/xg/<slug>.json: {meta: {league, season, updatedText, source, fetchedAt}, teams: {name: {all, home,
away, last5: {played, goals, xg, ga, xga, xg_pg, xga_pg, goals_pg, ga_pg}}}}.
statz.ai computes its own xG (see the page's method note): a consistent but non-official source.
"""
import html
import re
import time

from common import Log, get_text, write_json, now_iso

LEAGUES = {"premier-league": "Premier League", "championship": "Championship", "la-liga": "La Liga",
           "serie-a": "Serie A", "bundesliga": "Bundesliga", "ligue-1": "Ligue 1", "eredivisie": "Eredivisie",
           "primeira-liga": "Primeira Liga", "allsvenskan": "Allsvenskan", "super-lig": "Super Lig",
           "scottish-premiership": "Scottish Premiership", "belgian-pro-league": "Belgian Pro League"}
URL = "https://statz.ai/competitions/{slug}/xg"


def _clean(c):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c or ""))).strip()


def _num(x):
    try:
        return float(x.replace("+", ""))
    except (AttributeError, ValueError):
        return None


def parse(page):
    """-> (season label, updated text, {split: {team: row}}) ; split in all/home/away/last5."""
    out, season = {}, None
    for m in re.finditer(r"<table.*?</table>", page, flags=re.S):
        heads = re.findall(r"<h[1-4][^>]*>(.*?)</h[1-4]>", page[max(0, m.start() - 4000):m.start()], flags=re.S)
        title = _clean(heads[-1]) if heads else ""
        sm = re.search(r"(20\d\d/20\d\d|20\d\d)", title)
        if sm:
            season = season or sm.group(1)
        split = "home" if title.endswith("Home") else "away" if title.endswith("Away") else \
                "last5" if title.endswith("Last 5") else "all" if "xG table" in title else None
        if not split:
            continue
        rows = re.findall(r"<tr.*?</tr>", m.group(0), flags=re.S)
        hdr = [_clean(c) for c in re.findall(r"<th[^>]*>(.*?)</th>", rows[0], flags=re.S)] if rows else []
        need = ["Team", "Played", "Goals", "xG", "Goals against", "xGA"]
        if not all(h in hdr for h in need):
            continue
        ix = {h: hdr.index(h) for h in need}
        table = {}
        for r in rows[1:]:
            c = [_clean(x) for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.S)]
            if len(c) < len(hdr):
                continue
            p = _num(c[ix["Played"]])
            v = {k: _num(c[ix[h]]) for k, h in (("goals", "Goals"), ("xg", "xG"), ("ga", "Goals against"), ("xga", "xGA"))}
            if not p or None in v.values():
                continue
            table[c[ix["Team"]]] = {"played": int(p), **v, "xg_pg": round(v["xg"] / p, 3), "xga_pg": round(v["xga"] / p, 3),
                                    "goals_pg": round(v["goals"] / p, 3), "ga_pg": round(v["ga"] / p, 3)}
        out[split] = table
    upd = re.search(r"Updated [^<]{3,60}", page)
    return season, (upd.group(0).strip() if upd else None), out


def run(log: Log):
    for slug, name in LEAGUES.items():
        url = URL.format(slug=slug)
        try:
            season, updated, splits = parse(get_text(url, retries=2))
            if "all" not in splits or len(splits["all"]) < 8:
                raise ValueError("no season xG table on the page")
            if season not in ("2026/2027", "2026"):
                raise ValueError(f"page shows season {season}, not 2026/27")
            teams = {}
            for split, tab in splits.items():
                for t, row in tab.items():
                    teams.setdefault(t, {})[split] = row
            write_json(f"xg/{slug}.json", {"meta": {"league": name, "season": season, "updatedText": updated, "source": url,
                       "fetchedAt": now_iso(), "note": "statz.ai's own xG model; last5 = last five league games"}, "teams": teams})
            log.ok(f"xg {slug}", url, len(teams), note=updated)
        except Exception as e:
            log.fail(f"xg {slug}", url, e)
        time.sleep(2)            # be polite: one page every few seconds
