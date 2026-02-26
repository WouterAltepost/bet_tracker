"""
update_sheet.py — Write predictions and results to Google Sheet.

Usage:
  python tools/update_sheet.py --mode=predictions [--date=YYYY-MM-DD]
  python tools/update_sheet.py --mode=results     [--date=YYYY-MM-DD]

Mode: predictions
  Reads .tmp/predictions_{site}_{date}.json for each of 5 sites.
  Appends up to 30 rows to the "Predictions" tab.

Mode: results
  Reads .tmp/scores_{date}.json (produced by score_predictions.py).
  Updates Result and Correct columns in "Predictions" tab.
  Rebuilds the "Leaderboard" tab from all historical data.
"""

import argparse
import json
import os
import sys
from datetime import date

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

SPREADSHEET_ID = "19kX5kwwut8FAjNinI2YJjfm7LihruhkDYIXf8qD0f5M"
PREDICTIONS_TAB = "Predictions"
LEADERBOARD_TAB = "Leaderboard"

SITES = ["forebet", "predictz", "onemillion", "vitibet", "freesupertips", "claude"]

SITE_COLORS = {
    "forebet":       {"red": 0.788, "green": 0.875, "blue": 0.953},  # pastel blue
    "predictz":      {"red": 0.980, "green": 0.898, "blue": 0.706},  # pastel orange
    "onemillion":    {"red": 0.851, "green": 0.918, "blue": 0.827},  # pastel green
    "vitibet":       {"red": 0.851, "green": 0.824, "blue": 0.914},  # pastel purple
    "freesupertips": {"red": 0.957, "green": 0.800, "blue": 0.800},  # pastel red
    "claude":        {"red": 1.000, "green": 0.949, "blue": 0.800},  # pastel yellow
}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")
TMP_DIR = os.path.join(BASE_DIR, ".tmp")

PREDICTIONS_HEADERS = ["Date", "Site", "Home Team", "Away Team", "Prediction", "Result", "Correct"]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_service():
    creds = None
    token_env = os.environ.get("GOOGLE_TOKEN_JSON")
    credentials_env = os.environ.get("GOOGLE_CREDENTIALS_JSON")

    if token_env:
        creds = Credentials.from_authorized_user_info(json.loads(token_env), SCOPES)
    elif os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            if not token_env:
                with open(TOKEN_FILE, "w") as f:
                    f.write(creds.to_json())
        else:
            if credentials_env:
                flow = InstalledAppFlow.from_client_config(json.loads(credentials_env), SCOPES)
            else:
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
    return build("sheets", "v4", credentials=creds)


# ---------------------------------------------------------------------------
# Sheet helpers
# ---------------------------------------------------------------------------

def get_or_create_tab(service, tab_name):
    """Return the sheet ID for tab_name, creating it if it doesn't exist."""
    spreadsheet = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    for sheet in spreadsheet.get("sheets", []):
        if sheet["properties"]["title"] == tab_name:
            return sheet["properties"]["sheetId"]
    body = {"requests": [{"addSheet": {"properties": {"title": tab_name}}}]}
    resp = service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID, body=body
    ).execute()
    print(f"  Created tab '{tab_name}'")
    return resp["replies"][0]["addSheet"]["properties"]["sheetId"]


def ensure_headers(service, tab_name, headers):
    """Write header row if the tab is empty."""
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{tab_name}!A1:Z1"
    ).execute()
    if not result.get("values"):
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{tab_name}!A1",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()
        print(f"  Headers written to '{tab_name}'")


def read_tab(service, tab_name):
    """Return all rows (including header) from a tab as a list of lists."""
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{tab_name}!A:G"
    ).execute()
    return result.get("values", [])


def apply_site_colors(service, sheet_id, site_row_ranges):
    """
    Apply pastel background colors to each site's rows.
    site_row_ranges: list of (site, start_row_0idx, end_row_0idx) — end is exclusive.
    """
    requests = []
    for site, start, end in site_row_ranges:
        color = SITE_COLORS.get(site)
        if not color:
            continue
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start,
                    "endRowIndex": end,
                    "startColumnIndex": 0,
                    "endColumnIndex": 7,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": color,
                    }
                },
                "fields": "userEnteredFormat.backgroundColor",
            }
        })
    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": requests},
        ).execute()
        print(f"  Colors applied for {len(requests)} site(s)")


# ---------------------------------------------------------------------------
# Mode: predictions
# ---------------------------------------------------------------------------

def mode_predictions(service, run_date):
    sheet_id = get_or_create_tab(service, PREDICTIONS_TAB)
    ensure_headers(service, PREDICTIONS_TAB, PREDICTIONS_HEADERS)

    # Current row count (including header) tells us where new rows will land
    existing = read_tab(service, PREDICTIONS_TAB)
    base_row = len(existing)  # 0-indexed start index for first new row

    rows_to_append = []
    site_row_ranges = []  # (site, start_0idx, end_0idx)

    for site in SITES:
        path = os.path.join(TMP_DIR, f"predictions_{site}_{run_date}.json")

        if not os.path.exists(path):
            print(f"  [{site}] WARNING: file not found ({path}) — skipping")
            continue

        with open(path) as f:
            data = json.load(f)

        site_start = base_row + len(rows_to_append)

        if data["status"] == "failed":
            print(f"  [{site}] SCRAPE_FAILED — {data.get('error', 'unknown error')}")
            for _ in range(5):
                rows_to_append.append([run_date, site, "SCRAPE_FAILED", "", "", "", ""])
            site_row_ranges.append((site, site_start, site_start + 5))
        else:
            preds = data["predictions"][:5]
            for pred in preds:
                rows_to_append.append([
                    run_date,
                    site,
                    pred["home_team"],
                    pred["away_team"],
                    pred["prediction"],
                    "",  # Result — filled in evening run
                    "",  # Correct — filled in evening run
                ])
            site_row_ranges.append((site, site_start, site_start + len(preds)))
            print(f"  [{site}] {len(data['predictions'])} predictions loaded")

    if not rows_to_append:
        print("No rows to append — check that scrapers ran and wrote .tmp/ files.")
        return

    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{PREDICTIONS_TAB}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows_to_append},
    ).execute()
    print(f"\nAppended {len(rows_to_append)} rows to '{PREDICTIONS_TAB}'")

    apply_site_colors(service, sheet_id, site_row_ranges)


# ---------------------------------------------------------------------------
# Mode: results
# ---------------------------------------------------------------------------

def mode_results(service, run_date):
    scores_path = os.path.join(TMP_DIR, f"scores_{run_date}.json")
    if not os.path.exists(scores_path):
        print(f"ERROR: scores file not found: {scores_path}")
        print("Run score_predictions.py first.")
        sys.exit(1)

    with open(scores_path) as f:
        scores_data = json.load(f)

    details = scores_data.get("details", [])
    if not details:
        print("WARNING: scores file has no details — nothing to update")

    # Read current Predictions tab to find row positions
    all_rows = read_tab(service, PREDICTIONS_TAB)
    if not all_rows:
        print("ERROR: Predictions tab is empty.")
        sys.exit(1)

    # Build lookup: (date, site, home_team, away_team) -> 1-based sheet row number
    # Header is row 1, data starts at row 2
    lookup = {}
    for i, row in enumerate(all_rows):
        if i == 0:
            continue  # skip header
        if len(row) < 4:
            continue
        key = (row[0], row[1], row[2], row[3])
        if key not in lookup:  # keep first occurrence
            lookup[key] = i + 1  # 1-based

    # Build batch update for Result (col F) and Correct (col G)
    updates = []
    matched = 0
    for detail in details:
        key = (run_date, detail["site"], detail["home_team"], detail["away_team"])
        row_num = lookup.get(key)
        if row_num is None:
            print(f"  WARNING: no sheet row found for {key}")
            continue
        updates.append({
            "range": f"{PREDICTIONS_TAB}!F{row_num}:G{row_num}",
            "values": [[detail["result"], detail["correct"]]],
        })
        matched += 1

    if updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()
        print(f"Updated {matched} rows with results in '{PREDICTIONS_TAB}'")
    else:
        print("No rows updated — check that predictions were written this morning.")

    # Re-fetch sheet to get latest data for leaderboard rebuild
    fresh_rows = read_tab(service, PREDICTIONS_TAB)
    rebuild_leaderboard(service, fresh_rows)


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

def rebuild_leaderboard(service, all_rows):
    """
    Rebuild Leaderboard tab with one column per day and an Average column.

    Layout:
      Site | 2026-02-25 | 2026-02-26 | ... | Average
      forebet | 80% | 60% | ... | 70.0%

    Each cell = win % for that site on that date (Y / scoreable predictions).
    "—" when no scoreable predictions exist for that site/date.
    Average = mean of non-"—" cells. Rows sorted by Average descending.
    """
    get_or_create_tab(service, LEADERBOARD_TAB)

    # Step 1: build per-(date, site) counters
    counters = {}  # { (date, site): {"total": int, "correct": int} }
    all_dates = set()

    for i, row in enumerate(all_rows):
        if i == 0:
            continue  # skip header
        if len(row) < 3:
            continue
        run_date = row[0] if len(row) > 0 else ""
        site = row[1] if len(row) > 1 else ""
        home = row[2] if len(row) > 2 else ""
        correct_val = row[6] if len(row) > 6 else ""

        if not run_date or site not in SITES or home == "SCRAPE_FAILED":
            continue

        all_dates.add(run_date)
        key = (run_date, site)
        if key not in counters:
            counters[key] = {"total": 0, "correct": 0}

        if correct_val in ("Y", "N"):
            counters[key]["total"] += 1
            if correct_val == "Y":
                counters[key]["correct"] += 1

    sorted_dates = sorted(all_dates)  # chronological

    # Step 2: build one data row per site
    data_rows = []
    for site in SITES:
        day_pcts = []
        numeric_pcts = []

        for d in sorted_dates:
            c = counters.get((d, site), {"total": 0, "correct": 0})
            if c["total"] > 0:
                pct = c["correct"] / c["total"] * 100
                cell = f"{pct:.0f}%"
                numeric_pcts.append(pct)
            else:
                cell = "—"
            day_pcts.append(cell)

        if numeric_pcts:
            avg = sum(numeric_pcts) / len(numeric_pcts)
            avg_cell = f"{avg:.1f}%"
        else:
            avg_cell = "—"

        data_rows.append([site] + day_pcts + [avg_cell])

    # Step 3: sort by average descending
    def sort_key(row):
        avg = row[-1]
        return float(avg.replace("%", "")) if avg != "—" else -1

    data_rows.sort(key=sort_key, reverse=True)

    header = ["Site"] + sorted_dates + ["Average"]

    # Clear tab first to remove any stale columns from previous formats
    service.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{LEADERBOARD_TAB}!A:Z",
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{LEADERBOARD_TAB}!A1",
        valueInputOption="RAW",
        body={"values": [header] + data_rows},
    ).execute()
    print(f"Leaderboard rebuilt in '{LEADERBOARD_TAB}' ({len(sorted_dates)} day(s))")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Write predictions/results to Google Sheet")
    parser.add_argument("--mode", choices=["predictions", "results"], required=True)
    parser.add_argument("--date", default=str(date.today()), help="Date in YYYY-MM-DD format")
    args = parser.parse_args()

    print(f"update_sheet.py — mode={args.mode}, date={args.date}")
    service = get_service()

    if args.mode == "predictions":
        mode_predictions(service, args.date)
    elif args.mode == "results":
        mode_results(service, args.date)


if __name__ == "__main__":
    main()
