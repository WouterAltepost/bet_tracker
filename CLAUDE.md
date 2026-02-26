# Agent Instructions

You're working inside the **WAT framework** (Workflows, Agents, Tools). This architecture separates concerns so that probabilistic AI handles reasoning while deterministic code handles execution. That separation is what makes this system reliable.

## Project: Football Prediction Tracker

This system automates the tracking of football match predictions from 5 websites, compares them against real results, and maintains a live Google Sheet with win percentage statistics per site.

**The two daily runs:**
- **Morning run** — Scrape today's top 5 predictions from each site and write them to Google Sheets
- **Evening run** — Fetch real match results, score each site's predictions, update win percentages in Google Sheets

**The 5 prediction sites:**
- Forebet: https://www.forebet.com/
- PredictZ: https://www.predictz.com/
- OneMillion: https://onemillionpredictions.com/
- Vitibet: https://www.vitibet.com/index.php?clanek=quicktips&sekce=fotbal&lang=en
- FreeSuperTips: https://www.freesupertips.com/predictions/

**Scraping approach:** All sites require Playwright (headless browser) as they block standard HTTP requests. Each site has its own dedicated scraper tool. Always grab whatever the site surfaces as their top 5 picks of the day.

**External APIs:**
- `football-data.org` — Free API for fetching real match results. API key stored in `.env` as `FOOTBALL_DATA_API_KEY`
- Google Sheets API — For reading and writing the tracker spreadsheet. Credentials stored in `credentials.json` and `token.json` (both gitignored)

## The WAT Architecture

**Layer 1: Workflows (The Instructions)**
- Markdown SOPs stored in `workflows/`
- Each workflow defines the objective, required inputs, which tools to use, expected outputs, and how to handle edge cases
- Written in plain language, the same way you'd brief someone on your team

**Layer 2: Agents (The Decision-Maker)**
- This is your role. You're responsible for intelligent coordination.
- Read the relevant workflow, run tools in the correct sequence, handle failures gracefully, and ask clarifying questions when needed
- You connect intent to execution without trying to do everything yourself
- Example: To run the morning scrape, read `workflows/morning_run.md`, then execute each scraper tool in sequence, handle any failures per site without stopping the whole run, then call `tools/update_sheet.py` to write results

**Layer 3: Tools (The Execution)**
- Python scripts in `tools/` that do the actual work
- API calls, data transformations, file operations, Google Sheets updates
- Credentials and API keys are stored in `.env`
- These scripts are consistent, testable, and fast

**Why this matters:** When AI tries to handle every step directly, accuracy drops fast. If each step is 90% accurate, you're down to 59% success after just five steps. By offloading execution to deterministic scripts, you stay focused on orchestration and decision-making where you excel.

## How to Operate

**1. Look for existing tools first**
Before building anything new, check `tools/` based on what your workflow requires. Only create new scripts when nothing exists for that task.

**2. Learn and adapt when things fail**
When you hit an error:
- Read the full error message and trace
- Fix the script and retest (if it uses paid API calls or credits, check with me before running again)
- Document what you learned in the workflow (rate limits, timing quirks, unexpected behavior)
- Example: A site updates its HTML structure and the scraper breaks — inspect the new structure, update the selector in the tool, verify it works, update the workflow with a note about the change

**3. Handle per-site scraper failures gracefully**
If one site's scraper fails, log the error and continue with the remaining sites. Never let a single site failure abort the entire morning or evening run. Write `"SCRAPE_FAILED"` to that site's cells in the sheet so it's visible.

**4. Keep workflows current**
Workflows should evolve as you learn. When you find better methods, discover constraints, or encounter recurring issues, update the workflow. That said, don't create or overwrite workflows without asking unless I explicitly tell you to.

## The Self-Improvement Loop

Every failure is a chance to make the system stronger:
1. Identify what broke
2. Fix the tool
3. Verify the fix works
4. Update the workflow with the new approach
5. Move on with a more robust system

This loop is how the framework improves over time.

## File Structure

**What goes where:**
- **Deliverables**: All prediction data and results live in Google Sheets — that is the single source of truth
- **Intermediates**: Temporary scraped data stored in `.tmp/` before being written to the sheet

**Directory layout:**
```
.tmp/                   # Temporary scraped data and intermediate files. Regenerated as needed.
tools/
  scrape_forebet.py     # Playwright scraper for Forebet
  scrape_predictz.py    # Playwright scraper for PredictZ
  scrape_onemillion.py  # Playwright scraper for OneMillion
  scrape_vitibet.py     # Playwright scraper for Vitibet
  scrape_freesupertips.py # Playwright scraper for FreeSuperTips
  fetch_results.py      # Fetches real match results from football-data.org
  score_predictions.py  # Compares predictions to results, calculates scores
  update_sheet.py       # Reads/writes data to Google Sheets
workflows/
  morning_run.md        # SOP for daily predictions scrape
  evening_run.md        # SOP for results fetching, scoring, and sheet update
.env                    # API keys (NEVER store secrets anywhere else)
credentials.json        # Google OAuth credentials (gitignored)
token.json              # Google OAuth token (gitignored)
```

**Core principle:** Local files are just for processing. The Google Sheet is the live source of truth. Everything in `.tmp/` is disposable.

## Bottom Line

You sit between what I want (workflows) and what actually gets done (tools). Your job is to read instructions, make smart decisions, call the right tools, recover from errors per site without aborting the full run, and keep improving the system as you go.

Stay pragmatic. Stay reliable. Keep learning.