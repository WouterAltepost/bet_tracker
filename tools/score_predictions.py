"""
score_predictions.py — Match predictions to results and score each site.

Reads:
  predictions for the date — from the Google Sheet "Predictions" tab (default,
  survives server restarts) or from .tmp/predictions_{site}_{date}.json (--source=tmp)
  .tmp/results_{date}.json  (written by fetch_results.py)

Uses rapidfuzz for fuzzy team name matching: BOTH teams must individually
reach TEAM_THRESHOLD (an average lets one strong name drag a wrong pairing through).

Produces:
  .tmp/scores_{date}.json  — {"date", "summary": {site: {total, correct, unmatched, pending}}, "details": [...]}

Correct values:
  "Y" / "N"    — prediction matched / did not match the result
  "UNMATCHED"  — no result found for this match (not penalised)
  "VOID"       — match postponed/cancelled/suspended (not penalised)
  None         — pending: not finished yet, or not found yet but the date is
                 still recent. Left blank in the sheet and retried next run.
A date is "final" FINAL_AFTER_DAYS days after it; only then do unresolved
predictions become UNMATCHED/VOID.
"""

import json
import os
import re
import sys
import unicodedata
from datetime import date, timedelta

from rapidfuzz import fuzz

SITES = ["forebet", "predictz", "onemillion", "vitibet", "freesupertips", "zulubet", "statarea", "claude"]
# Each team must score at least this (see team_score).
# Calibrated on Feb/Mar 2026 data: all true matches scored >= 69 per team; the
# old average-of-two >= 55 rule matched 'Preston v Millwall' to 'Cremonese v AC Milan'.
TEAM_THRESHOLD = 65
FINAL_AFTER_DAYS = 2
VOID_STATUSES = {"POSTPONED", "CANCELLED", "SUSPENDED", "AWARDED"}

# Tokens that mark a different team from the senior men's side. Both names must
# carry the same markers ("Brighton Women" must never match "Brighton").
SIDE_MARKERS = {
    "women", "w", "ladies", "fem", "femenino", "feminine", "frauen", "dames",
    "u17", "u18", "u19", "u20", "u21", "u23", "youth", "ii", "b", "reserves", "res", "jong",
}

# City words that Vitibet (and occasionally other sites) append to club names.
# If the last word of a normalized name is in this set, it is stripped.
CITY_SUFFIXES = {
    "london", "liverpool", "manchester", "birmingham", "leeds", "newcastle",
    "madrid", "barcelona", "milan", "torino", "rome", "roma", "paris",
    "rotterdam", "amsterdam", "lisbon", "lisboa", "porto", "munich",
    "munchen", "berlin", "dortmund", "glasgow", "edinburgh", "seville",
    "sevilla", "valencia", "bilbao", "marseille", "brussels", "bergamo",
    "zagreb", "athens", "bucharest", "prague", "warsaw",
}

# Words that don't identify a club on their own ("Real", "Inter", "United"...).
# A name is never cut down to only these, and they don't earn the subset bonus.
GENERIC_WORDS = {
    "real", "inter", "united", "city", "club", "sporting", "athletic", "atletico",
    "racing", "deportivo", "olympique", "dynamo", "dinamo", "young", "royal",
    "sport", "sports", "town", "county", "rovers", "wanderers", "albion", "de", "la",
}

# Known alternate → canonical name mappings (all lowercase, post-normalize form).
# Expand this dict whenever a new site-specific alias causes persistent UNMATCHED results.
TEAM_ALIASES = {
    # Eastern European / translated names
    "crvena zvezda":              "red star belgrade",
    "red star":                   "red star belgrade",
    # UK abbreviations
    "nott'm forest":              "nottingham forest",
    "nott forest":                "nottingham forest",
    "nott'm":                     "nottingham",
    "wolves":                     "wolverhampton wanderers",
    "spurs":                      "tottenham hotspur",
    "man city":                   "manchester city",
    "man utd":                    "manchester united",
    "man united":                 "manchester united",
    "sheffield utd":              "sheffield united",
    "sheff utd":                  "sheffield united",
    "west brom":                  "west bromwich albion",
    "west ham":                   "west ham united",
    "ham united":                 "west ham united",
    "brighton":                   "brighton hove albion",
    "brighton hove albion":       "brighton hove albion",
    "brighton & hove albion":     "brighton hove albion",
    "brighton and hove albion":   "brighton hove albion",
    # German clubs
    "bayern munich":              "bayern munchen",
    "fc bayern":                  "bayern munchen",
    "m gladbach":                 "borussia monchengladbach",
    "gladbach":                   "borussia monchengladbach",
    "borussia m gladbach":        "borussia monchengladbach",
    "borussia monchengladbach":   "borussia monchengladbach",
    "hertha":                     "hertha berlin",
    # Dutch clubs
    "az alkmaar":                 "az",
    "psv eindhoven":              "psv",
    "nijmegen":                   "nec",
    "nec nijmegen":               "nec",
    # Italian clubs
    "internazionale":             "inter",
    "inter milan":                "inter",
    "internazionale milano":      "inter",
    "inter milano":               "inter",
    "lazio roma":                 "lazio",   # after city strip: "ss lazio roma" → "lazio roma" → "lazio"
    # Spanish clubs
    "atletico de madrid":         "atletico madrid",
    "club atletico de madrid":    "atletico madrid",
    "real betis balompie":        "real betis",
    "rcd espanyol de barcelona":  "espanyol",
    "deportivo alaves":           "alaves",
    "athletic club":              "athletic bilbao",
    "ca osasuna":                 "osasuna",
    # French clubs
    "olympique lyonnais":         "olympique lyon",
    "lyon":                       "olympique lyon",
    "olympique de marseille":     "olympique marseille",
    "marseille":                  "olympique marseille",
    "stade brestois 29":          "brest",
    "stade brestois":             "brest",
    "stade rennais":              "rennes",
    "paris saint germain":        "psg",
    "paris saint-germain":        "psg",
    "paris germain":              "psg",    # after 'saint' stripped by abbrev regex
    # Portuguese clubs
    "sl benfica":                 "benfica",
    "sporting cp":                "sporting",
    "sporting clube de portugal": "sporting",
    # Vitibet city-suffix variants (resolved by CITY_SUFFIXES stripping, but kept as fallback)
    "aston villa birmingham":     "aston villa",
    "juventus torino":            "juventus",
    "atalanta bergamo":           "atalanta",
    "benfica lisboa":             "benfica",
    "everton liverpool":          "everton",
}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(BASE_DIR, ".tmp")


def load_predictions_from_tmp(run_date):
    """Load all site prediction files. Returns dict: site -> predictions list."""
    all_preds = {}
    for site in SITES:
        path = os.path.join(TMP_DIR, f"predictions_{site}_{run_date}.json")
        if not os.path.exists(path):
            print(f"  WARNING: no predictions file for {site} on {run_date}")
            all_preds[site] = []
            continue
        with open(path) as f:
            data = json.load(f)
        if data["status"] == "failed":
            print(f"  [{site}] was SCRAPE_FAILED — skipping")
            all_preds[site] = []
        else:
            all_preds[site] = data["predictions"]
    return all_preds


def load_predictions_from_rows(all_rows, run_date):
    """
    Predictions for run_date from Predictions-tab rows (header included).
    Duplicate rows (same date/site/home/away) are returned once.
    Returns dict: site -> predictions list.
    """
    all_preds = {site: [] for site in SITES}
    seen = set()
    for row in all_rows[1:]:
        if len(row) < 5 or row[0] != run_date or row[1] not in all_preds:
            continue
        if row[2] == "SCRAPE_FAILED" or row[4] not in ("1", "X", "2"):
            continue
        key = tuple(row[:4])
        if key in seen:
            continue
        seen.add(key)
        all_preds[row[1]].append({"home_team": row[2], "away_team": row[3], "prediction": row[4]})
    return all_preds


def load_results(run_date):
    path = os.path.join(TMP_DIR, f"results_{run_date}.json")
    if not os.path.exists(path):
        print(f"ERROR: results file not found: {path}")
        print("Run fetch_results.py first.")
        sys.exit(1)
    with open(path) as f:
        data = json.load(f)
    if data.get("status") == "failed":
        print(f"ERROR: results file has failed status: {data.get('error')}")
        sys.exit(1)
    return data["matches"]


def normalize(name):
    """Normalize team name for fuzzy comparison.

    Steps:
      1. Strip accents (Fenerbahçe → Fenerbahce, München → Munchen)
      2. Lowercase
      3. Normalise punctuation: hyphens, underscores, apostrophes, dots → spaces
      4. Strip club-type abbreviations (FC, AFC, BC, SC, …)
      5. Strip trailing city word if it's a known Vitibet-appended suffix
      6. Collapse whitespace
      7. Apply TEAM_ALIASES for known alternate names
    """
    # 1. Strip accents
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    # 2. Lowercase
    n = n.lower()
    # 3. Normalise punctuation (apostrophes/dots for M'gladbach, M.gladbach etc.)
    n = n.replace("-", " ").replace("_", " ").replace("'", " ").replace(".", " ")
    # 4. Strip common club-type abbreviations that differ across sites
    n = re.sub(
        r"\b(afc|bc|fc|cf|sc|ac|rc|bv|sv|vv|if|fk|sk|uk|as|ss|us|cd|sd|rcd|ud|osc|losc|sbv|vfb|vfl|hsv|rb)\b",
        "",
        n,
    )
    n = " ".join(n.split())  # collapse whitespace
    # 5. Strip trailing city word appended by Vitibet (e.g. "Everton Liverpool" → "Everton")
    words = n.split()
    if len(words) > 1 and words[-1] in CITY_SUFFIXES and not set(words[:-1]) <= GENERIC_WORDS:
        n = " ".join(words[:-1])
    # 6. Apply known aliases
    n = TEAM_ALIASES.get(n, n)
    return n


def side_markers(name):
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return {w for w in words if w in SIDE_MARKERS}


def team_score(pred_raw, cand_raw):
    """
    Similarity 0-100 between a scraped team name and an API team name.
    token_sort_ratio on normalized names, but a name whose words are all contained
    in the other ("Leeds" vs "Leeds United") scores at least 90, unless the shorter
    one is only generic words ("Real" vs "Real Sociedad"). Names with
    different women/youth/reserve markers score 0.
    """
    if side_markers(pred_raw) != side_markers(cand_raw):
        return 0
    a, b = normalize(pred_raw), normalize(cand_raw)
    if not a or not b:
        return 0
    score = fuzz.token_sort_ratio(a, b)
    shorter = min(a.split(), b.split(), key=len)
    if not set(shorter) <= GENERIC_WORDS:
        score = max(score, fuzz.token_set_ratio(a, b) - 10)
    return score


def find_result(pred_home, pred_away, results):
    """
    Find the best matching result for a predicted match using fuzzy string matching.
    Candidates are ranked by their weaker team score (then the average), and are
    accepted only if both teams reach TEAM_THRESHOLD.
    Returns (matched_result_or_None, best_score, best_api_home, best_api_away);
    best_score is the weaker-team score. Debug fields are populated even when
    nothing reaches the threshold, so callers can log the closest candidate.
    """
    best_key = (-1, -1)
    best_match = None
    best_api_home = ""
    best_api_away = ""

    for r in results:
        # Build candidate name strings — try both full and short names
        candidates_home = [r["home_team"], r.get("short_home", "")]
        candidates_away = [r["away_team"], r.get("short_away", "")]

        for cand_home in candidates_home:
            for cand_away in candidates_away:
                if not cand_home or not cand_away:
                    continue
                score_h = team_score(pred_home, cand_home)
                score_a = team_score(pred_away, cand_away)
                key = (min(score_h, score_a), (score_h + score_a) / 2)

                if key > best_key:
                    best_key = key
                    best_match = r
                    best_api_home = cand_home
                    best_api_away = cand_away

    best_score = best_key[0]
    if best_score >= TEAM_THRESHOLD:
        return best_match, best_score, best_api_home, best_api_away
    return None, best_score, best_api_home, best_api_away


def is_final(run_date, today=None):
    today = today or date.today()
    return date.fromisoformat(run_date) <= today - timedelta(days=FINAL_AFTER_DAYS)


def score(all_predictions, results, final):
    """Score every prediction. Returns (summary, details); see module docstring."""
    details = []
    summary = {site: {"total": 0, "correct": 0, "unmatched": 0, "pending": 0} for site in SITES}

    for site in SITES:
        preds = all_predictions.get(site, [])
        if preds:
            print(f"\n[{site}] Scoring {len(preds)} predictions...")

        for pred in preds:
            home, away, prediction = pred["home_team"], pred["away_team"], pred["prediction"]
            matched, best_score, best_api_home, best_api_away = find_result(home, away, results)
            status = (matched or {}).get("status", "FINISHED")

            if matched is not None and matched.get("result"):
                result_val = matched["result"]
                correct = "Y" if prediction == result_val else "N"
                summary[site]["total"] += 1
                summary[site]["correct"] += correct == "Y"
                print(f"  {home} vs {away} | pred={prediction} result={result_val} → {correct}")
            elif not final:
                result_val, correct = None, None
                summary[site]["pending"] += 1
                why = f"status {status}" if matched is not None else f"not found yet (closest '{best_api_home}' vs '{best_api_away}', {best_score:.0f})"
                print(f"  PENDING: {home} vs {away} — {why}")
            elif matched is not None and status in VOID_STATUSES:
                result_val, correct = status, "VOID"
                summary[site]["unmatched"] += 1
                print(f"  VOID: {home} vs {away} — {status}")
            else:
                result_val, correct = "UNMATCHED", "UNMATCHED"
                summary[site]["unmatched"] += 1
                print(f"  UNMATCHED: {home} vs {away} | closest API: '{best_api_home}' vs '{best_api_away}' (score={best_score:.1f})")

            details.append({
                "site": site,
                "home_team": home,
                "away_team": away,
                "prediction": prediction,
                "result": result_val,
                "correct": correct,
            })

        s = summary[site]
        if s["total"] > 0:
            print(f"  [{site}] {s['correct']}/{s['total']} correct ({s['correct'] / s['total'] * 100:.0f}%), "
                  f"{s['unmatched']} unmatched, {s['pending']} pending")

    return summary, details


def write_scores(run_date, summary, details):
    os.makedirs(TMP_DIR, exist_ok=True)
    path = os.path.join(TMP_DIR, f"scores_{run_date}.json")
    with open(path, "w") as f:
        json.dump({"date": run_date, "summary": summary, "details": details}, f, indent=2, ensure_ascii=False)
    return path


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date.today()), help="Date in YYYY-MM-DD format")
    parser.add_argument("--source", choices=["sheet", "tmp"], default="sheet",
                        help="Where to read the predictions from (default: the Google Sheet)")
    args = parser.parse_args()
    run_date = args.date
    print(f"score_predictions.py — date={run_date}, source={args.source}")

    if args.source == "sheet":
        from update_sheet import PREDICTIONS_TAB, get_service, read_tab
        all_predictions = load_predictions_from_rows(read_tab(get_service(), PREDICTIONS_TAB), run_date)
    else:
        all_predictions = load_predictions_from_tmp(run_date)
    results = load_results(run_date)
    print(f"Loaded {len(results)} results from results file")

    summary, details = score(all_predictions, results, is_final(run_date))
    print(f"\nScores written to {write_scores(run_date, summary, details)}")


if __name__ == "__main__":
    main()
