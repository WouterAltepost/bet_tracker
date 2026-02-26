# Evening Run — Fetch Results & Score Predictions

## Objective
Fetch real match results from football-data.org, score each site's morning predictions, and update the Google Sheet with results and win percentages.

## Schedule
Run each evening after matches are complete (recommended: 23:00 local time).

## Required Inputs
- Today's date (YYYY-MM-DD format, auto-detected)
- `.tmp/predictions_{site}_{date}.json` files from the morning run
- `FOOTBALL_DATA_API_KEY` in `.env`

## Steps

### 1. Fetch today's match results

```bash
python tools/fetch_results.py
```

Calls the football-data.org API for all matches played today across tracked competitions. Saves results to `.tmp/results_{date}.json`.

**If this step fails** (API down, rate limit hit): do not proceed to scoring. Investigate the error, wait if rate-limited, and retry. The morning predictions are safe in `.tmp/`.

### 2. Score predictions against results

```bash
python tools/score_predictions.py
```

Reads all `.tmp/predictions_{site}_{date}.json` files and `.tmp/results_{date}.json`. Matches teams using fuzzy string matching (threshold: 80 via `rapidfuzz`). Saves scored output to `.tmp/scores_{date}.json`.

Predictions that cannot be matched to a result are marked as `"UNMATCHED"` — they are not counted as correct or incorrect and do not penalise the site's score.

### 3. Update Google Sheet with results and leaderboard

```bash
python tools/update_sheet.py --mode=results
```

- Finds today's rows in the "Predictions" tab and fills in "Result" and "Correct" columns
- Recalculates and overwrites the entire "Leaderboard" tab from all historical data in "Predictions"

## Expected Output
- `.tmp/results_{date}.json` — all matches played today with outcomes
- `.tmp/scores_{date}.json` — per-site correct/total counts for today
- "Predictions" tab: Result and Correct columns filled in for today's rows
- "Leaderboard" tab: updated running win % per site

## Output File Formats

**`.tmp/results_{date}.json`**
```json
{
  "date": "2026-02-25",
  "matches": [
    {
      "home_team": "Arsenal FC",
      "away_team": "Chelsea FC",
      "result": "1",
      "home_score": 2,
      "away_score": 0
    }
  ]
}
```

**`.tmp/scores_{date}.json`**
```json
{
  "date": "2026-02-25",
  "scores": {
    "forebet":       { "total": 5, "correct": 3, "unmatched": 0 },
    "predictz":      { "total": 5, "correct": 2, "unmatched": 1 },
    "onemillion":    { "total": 5, "correct": 3, "unmatched": 0 },
    "vitibet":       { "total": 5, "correct": 2, "unmatched": 0 },
    "freesupertips": { "total": 5, "correct": 1, "unmatched": 2 }
  }
}
```

## Error Handling
- If `fetch_results.py` fails: stop, do not score, retry later
- If a prediction can't be matched to a result: mark "UNMATCHED", do not penalise
- If `update_sheet.py` fails: `.tmp/` files are intact — fix the error and rerun step 3 only
- If morning predictions are missing for a site (SCRAPE_FAILED): those rows already say "SCRAPE_FAILED" in the sheet; skip them during scoring

## Notes on Team Name Matching
football-data.org uses official long-form names (e.g. "Manchester City FC"). Scraped names vary by site (e.g. "Man City", "Manchester C."). `score_predictions.py` uses `rapidfuzz.fuzz.token_sort_ratio` with a threshold of 80. If unmatched counts are consistently high for a site, consider adding a team name alias map to `score_predictions.py`.

## Tracked Competitions (football-data.org free tier)
- Premier League (PL)
- Championship (ELC)
- La Liga (PD)
- Bundesliga (BL1)
- Serie A (SA)
- Ligue 1 (FL1)
- Eredivisie (DED)
- Primeira Liga (PPL)
- Champions League (CL)
- Europa League (EL)

If a prediction site consistently picks matches from leagues not listed above, the match will be unmatched. This is expected behaviour on the free API tier.
