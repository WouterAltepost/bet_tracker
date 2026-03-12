"""
generate_analysis.py — Analyze today's predictions and write a daily analysis to Google Sheet.

Usage:
  python tools/generate_analysis.py [--date=YYYY-MM-DD]

Reads today's predictions and leaderboard from the Google Sheet,
computes consensus across all 6 sites, calls the Anthropic API for
a brief qualitative commentary, and writes everything to an "Analysis" tab.

The Analysis tab is cleared and rewritten each morning.
"""

import argparse
import json
import os
import sys
import traceback
from collections import defaultdict
from datetime import date

from dotenv import load_dotenv

load_dotenv()

# Re-use auth and sheet constants from update_sheet
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from update_sheet import (
    SPREADSHEET_ID,
    PREDICTIONS_TAB,
    LEADERBOARD_TAB,
    SITES,
    SITE_COLORS,
    get_service,
    get_or_create_tab,
)

ANALYSIS_TAB = "Analysis"
MODEL = "claude-sonnet-4-6"

PREDICTION_LABELS = {"1": "Home Win", "X": "Draw", "2": "Away Win"}

CONFIDENCE_MAP = {
    6: "🔒 Lock",
    5: "⭐ High",
    4: "✅ Medium",
}


# ---------------------------------------------------------------------------
# Read data from sheet
# ---------------------------------------------------------------------------

def read_todays_predictions(service, run_date):
    """Read today's predictions from the Predictions tab. Returns list of dicts."""
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{PREDICTIONS_TAB}!A:G"
    ).execute()
    rows = result.get("values", [])
    predictions = []
    for row in rows[1:]:  # skip header
        if len(row) < 5:
            continue
        if row[0] != run_date:
            continue
        if row[2] == "SCRAPE_FAILED":
            continue
        predictions.append({
            "site": row[1],
            "home_team": row[2],
            "away_team": row[3],
            "prediction": row[4],
        })
    return predictions


def read_leaderboard(service):
    """Read current leaderboard standings. Returns list of (site, avg_pct) tuples."""
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{LEADERBOARD_TAB}!A:B"
    ).execute()
    rows = result.get("values", [])
    standings = []
    for row in rows[1:]:  # skip header
        if len(row) < 2:
            continue
        standings.append((row[0], row[1]))
    return standings


# ---------------------------------------------------------------------------
# Consensus computation
# ---------------------------------------------------------------------------

def compute_consensus(predictions):
    """
    Group predictions by match and compute consensus.

    Returns list of dicts sorted by confidence (highest first):
    {
        "home_team": str, "away_team": str,
        "votes": {"1": [...sites], "X": [...], "2": [...]},
        "majority_prediction": str, "majority_label": str,
        "agreement": "4/6", "confidence": "✅ Medium",
        "claude_agrees": bool, "claude_prediction": str or None,
        "total_sites": int,
    }
    """
    # Group by match — normalize team names for grouping
    matches = defaultdict(lambda: {"1": [], "X": [], "2": []})
    match_teams = {}  # key -> (home, away) with original casing

    for pred in predictions:
        key = (pred["home_team"].lower().strip(), pred["away_team"].lower().strip())
        matches[key][pred["prediction"]].append(pred["site"])
        if key not in match_teams:
            match_teams[key] = (pred["home_team"], pred["away_team"])

    results = []
    for key, votes in matches.items():
        home, away = match_teams[key]
        total = sum(len(v) for v in votes.values())

        # Find majority
        majority_pred = max(votes, key=lambda p: len(votes[p]))
        majority_count = len(votes[majority_pred])

        # Claude's position
        claude_pred = None
        claude_agrees = True
        for pred_type, sites in votes.items():
            if "claude" in sites:
                claude_pred = pred_type
                claude_agrees = (pred_type == majority_pred)
                break

        confidence = CONFIDENCE_MAP.get(majority_count, "⚠️ Low")

        results.append({
            "home_team": home,
            "away_team": away,
            "votes": dict(votes),
            "majority_prediction": majority_pred,
            "majority_label": PREDICTION_LABELS.get(majority_pred, majority_pred),
            "agreement": f"{majority_count}/{total}",
            "confidence": confidence,
            "claude_agrees": claude_agrees,
            "claude_prediction": claude_pred,
            "total_sites": total,
            "majority_count": majority_count,
        })

    # Sort: highest confidence first, then alphabetically
    results.sort(key=lambda r: (-r["majority_count"], r["home_team"]))
    return results


# ---------------------------------------------------------------------------
# Claude AI commentary
# ---------------------------------------------------------------------------

def generate_commentary(consensus, leaderboard, run_date):
    """Call Claude API for a brief qualitative commentary on today's picks."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  [analysis] ANTHROPIC_API_KEY not set — skipping AI commentary")
        return None

    try:
        import anthropic
    except ImportError:
        print("  [analysis] anthropic package not installed — skipping AI commentary")
        return None

    # Build a concise summary for the prompt
    picks_text = []
    for m in consensus:
        claude_flag = ""
        if not m["claude_agrees"] and m["claude_prediction"]:
            claude_flag = f" (Claude picks {PREDICTION_LABELS.get(m['claude_prediction'], m['claude_prediction'])} instead)"
        picks_text.append(
            f"- {m['home_team']} vs {m['away_team']}: "
            f"{m['agreement']} predict {m['majority_label']} [{m['confidence']}]{claude_flag}"
        )

    lb_text = "\n".join(f"- {site}: {pct}" for site, pct in leaderboard)

    prompt = f"""Today is {run_date}. Here are today's football prediction consensus results across 6 prediction sites:

{chr(10).join(picks_text)}

Current leaderboard standings (average win %):
{lb_text}

Write a brief 3-4 sentence analysis covering:
1. Which games stand out as the strongest bets today and why
2. Any interesting contrarian picks from Claude AI
3. Which prediction sites to watch based on current form

Keep it punchy and useful — this goes directly into a Google Sheet cell. No headers or bullet points, just flowing text."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        print(f"  [analysis] AI commentary generated ({len(text)} chars)")
        return text
    except Exception as e:
        print(f"  [analysis] AI commentary failed: {e}")
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# Write to sheet
# ---------------------------------------------------------------------------

def write_analysis(service, run_date, consensus, leaderboard, commentary):
    """Write the full analysis to the Analysis tab."""
    sheet_id = get_or_create_tab(service, ANALYSIS_TAB)

    # Clear existing content
    service.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{ANALYSIS_TAB}!A:Z",
    ).execute()

    rows = []

    # --- Section 1: Header ---
    rows.append([f"📊 Daily Analysis — {run_date}"])
    rows.append([])

    # --- Section 2: Leaderboard Snapshot ---
    rows.append(["🏆 Leaderboard Snapshot"])
    rows.append(["Site", "Win %"])
    lb_start_row = len(rows)  # 0-indexed for formatting
    for site, pct in leaderboard:
        rows.append([site, pct])
    rows.append([])

    # --- Section 3: Best Bets (Lock + High only) ---
    best_bets = [m for m in consensus if m["majority_count"] >= 5]
    rows.append(["🔒 Best Bets of the Day"])
    if best_bets:
        rows.append(["Match", "Consensus", "Confidence"])
        for m in best_bets:
            match_name = f"{m['home_team']} vs {m['away_team']}"
            consensus_text = f"{m['agreement']} predict {m['majority_label']}"
            rows.append([match_name, consensus_text, m["confidence"]])
    else:
        rows.append(["No high-confidence picks today — proceed with caution"])
    rows.append([])

    # --- Section 4: Full Consensus Table ---
    rows.append(["📋 Full Consensus Analysis"])
    rows.append(["Match", "Consensus", "Prediction", "Confidence", "Claude AI"])
    consensus_start_row = len(rows)
    for m in consensus:
        match_name = f"{m['home_team']} vs {m['away_team']}"
        consensus_text = f"{m['agreement']} predict {m['majority_label']}"

        claude_flag = "✅ Agrees"
        if m["claude_prediction"] is None:
            claude_flag = "—"
        elif not m["claude_agrees"]:
            claude_label = PREDICTION_LABELS.get(m["claude_prediction"], m["claude_prediction"])
            claude_flag = f"🤖 Disagrees → {claude_label}"

        rows.append([match_name, consensus_text, m["majority_prediction"], m["confidence"], claude_flag])
    rows.append([])

    # --- Section 5: AI Commentary ---
    if commentary:
        rows.append(["🤖 Claude AI Commentary"])
        rows.append([commentary])
    rows.append([])

    # Write all rows
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{ANALYSIS_TAB}!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()
    print(f"  Analysis written to '{ANALYSIS_TAB}' tab ({len(rows)} rows)")

    # --- Formatting ---
    format_analysis_tab(service, sheet_id, rows, lb_start_row, consensus_start_row)


def format_analysis_tab(service, sheet_id, rows, lb_start_row, consensus_start_row):
    """Apply formatting: bold headers, section colors, column widths."""
    requests = []

    # Find section header rows (rows starting with emoji)
    section_rows = []
    for i, row in enumerate(rows):
        if row and isinstance(row[0], str) and row[0] and row[0][0] in "📊🏆🔒📋🤖":
            section_rows.append(i)

    # Bold + dark background for section headers
    for row_idx in section_rows:
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_idx,
                    "endRowIndex": row_idx + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 6,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True, "fontSize": 11},
                        "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.3},
                        "textFormat": {"bold": True, "fontSize": 11, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }
        })

    # Bold for sub-headers (rows with "Site", "Match", etc.)
    for i, row in enumerate(rows):
        if row and row[0] in ("Site", "Match"):
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": i,
                        "endRowIndex": i + 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 6,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True},
                            "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,backgroundColor)",
                }
            })

    # Color leaderboard rows with site colors
    for i, row in enumerate(rows):
        if len(row) >= 2 and row[0] in SITE_COLORS:
            color = SITE_COLORS[row[0]]
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": i,
                        "endRowIndex": i + 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 6,
                    },
                    "cell": {"userEnteredFormat": {"backgroundColor": color}},
                    "fields": "userEnteredFormat.backgroundColor",
                }
            })

    # Color confidence cells in consensus table
    confidence_colors = {
        "🔒 Lock": {"red": 0.7, "green": 0.9, "blue": 0.7},    # green
        "⭐ High": {"red": 0.8, "green": 0.9, "blue": 0.7},     # light green
        "✅ Medium": {"red": 1.0, "green": 0.95, "blue": 0.7},   # light yellow
        "⚠️ Low": {"red": 1.0, "green": 0.85, "blue": 0.75},    # light orange
    }
    for i, row in enumerate(rows):
        if len(row) >= 4:
            for conf_text, color in confidence_colors.items():
                if conf_text in str(row[3]):
                    requests.append({
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": i,
                                "endRowIndex": i + 1,
                                "startColumnIndex": 3,
                                "endColumnIndex": 4,
                            },
                            "cell": {"userEnteredFormat": {"backgroundColor": color}},
                            "fields": "userEnteredFormat.backgroundColor",
                        }
                    })
                    break

    # Column widths
    col_widths = [250, 200, 80, 120, 200]
    for col_idx, width in enumerate(col_widths):
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": col_idx,
                    "endIndex": col_idx + 1,
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate daily analysis for bet tracker")
    parser.add_argument("--date", default=str(date.today()), help="Date in YYYY-MM-DD format")
    args = parser.parse_args()

    run_date = args.date
    print(f"generate_analysis.py — date={run_date}")

    service = get_service()

    # Step 1: Read data
    print("Reading today's predictions...")
    predictions = read_todays_predictions(service, run_date)
    if not predictions:
        print(f"No predictions found for {run_date} — skipping analysis")
        return

    print(f"  Found {len(predictions)} predictions across {len(set(p['site'] for p in predictions))} sites")

    print("Reading leaderboard...")
    leaderboard = read_leaderboard(service)
    print(f"  {len(leaderboard)} sites on leaderboard")

    # Step 2: Compute consensus
    print("Computing consensus...")
    consensus = compute_consensus(predictions)
    print(f"  {len(consensus)} unique matches found")

    best_bets = [m for m in consensus if m["majority_count"] >= 5]
    print(f"  Best bets (5+ agreement): {len(best_bets)}")

    # Step 3: AI commentary
    print("Generating AI commentary...")
    commentary = generate_commentary(consensus, leaderboard, run_date)

    # Step 4: Write to sheet
    print("Writing analysis to sheet...")
    write_analysis(service, run_date, consensus, leaderboard, commentary)

    print(f"\nAnalysis complete for {run_date}")


if __name__ == "__main__":
    main()
