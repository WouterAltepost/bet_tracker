"""
pick_utils.py — Shared helpers for scrapers that rank a site's own picks.

top_scorable(): keep only picks the evening run will be able to score, by
checking them against today's ESPN fixture list (the same matcher the evening
run uses). This is how "top 5 = the site's most confident picks in leagues we
can score" is applied for sites that list hundreds of matches across obscure
leagues.
"""

from fetch_results import fetch_espn
from score_predictions import find_result

# Fixtures we never pick: results sources don't separate these from the senior men's side reliably.
SKIP_KEYWORDS = ("women", "femen", "frauen", "ladies", "u17", "u18", "u19", "u20", "u21", "u23",
                 "youth", "reserve")


def is_skipped(*texts):
    blob = " ".join(t or "" for t in texts).lower()
    return any(k in blob for k in SKIP_KEYWORDS)


def top_scorable(candidates, run_date, site, n=5):
    """candidates: best-first list of dicts with home_team / away_team.
    Returns the first n that appear in ESPN's fixtures around run_date.
    If ESPN can't be reached, returns the first n unfiltered (logged)."""
    try:
        fixtures = fetch_espn(run_date)
    except Exception as e:
        print(f"  [{site}] WARNING: ESPN fixture check unavailable ({e}) — using the site's ranking unfiltered")
        return candidates[:n]

    picked, skipped = [], 0
    for c in candidates:
        match, score, _, _ = find_result(c["home_team"], c["away_team"], fixtures)
        if match is None:
            skipped += 1
            continue
        picked.append(c)
        if len(picked) == n:
            break
    print(f"  [{site}] {len(picked)} scorable pick(s) chosen; {skipped} higher-ranked match(es) skipped "
          f"(not in the results sources)")
    return picked
