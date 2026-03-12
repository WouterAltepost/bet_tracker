# Parlay — Daily 3-Game Parlay Recommendation

## Objective
Generate a single high-quality 3-game parlay recommendation each morning using Claude AI with extensive web research. This is the highest-effort step in the morning run — accuracy matters more than speed.

## Schedule
Runs as the final step of the morning run, after `generate_analysis.py`.

## Required Inputs
- Today's date (YYYY-MM-DD, auto-detected)
- `ANTHROPIC_API_KEY` in `.env`
- Today's predictions already written to the Predictions tab (for context)

## How It Works

### 1. Research Phase (Agentic Loop with Web Search)
Claude uses the `web_search` tool in a multi-turn loop (up to 30 iterations) to research today's matches. The research process:

1. **Discover fixtures** — Search for all matches scheduled today across major European leagues
2. **Deep-dive candidates** — For each promising match (8-10+ candidates), research:
   - Team form (last 5-10 matches, goals scored/conceded)
   - Head-to-head record
   - Key player availability, injuries, suspensions
   - Home/away performance stats
   - League position and momentum
   - xG data if available
   - Betting odds and line movement
   - Tactical/managerial context
3. **Select final 3** — Choose the 3 matches with the highest confidence edge

### 2. Bet Type Selection
The parlay is NOT limited to 1X2. Claude selects from:
- Home Win / Draw / Away Win (1X2)
- Both Teams to Score (BTTS Yes/No)
- Over/Under 2.5 goals
- Over/Under 1.5 goals
- Asian handicap
- Team to win to nil
- First half result
- Player to score anytime
- Player shots on target

For each leg, Claude explains why that specific bet type was chosen over alternatives.

### 3. Output
Each leg includes:
- Match (Home vs Away)
- Bet type and selection
- 3-5 bullet points of specific research findings
- Confidence level (High / Very High / Near Certain)

Plus an overall parlay summary and confidence rating.

### 4. Quality Gate
If Claude cannot find 3 high-confidence picks, it returns a 2-leg parlay instead of forcing a weak third pick. The `overall_confidence` field explains why.

## Sheet Layout (Parlay Tab)

**Section 1 — Today's Parlay** (cleared and rewritten each morning):
- Date and overall confidence
- Each leg with full details: bet type, selection, reasoning
- Parlay summary

**Section 2 — Parlay Tracker** (appended, never deleted):
| Date | Leg 1 | Leg 2 | Leg 3 | Leg 1 Result | Leg 2 Result | Leg 3 Result | Parlay Result | Notes |
Results start as "Pending" — updated manually.

## Running the Tool

```bash
python tools/generate_parlay.py [--date=YYYY-MM-DD]
```

## Error Handling
- If `ANTHROPIC_API_KEY` is missing: exits with code 1 (fatal — cannot run without API)
- If the API fails mid-research: exits with code 1 and logs the error
- If Claude's output can't be parsed: exits with code 1 and logs the raw response
- This step is non-fatal to the overall morning run — prediction data is already written

## Cost Notes
This step makes many API calls (potentially 15-25 web search iterations). It uses `claude-sonnet-4-6` to balance quality and cost. Each run costs roughly $0.10-0.30 depending on research depth.

## Logging
The full research process is logged to stdout (visible in Railway deploy logs):
- Each API call iteration number
- Stop reasons and block types
- Final parlay with all reasoning
- Sheet write confirmation
