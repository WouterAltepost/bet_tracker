"""
generate_parlay.py — Generate a daily 3-game parlay recommendation using Claude AI.

Usage:
  python tools/generate_parlay.py [--date=YYYY-MM-DD]

Uses Claude with web_search to deeply research today's football matches,
then recommends the single best 3-game parlay. Writes the recommendation
to a "Parlay" tab in Google Sheets with a running historical tracker.

Requires ANTHROPIC_API_KEY in .env or shell environment.
"""

import argparse
import json
import os
import re
import sys
import traceback
from datetime import date

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from update_sheet import (
    SPREADSHEET_ID,
    get_service,
    get_or_create_tab,
)

PARLAY_TAB = "Parlay"
MODEL = "claude-sonnet-4-6"
MAX_ITERATIONS = 30
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}

TRACKER_HEADER = [
    "Date", "Leg 1", "Leg 2", "Leg 3",
    "Leg 1 Result", "Leg 2 Result", "Leg 3 Result",
    "Parlay Result", "Notes",
]
TRACKER_SEPARATOR = ["─── Parlay Tracker ───"]

PROMPT_TEMPLATE = """\
Today is {date}.

You are an expert football betting analyst. Your task is to construct the SINGLE BEST \
3-game parlay for today's football matches. Quality and accuracy matter more than anything — \
take your time and research thoroughly.

## Research Process

1. FIRST, search for ALL football matches scheduled for today across these leagues/competitions:
   Premier League, Championship, La Liga, Bundesliga, Serie A, Ligue 1, \
   Champions League, Europa League, Conference League, Eredivisie, Primeira Liga.

2. For EACH promising match (research at least 8-10 candidates), investigate:
   - Current team form (last 5-10 matches, goals scored/conceded)
   - Head-to-head record between the two teams
   - Key player availability, injuries, and suspensions
   - Home/away performance statistics this season
   - League position, points, and recent momentum
   - Expected goals (xG) data if available
   - Betting market odds and line movement
   - Any relevant tactical or managerial context

3. After researching all candidates, select the 3 matches you are MOST confident about.

## Bet Types Available

You are NOT limited to 1X2. Choose the bet type that gives you the highest edge:
- Home Win / Draw / Away Win (1X2)
- Both Teams to Score (BTTS Yes/No)
- Over/Under 2.5 goals
- Over/Under 1.5 goals
- Asian handicap (specify the line)
- A team to win to nil
- First half result
- Player to score anytime (only if you found strong evidence)

For each leg, explain WHY you chose that specific bet type over alternatives.

## Context from prediction sites

These matches were already predicted today by 6 prediction sites (for reference):
{predictions_context}

You may include these matches or choose entirely different ones — pick whatever \
gives the strongest parlay.

## Output Format

Return your final answer as ONLY a JSON code block (no other text after it):

```json
{{
  "legs": [
    {{
      "home_team": "Team A",
      "away_team": "Team B",
      "bet_type": "Over 2.5 Goals",
      "selection": "Over 2.5",
      "confidence": "Very High",
      "reasoning": [
        "Team A averaging 2.1 goals per game at home this season",
        "H2H: last 5 meetings produced 3+ goals in 4 of them",
        "Team B conceding 1.8 goals per game away from home",
        "Both teams need points — expect open, attacking game"
      ],
      "why_this_bet_type": "Over 2.5 is safer than backing either team outright given Team B's ability to score on the counter"
    }},
    {{
      "home_team": "Team C",
      "away_team": "Team D",
      "bet_type": "Home Win (1)",
      "selection": "Team C to win",
      "confidence": "High",
      "reasoning": [
        "Reason 1",
        "Reason 2",
        "Reason 3"
      ],
      "why_this_bet_type": "Explanation"
    }},
    {{
      "home_team": "Team E",
      "away_team": "Team F",
      "bet_type": "BTTS Yes",
      "selection": "Both Teams to Score - Yes",
      "confidence": "High",
      "reasoning": [
        "Reason 1",
        "Reason 2",
        "Reason 3"
      ],
      "why_this_bet_type": "Explanation"
    }}
  ],
  "parlay_summary": "Brief explanation of why these 3 games work well together as a parlay",
  "overall_confidence": "High",
  "games_researched": 12
}}
```

## Rules
- confidence must be "High", "Very High", or "Near Certain"
- If you cannot find 3 legs you are genuinely confident about, return only 2 legs \
  and set overall_confidence to explain why
- reasoning must contain 3-5 bullet points of SPECIFIC research findings (stats, not vague claims)
- Use common English team names (e.g. "Real Madrid", "PSG", "Man City")
- Do NOT include any text after the closing ``` of the JSON block
- Research thoroughly — make as many web searches as you need
"""


def build_predictions_context(service, run_date):
    """Read today's predictions from the sheet to provide context."""
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range=f"Predictions!A:E"
        ).execute()
        rows = result.get("values", [])
        matches = set()
        for row in rows[1:]:
            if len(row) >= 5 and row[0] == run_date and row[2] != "SCRAPE_FAILED":
                matches.add(f"{row[2]} vs {row[3]}")
        if matches:
            return "\n".join(f"- {m}" for m in sorted(matches))
    except Exception as e:
        print(f"  [parlay] Could not read predictions context: {e}")
    return "(No predictions available yet)"


def run_agentic_loop(client, run_date, predictions_context):
    """Run the multi-turn agentic loop with web_search for deep research."""
    prompt = PROMPT_TEMPLATE.format(
        date=run_date,
        predictions_context=predictions_context,
    )
    messages = [{"role": "user", "content": prompt}]

    for iteration in range(MAX_ITERATIONS):
        print(f"  [parlay] API call #{iteration + 1}...", flush=True)
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            tools=[WEB_SEARCH_TOOL],
            messages=messages,
            betas=["web-search-2025-03-05"],
        )

        block_types = [getattr(b, "type", type(b).__name__) for b in response.content]
        print(f"  [parlay] stop_reason={response.stop_reason}, blocks={block_types}", flush=True)

        if response.stop_reason == "end_turn":
            final_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_text += block.text
            print(f"  [parlay] Done after {iteration + 1} API call(s)", flush=True)
            return final_text

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if getattr(block, "type", None) == "tool_result":
                tool_results.append(block)

        if not tool_results:
            print(f"  [parlay] WARNING: stop_reason={response.stop_reason} but no tool_result blocks", flush=True)
            break

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Agentic loop did not complete within {MAX_ITERATIONS} iterations")


def extract_json(text):
    """Extract and parse the JSON parlay block from Claude's response."""
    match = re.search(r"```json\s*([\s\S]+?)\s*```", text)
    if not match:
        raise ValueError("No ```json ... ``` block found in response")
    return json.loads(match.group(1))


def validate_parlay(data):
    """Validate the parsed parlay data."""
    if not isinstance(data, dict):
        raise ValueError(f"Expected a dict, got {type(data).__name__}")
    if "legs" not in data:
        raise ValueError("Missing 'legs' key")

    legs = data["legs"]
    if not isinstance(legs, list) or len(legs) < 2 or len(legs) > 3:
        raise ValueError(f"Expected 2-3 legs, got {len(legs) if isinstance(legs, list) else 'non-list'}")

    for i, leg in enumerate(legs):
        for key in ("home_team", "away_team", "bet_type", "selection", "confidence", "reasoning"):
            if key not in leg:
                raise ValueError(f"Leg {i+1} missing key '{key}'")
        if leg["confidence"] not in ("High", "Very High", "Near Certain"):
            raise ValueError(f"Leg {i+1} has invalid confidence '{leg['confidence']}'")
        if not isinstance(leg["reasoning"], list) or len(leg["reasoning"]) < 2:
            raise ValueError(f"Leg {i+1} reasoning must be a list of 2+ items")

    return data


# ---------------------------------------------------------------------------
# Sheet writing
# ---------------------------------------------------------------------------

def read_existing_tracker(service):
    """Read existing tracker rows from the Parlay tab (everything after the separator)."""
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range=f"{PARLAY_TAB}!A:I"
        ).execute()
        rows = result.get("values", [])
    except Exception:
        return []

    # Find the tracker separator row
    tracker_rows = []
    in_tracker = False
    for row in rows:
        if row and row[0].startswith("─── Parlay Tracker"):
            in_tracker = True
            continue
        if in_tracker:
            # Skip the header row (Date, Leg 1, ...)
            if row and row[0] == "Date":
                continue
            if row and len(row) >= 1 and row[0]:  # non-empty data row
                tracker_rows.append(row)

    return tracker_rows


def write_parlay(service, run_date, parlay_data):
    """Write today's parlay + historical tracker to the Parlay tab."""
    sheet_id = get_or_create_tab(service, PARLAY_TAB)

    # Read existing tracker data before clearing
    existing_tracker = read_existing_tracker(service)

    # Clear entire tab
    service.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{PARLAY_TAB}!A:Z",
    ).execute()

    rows = []
    legs = parlay_data["legs"]

    # --- Section 1: Today's Parlay ---
    rows.append([f"🎯 Daily Parlay — {run_date}"])
    rows.append([f"Overall Confidence: {parlay_data.get('overall_confidence', 'N/A')}",
                 f"Games Researched: {parlay_data.get('games_researched', 'N/A')}"])
    rows.append([])

    for i, leg in enumerate(legs):
        match_name = f"{leg['home_team']} vs {leg['away_team']}"
        rows.append([f"Leg {i+1}: {match_name}"])
        rows.append(["Bet Type", leg["bet_type"]])
        rows.append(["Selection", leg["selection"]])
        rows.append(["Confidence", leg["confidence"]])
        rows.append(["Why This Bet", leg.get("why_this_bet_type", "")])
        rows.append(["Reasoning:"])
        for reason in leg["reasoning"]:
            rows.append([f"  • {reason}"])
        rows.append([])

    # Parlay summary
    if parlay_data.get("parlay_summary"):
        rows.append(["📝 Parlay Summary"])
        rows.append([parlay_data["parlay_summary"]])
        rows.append([])

    # --- Separator ---
    rows.append(TRACKER_SEPARATOR)
    rows.append([])

    # --- Section 2: Tracker ---
    rows.append(TRACKER_HEADER)
    tracker_header_row = len(rows) - 1  # 0-indexed

    # Build today's tracker row
    leg_summaries = []
    for leg in legs:
        leg_summaries.append(f"{leg['home_team']} vs {leg['away_team']} — {leg['bet_type']}: {leg['selection']}")
    # Pad to 3 legs if only 2
    while len(leg_summaries) < 3:
        leg_summaries.append("—")

    today_row = [
        run_date,
        leg_summaries[0],
        leg_summaries[1],
        leg_summaries[2],
        "Pending", "Pending", "Pending",
        "Pending",
        "",
    ]

    # Check if today already exists in tracker
    today_exists = any(r[0] == run_date for r in existing_tracker if r)

    if today_exists:
        # Replace today's row
        for r in existing_tracker:
            if r and r[0] == run_date:
                rows.append(today_row)
            else:
                rows.append(r)
    else:
        # Add today's row at top, then existing history
        rows.append(today_row)
        for r in existing_tracker:
            rows.append(r)

    # Write all rows
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{PARLAY_TAB}!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()
    print(f"  Parlay written to '{PARLAY_TAB}' tab ({len(rows)} rows)")

    # --- Formatting ---
    format_parlay_tab(service, sheet_id, rows, tracker_header_row)


def format_parlay_tab(service, sheet_id, rows, tracker_header_row):
    """Apply formatting to the Parlay tab."""
    requests = []

    for i, row in enumerate(rows):
        if not row or not isinstance(row[0], str):
            continue

        cell_text = row[0]

        # Main title row
        if cell_text.startswith("🎯"):
            requests.append(_header_format(sheet_id, i, {"red": 0.15, "green": 0.15, "blue": 0.25},
                                           font_size=13, white_text=True))

        # Leg headers
        elif cell_text.startswith("Leg ") and ":" in cell_text and "vs" in cell_text:
            requests.append(_header_format(sheet_id, i, {"red": 0.2, "green": 0.3, "blue": 0.5},
                                           font_size=11, white_text=True))

        # Parlay summary header
        elif cell_text.startswith("📝"):
            requests.append(_header_format(sheet_id, i, {"red": 0.2, "green": 0.2, "blue": 0.3},
                                           font_size=11, white_text=True))

        # Tracker separator
        elif cell_text.startswith("───"):
            requests.append(_header_format(sheet_id, i, {"red": 0.3, "green": 0.3, "blue": 0.3},
                                           font_size=10, white_text=True))

        # Sub-labels (Bet Type, Selection, Confidence, etc.)
        elif cell_text in ("Bet Type", "Selection", "Confidence", "Why This Bet", "Reasoning:"):
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": i, "endRowIndex": i + 1,
                        "startColumnIndex": 0, "endColumnIndex": 1,
                    },
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            })

        # Confidence cells — color by level
        if cell_text == "Confidence" and len(row) >= 2:
            conf = row[1]
            color = {"Near Certain": {"red": 0.6, "green": 0.9, "blue": 0.6},
                     "Very High": {"red": 0.7, "green": 0.9, "blue": 0.7},
                     "High": {"red": 0.8, "green": 0.9, "blue": 0.75}}.get(conf)
            if color:
                requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": i, "endRowIndex": i + 1,
                            "startColumnIndex": 1, "endColumnIndex": 2,
                        },
                        "cell": {"userEnteredFormat": {"backgroundColor": color}},
                        "fields": "userEnteredFormat.backgroundColor",
                    }
                })

    # Tracker header row formatting
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": tracker_header_row,
                "endRowIndex": tracker_header_row + 1,
                "startColumnIndex": 0, "endColumnIndex": 9,
            },
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.85, "green": 0.85, "blue": 0.85},
            }},
            "fields": "userEnteredFormat(textFormat,backgroundColor)",
        }
    })

    # Column widths
    col_widths = [300, 350, 350, 350, 80, 80, 80, 80, 200]
    for col_idx, width in enumerate(col_widths):
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": col_idx, "endIndex": col_idx + 1,
                },
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        })

    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": requests},
        ).execute()
        print(f"  [format] Applied {len(requests)} formatting request(s)")


def _header_format(sheet_id, row_idx, bg_color, font_size=11, white_text=False):
    """Build a repeatCell request for a header row."""
    text_fmt = {"bold": True, "fontSize": font_size}
    if white_text:
        text_fmt["foregroundColor"] = {"red": 1, "green": 1, "blue": 1}
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                "startColumnIndex": 0, "endColumnIndex": 9,
            },
            "cell": {"userEnteredFormat": {
                "textFormat": text_fmt,
                "backgroundColor": bg_color,
            }},
            "fields": "userEnteredFormat(textFormat,backgroundColor)",
        }
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    run_date = str(date.today())

    parser = argparse.ArgumentParser(description="Generate daily parlay recommendation")
    parser.add_argument("--date", default=run_date, help="Date in YYYY-MM-DD format")
    args = parser.parse_args()
    run_date = args.date

    print(f"generate_parlay.py — date={run_date}", flush=True)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[parlay] FAILED — ANTHROPIC_API_KEY not found in environment")
        sys.exit(1)

    try:
        import anthropic
    except ImportError:
        print("[parlay] FAILED — anthropic package not installed")
        sys.exit(1)

    # Get Google Sheets service for reading context and writing output
    service = get_service()

    # Read today's predictions for context
    print("Reading predictions context...", flush=True)
    predictions_context = build_predictions_context(service, run_date)
    print(f"  Context: {predictions_context[:200]}...", flush=True)

    # Run the agentic research loop
    print("Starting research loop...", flush=True)
    client = anthropic.Anthropic(api_key=api_key)

    try:
        final_text = run_agentic_loop(client, run_date, predictions_context)
    except Exception as e:
        traceback.print_exc()
        print(f"[parlay] FAILED — API error: {e}", flush=True)
        sys.exit(1)

    # Parse and validate
    try:
        parlay_data = extract_json(final_text)
        parlay_data = validate_parlay(parlay_data)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[parlay] FAILED — Parse/validation error: {e}", flush=True)
        print(f"  Raw response snippet: {final_text[:1000]}", flush=True)
        sys.exit(1)

    # Print the parlay
    legs = parlay_data["legs"]
    print(f"\n{'='*60}", flush=True)
    print(f"DAILY PARLAY — {run_date}", flush=True)
    print(f"Games researched: {parlay_data.get('games_researched', '?')}", flush=True)
    print(f"Overall confidence: {parlay_data.get('overall_confidence', '?')}", flush=True)
    print(f"{'='*60}", flush=True)

    for i, leg in enumerate(legs):
        print(f"\nLeg {i+1}: {leg['home_team']} vs {leg['away_team']}", flush=True)
        print(f"  Bet: {leg['bet_type']} → {leg['selection']}", flush=True)
        print(f"  Confidence: {leg['confidence']}", flush=True)
        print(f"  Why this bet: {leg.get('why_this_bet_type', 'N/A')}", flush=True)
        for reason in leg["reasoning"]:
            print(f"    • {reason}", flush=True)

    if parlay_data.get("parlay_summary"):
        print(f"\nSummary: {parlay_data['parlay_summary']}", flush=True)
    print(f"{'='*60}\n", flush=True)

    # Write to sheet
    print("Writing parlay to sheet...", flush=True)
    write_parlay(service, run_date, parlay_data)

    print(f"[parlay] Complete for {run_date}", flush=True)


if __name__ == "__main__":
    main()
