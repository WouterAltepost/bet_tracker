# Evening Run — Fetch Results & Score Predictions

## Objective
Fetch real match results (football-data.org + ESPN), score each site's morning predictions, and update the Google Sheet with results and win percentages.

## Schedule
Run each evening after matches are complete (recommended: 23:00 local time).

## Required Inputs
- Today's date (YYYY-MM-DD format, auto-detected)
- The "Predictions" tab of the sheet (written by the morning run)
- `FOOTBALL_DATA_API_KEY` in `.env` (ESPN needs no key)
- Google access: `GOOGLE_SERVICE_ACCOUNT_JSON` (preferred) or `GOOGLE_TOKEN_JSON` — see "Google access" below

## Steps

### One command (normal use)

```bash
python tools/run_evening.py            # add --dry-run to preview, --date=YYYY-MM-DD for one day
```

This is what `POST /run-evening` runs. It:
1. Reads the "Predictions" tab and finds every date (up to today) that still has blank Result/Correct cells — so a missed evening is caught up automatically next time.
2. For each date: fetches results (`fetch_results.get_results`, window date-1..date+1, pausing ~7s between dates for the free-tier rate limit), scores the sheet's predictions (`score_predictions.score`) and writes Result/Correct (`update_sheet.write_results`).
3. Rebuilds the "Leaderboard" tab once.

Predictions come from the **sheet**, not `.tmp/`, so a server restart between morning and evening loses nothing.

Pending logic: a prediction whose match isn't finished yet (or isn't found yet) stays blank and is retried. Two days after the date (`FINAL_AFTER_DAYS`), unresolved predictions become `UNMATCHED`, and postponed/cancelled matches become `VOID`; neither counts toward win %.

`--rescore` re-checks rows that already have a value (use with `--dry-run` first: it prints every value that would change).

### Individual steps (debugging)

```bash
python tools/fetch_results.py --date=YYYY-MM-DD        # .tmp/results_{date}.json
python tools/score_predictions.py --date=YYYY-MM-DD    # reads the sheet; --source=tmp for .tmp files
python tools/update_sheet.py --mode=results --date=YYYY-MM-DD
```

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
- If fetching results fails for a date: that date is skipped and stays pending; the next run retries it
- If a prediction can't be matched to a result: mark "UNMATCHED", do not penalise
- If the sheet write fails: fix the error and rerun `run_evening.py` — it only fills blanks, so reruns are safe
- If morning predictions are missing for a site (SCRAPE_FAILED): those rows already say "SCRAPE_FAILED" in the sheet; skip them during scoring

## Notes on Team Name Matching
football-data.org uses official long-form names (e.g. "Manchester City FC"). Scraped names vary by site (e.g. "Man City", "Manchester C."). `score_predictions.py` uses `rapidfuzz.fuzz.token_sort_ratio` on normalized names, and BOTH teams must individually reach `TEAM_THRESHOLD` (65). Do not go back to averaging the two scores: in Mar 2026 an average ≥ 55 matched "Preston v Millwall" to "Cremonese v AC Milan". If unmatched counts are consistently high for a site, add entries to `TEAM_ALIASES` in `score_predictions.py`.

## Results sources (Nov 2026)
`fetch_results.get_results` merges two sources; if one fails the other is still used.
1. **football-data.org** free tier — PL, ELC, PD, BL1, SA, FL1, DED, PPL, BSA, CL, CLI, WC, EC. Listed first, so it wins ties.
2. **ESPN public scoreboard** (`site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard?dates=YYYYMMDD&limit=1000`, no key) — ~150–250 matches/day: Europa/Conference League, Nations League, domestic cups, English lower leagues, Scotland, MLS, Liga MX, Turkey, Greece, Saudi, Brazil, Argentina, Chile, Australia, China… Not covered: K-League and some very small leagues. Unofficial API — if ESPN changes it, football-data still works and the run logs a warning.

Rules both sources follow:
- The result is the **90-minute** result (what 1X2 settles on). football-data: `score.regularTime` when `duration` isn't REGULAR. ESPN: for `STATUS_FINAL_AET` / `STATUS_FINAL_PEN` the 90-minute score is rebuilt from the goal list (`details`, minute ≤ 90); if the goals don't add up to the final score the result is left unknown rather than guessed.
- ESPN names women's/youth teams exactly like the men's side ("Arsenal"), so `fetch_results.py` appends "Women"/"U21"… based on the competition slug; the matcher requires markers to agree.
- Scrapers skip women's/youth fixtures entirely.

Adding ESPN on 2026-11-15 turned 81 March rows from UNMATCHED into Y/N (no existing Y/N changed).

## Google access
Preferred: a **service account** (never expires, no browser login). `update_sheet.get_service()` uses env `GOOGLE_SERVICE_ACCOUNT_JSON` (Railway) or `service_account.json` in the project root (local, gitignored) when present; otherwise it falls back to the personal OAuth token (`GOOGLE_TOKEN_JSON` / `token.json`). The OAuth app is in Google "Testing" mode, so that token dies every 7 days — headless runs then fail with a clear error instead of hanging on a browser login.

One-time setup: Google Cloud console → same project as `credentials.json` → IAM & Admin → Service Accounts → Create (no roles needed) → Keys → Add key → JSON. Share the sheet with the account's `…@….iam.gserviceaccount.com` address as Editor. Put the JSON file contents in Railway variable `GOOGLE_SERVICE_ACCOUNT_JSON` and save the file locally as `service_account.json`.

## Notes (Nov 2026)
- The Mar 2026 sheet had 122 never-scored rows (missed evening runs, and duplicate rows from a repeated morning run on 2026-03-13 — the old writer only filled the first copy). Backfilled on 2026-11-15 with `run_evening.py`. Duplicates are now filled together and counted once in the leaderboard.
- football-data.org's `dateTo` appears exclusive; `fetch_results.py` asks for date-1 .. date+2 to fully include date+1.
- Matches are often listed by a site a day before they're played (e.g. Barcelona v Newcastle logged 03-17, played 03-18 UTC); the ±1 day window catches these.
- Side markers (Women, U21, II, B…) must agree on both names — "Brighton Women" once matched the men's Brighton v Liverpool.
