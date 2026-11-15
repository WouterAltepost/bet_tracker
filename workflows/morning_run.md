# Morning Run — Scrape Predictions

## Objective
Scrape the top 5 football predictions from each of 5 sites and write them to the Google Sheet.

## Schedule
Run each morning before matches begin (recommended: 11:00 local time).

## Required Inputs
- Today's date (YYYY-MM-DD format, auto-detected by each script)

## Steps

### 1. Run all 5 scrapers + Claude AI generator
For each site, run its scraper script. Each script saves output to `.tmp/predictions_{site}_{date}.json`.

```bash
python tools/scrape_forebet.py
python tools/scrape_predictz.py
python tools/scrape_onemillion.py
python tools/scrape_vitibet.py
python tools/scrape_freesupertips.py
python tools/generate_claude_predictions.py
```

**On failure:** Each scraper handles its own errors internally. If a site is unreachable or the scrape fails, the script writes a failed-status JSON to `.tmp/predictions_{site}_{date}.json` and exits with code 0. Do not abort the run. Log the error and continue.

`generate_claude_predictions.py` requires `ANTHROPIC_API_KEY` in `.env`. If the API call fails or the response can't be parsed, it writes a failed-status JSON and logs `PRED_FAILED`.

### 2. Write predictions to Google Sheet

```bash
python tools/update_sheet.py --mode=predictions
```

This reads all 6 `.tmp/predictions_{site}_{date}.json` files and inserts rows at the top of the "Predictions" tab (row 2, just below the header) so the most recent date is always visible first. Sites that failed will have "SCRAPE_FAILED" written in their prediction cells.

### 3. Generate daily analysis

```bash
python tools/generate_analysis.py
```

This reads today's predictions and leaderboard data from the sheet, computes consensus across all 6 sites, calls the Anthropic API for a brief qualitative commentary, and writes everything to the "Analysis" tab. The tab is cleared and rewritten each morning.

The analysis includes:
- Leaderboard snapshot (current win % per site)
- Best bets of the day (only 🔒 Lock and ⭐ High confidence picks)
- Full consensus table showing agreement level, confidence rating, and Claude AI contrarian flags
- AI-generated commentary on the day's picks

**On failure:** If the Anthropic API is unavailable, the analysis still runs — it just omits the AI commentary section. This step is non-fatal; a failure here does not affect the predictions already written.

### 4. Generate daily parlay recommendation

```bash
python tools/generate_parlay.py
```

Uses Claude AI with web_search to deeply research today's matches and recommend the single best 3-game parlay. This is the most research-intensive step — Claude may make 15-25 web search calls to investigate team form, H2H, injuries, odds, and more. See `workflows/parlay.md` for full details.

Writes to the "Parlay" tab with two sections:
- **Today's Parlay** (cleared and rewritten each morning): full recommendation with reasoning
- **Parlay Tracker** (appended, never deleted): running historical log with pending results

**On failure:** This step is non-fatal. If the API fails or output can't be parsed, the script exits with code 1 but the morning run continues — predictions and analysis are already written.

## Expected Output
- `.tmp/predictions_{site}_{date}.json` for each of 6 sites (5 scrapers + claude)
- "Predictions" tab in Google Sheet populated with up to 30 new rows for today
- "Analysis" tab in Google Sheet with daily consensus analysis
- "Parlay" tab in Google Sheet with today's 3-game parlay recommendation

## Output File Format (`.tmp/predictions_{site}_{date}.json`)

```json
{
  "date": "2026-02-25",
  "site": "forebet",
  "status": "ok",
  "error": null,
  "predictions": [
    { "home_team": "Arsenal", "away_team": "Chelsea", "prediction": "1" },
    { "home_team": "Real Madrid", "away_team": "Barcelona", "prediction": "X" }
  ]
}
```

On failure: `"status": "failed"`, `"error": "<message>"`, `"predictions": []`

## Prediction Format
- `"1"` = Home win
- `"X"` = Draw
- `"2"` = Away win

## Error Handling
- One site failing does not abort the run — continue to the next scraper
- If `update_sheet.py` fails, check Google API credentials (`token.json`) and retry
- All scraper errors are logged to stdout with the site name and exception message

## Notes
- Playwright must be installed (`playwright install chromium`) for scrapers to work
- Run `playwright install chromium` once if browsers aren't installed yet
- If a site changes its layout and the scraper breaks, check the selector in the relevant `scrape_*.py` script and update it

## Scraper notes (Nov 2026)
- **"Top 5" definition (agreed 2026-11-15):** each site's most confident picks for today, in leagues the results sources can score. Forebet: ranked by the probability it gives its own pick. Vitibet: big leagues first, then |INDEX|. PredictZ and FreeSuperTips publish no confidence number, so their own listing order is used. Women's/youth fixtures are skipped everywhere. Since ESPN was added as a results source, the old league skip lists (English lower leagues, Scotland, MLS, Turkey…) were removed.
- **Vitibet** redesigned its quick-tips page into a livescore-style list (`a.livescore-match-row` with INDEX, 1/X/2 %, tip badge). Top 5 = today's not-yet-started matches sorted by |INDEX| (Vitibet's own "sort by INDEX"), preferring leagues football-data.org can score. Prediction = Vitibet's highest 1/X/2 %. League ids in `data-lid` are API-Football ids.
- **PredictZ** homepage stacks Today's / Tomorrow's / Next Upcoming tips; only the "Today's Tips" block is used.
- **Forebet** homepage can include other days; rows are filtered on `time[itemprop=startDate]`.
- **FreeSuperTips** listing shows only kick-off times; the detail page's "Sunday 15th November" phrase is used to drop matches not played today. Many of its tips are BTTS/goals, so it often yields fewer than 5 1X2 picks.
- **OneMillion** moved its homepage picks behind a paywall ("Premium Prediction → Get Access"); the scraper fails until a replacement is decided. (The old scraper used the lowest bookmaker odds, not the site's own tip.)
- Running the morning sheet write twice no longer duplicates rows: sites that already have rows for the date are skipped.
