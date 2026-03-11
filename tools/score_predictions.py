"""
score_predictions.py — Match predictions to results and score each site.

Reads:
  .tmp/predictions_{site}_{date}.json  (for each of 5 sites)
  .tmp/results_{date}.json

Uses rapidfuzz for fuzzy team name matching (threshold: 60).
Both long name and short name from the results file are tried.

Produces:
  .tmp/scores_{date}.json

Output format:
{
  "date": "2026-02-25",
  "summary": {
    "forebet": { "total": 5, "correct": 3, "unmatched": 0 }
  },
  "details": [
    {
      "site": "forebet",
      "home_team": "Arsenal",
      "away_team": "Chelsea",
      "prediction": "1",
      "result": "1",
      "correct": "Y"
    }
  ]
}

Correct values:
  "Y"          — prediction matched result
  "N"          — prediction did not match result
  "UNMATCHED"  — could not find this match in results (not penalised)
"""

import json
import os
import re
import sys
import unicodedata
from datetime import date

from rapidfuzz import fuzz

SITES = ["forebet", "predictz", "onemillion", "vitibet", "freesupertips", "claude"]
FUZZY_THRESHOLD = 55

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
    # Italian clubs
    "internazionale":             "inter",
    "inter milan":                "inter",
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


def load_predictions(run_date):
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
    if len(words) > 1 and words[-1] in CITY_SUFFIXES:
        n = " ".join(words[:-1])
    # 6. Apply known aliases
    n = TEAM_ALIASES.get(n, n)
    return n


def find_result(pred_home, pred_away, results):
    """
    Find the best matching result for a predicted match using fuzzy string matching.
    Returns (matched_result_or_None, best_score, best_api_home, best_api_away).
    The debug fields are populated even when no match reaches the threshold, so
    callers can log what the closest candidate was.
    """
    best_score = 0
    best_match = None
    best_api_home = ""
    best_api_away = ""

    norm_pred_home = normalize(pred_home)
    norm_pred_away = normalize(pred_away)

    for r in results:
        # Build candidate name strings — try both full and short names
        candidates_home = [r["home_team"], r.get("short_home", "")]
        candidates_away = [r["away_team"], r.get("short_away", "")]

        for cand_home in candidates_home:
            for cand_away in candidates_away:
                if not cand_home or not cand_away:
                    continue
                score_h = fuzz.token_sort_ratio(norm_pred_home, normalize(cand_home))
                score_a = fuzz.token_sort_ratio(norm_pred_away, normalize(cand_away))
                combined = (score_h + score_a) / 2

                if combined > best_score:
                    best_score = combined
                    best_match = r
                    best_api_home = cand_home
                    best_api_away = cand_away

    if best_score >= FUZZY_THRESHOLD:
        return best_match, best_score, best_api_home, best_api_away
    return None, best_score, best_api_home, best_api_away


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date.today()), help="Date in YYYY-MM-DD format")
    args = parser.parse_args()
    run_date = args.date
    print(f"score_predictions.py — date={run_date}")

    all_predictions = load_predictions(run_date)
    results = load_results(run_date)
    print(f"Loaded {len(results)} results from results file")

    details = []
    summary = {site: {"total": 0, "correct": 0, "unmatched": 0} for site in SITES}

    for site in SITES:
        preds = all_predictions.get(site, [])
        print(f"\n[{site}] Scoring {len(preds)} predictions...")

        for pred in preds:
            home = pred["home_team"]
            away = pred["away_team"]
            prediction = pred["prediction"]

            matched, best_score, best_api_home, best_api_away = find_result(home, away, results)

            if matched is None:
                correct = "UNMATCHED"
                result_val = "UNMATCHED"
                summary[site]["unmatched"] += 1
                print(f"  UNMATCHED: {home} vs {away} | closest API: '{best_api_home}' vs '{best_api_away}' (score={best_score:.1f})")
            else:
                result_val = matched["result"] or "?"
                if result_val == "?":
                    correct = "UNMATCHED"
                    summary[site]["unmatched"] += 1
                else:
                    correct = "Y" if prediction == result_val else "N"
                    summary[site]["total"] += 1
                    if correct == "Y":
                        summary[site]["correct"] += 1
                    print(f"  {home} vs {away} | pred={prediction} result={result_val} → {correct}")

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
            pct = s["correct"] / s["total"] * 100
            print(f"  [{site}] {s['correct']}/{s['total']} correct ({pct:.0f}%), {s['unmatched']} unmatched")

    output = {
        "date": run_date,
        "summary": summary,
        "details": details,
    }

    path = os.path.join(TMP_DIR, f"scores_{run_date}.json")
    os.makedirs(TMP_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nScores written to {path}")


if __name__ == "__main__":
    main()
