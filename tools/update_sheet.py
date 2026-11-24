"""
update_sheet.py — Write predictions and results to Google Sheet.

Usage:
  python tools/update_sheet.py --mode=predictions [--date=YYYY-MM-DD]
  python tools/update_sheet.py --mode=results     [--date=YYYY-MM-DD]

Mode: predictions
  Reads .tmp/predictions_{site}_{date}.json for each site in ACTIVE_SITES.
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
from google.oauth2 import service_account
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

# Every site that has ever been tracked — kept so historical rows are still
# scored and shown on the Leaderboard.
SITES = ["forebet", "predictz", "onemillion", "vitibet", "freesupertips", "zulubet", "statarea", "claude"]

# Sites the morning run collects today. Forebet (Cloudflare blocks Railway) and
# OneMillion (paywall) were retired on 2026-11-24 and replaced by Zulubet and Statarea.
ACTIVE_SITES = ["predictz", "vitibet", "freesupertips", "zulubet", "statarea", "claude"]

SITE_COLORS = {
    "forebet":       {"red": 0.788, "green": 0.875, "blue": 0.953},  # pastel blue
    "predictz":      {"red": 0.980, "green": 0.898, "blue": 0.706},  # pastel orange
    "onemillion":    {"red": 0.851, "green": 0.918, "blue": 0.827},  # pastel green
    "vitibet":       {"red": 0.851, "green": 0.824, "blue": 0.914},  # pastel purple
    "freesupertips": {"red": 0.957, "green": 0.800, "blue": 0.800},  # pastel red
    "zulubet":       {"red": 0.800, "green": 0.937, "blue": 0.929},  # pastel teal
    "statarea":      {"red": 0.957, "green": 0.851, "blue": 0.918},  # pastel pink
    "claude":        {"red": 1.000, "green": 0.949, "blue": 0.800},  # pastel yellow
}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")
SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, "service_account.json")
TMP_DIR = os.path.join(BASE_DIR, ".tmp")

PREDICTIONS_HEADERS = ["Date", "Site", "Home Team", "Away Team", "Prediction", "Result", "Correct"]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_service():
    """Google Sheets client. Preferred: a service account (never expires) from
    env GOOGLE_SERVICE_ACCOUNT_JSON or service_account.json — the sheet must be
    shared with the service account's email as Editor. Fallback: the personal
    OAuth token (env GOOGLE_TOKEN_JSON or token.json)."""
    sa_env = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_env or os.path.exists(SERVICE_ACCOUNT_FILE):
        info = json.loads(sa_env) if sa_env else json.load(open(SERVICE_ACCOUNT_FILE))
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        return build("sheets", "v4", credentials=creds)

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
            if token_env or not sys.stdin.isatty():
                # Headless (Railway / n8n): a browser login would hang forever.
                raise RuntimeError(
                    "Google token is missing, expired or revoked and no service account is "
                    "configured. Set GOOGLE_SERVICE_ACCOUNT_JSON (see workflows/evening_run.md).")
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
    if not requests:
        print("  [color] No color requests generated — site_row_ranges may be empty or all sites unknown")
        return
    if requests:
        print(f"  [color] Sending {len(requests)} color request(s) to Sheets API")
        try:
            service.spreadsheets().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"requests": requests},
            ).execute()
            print(f"  [color] Colors applied for {len(requests)} site(s)")
        except Exception as e:
            print(f"  [color] batchUpdate failed: {e}")
            raise


# ---------------------------------------------------------------------------
# Mode: predictions
# ---------------------------------------------------------------------------

def sites_written_for_date(all_rows, run_date):
    """Sites that already have real (not SCRAPE_FAILED) Predictions rows for run_date."""
    return {row[1] for row in all_rows[1:]
            if len(row) >= 3 and row[0] == run_date and row[2] != "SCRAPE_FAILED"}


def failed_row_indices(all_rows, run_date):
    """site -> 0-based sheet row indices of its SCRAPE_FAILED rows for run_date."""
    out = {}
    for i, row in enumerate(all_rows):
        if i and len(row) >= 3 and row[0] == run_date and row[2] == "SCRAPE_FAILED":
            out.setdefault(row[1], []).append(i)
    return out


def mode_predictions(service, run_date):
    sheet_id = get_or_create_tab(service, PREDICTIONS_TAB)
    ensure_headers(service, PREDICTIONS_TAB, PREDICTIONS_HEADERS)

    rows_to_insert = []
    site_row_ranges = []  # (site, start_0idx, end_0idx)

    # Re-running the morning step must not duplicate rows: skip any site that
    # already has real rows for this date. A site that only has SCRAPE_FAILED
    # rows is retried, and those rows are replaced if the retry succeeds.
    all_rows = read_tab(service, PREDICTIONS_TAB)
    already_written = sites_written_for_date(all_rows, run_date)
    failed_rows = failed_row_indices(all_rows, run_date)
    if already_written:
        print(f"  Rows for {run_date} already exist for: {sorted(already_written)} — those sites are skipped")
    rows_to_delete = []

    for site in ACTIVE_SITES:
        if site in already_written:
            continue
        path = os.path.join(TMP_DIR, f"predictions_{site}_{run_date}.json")

        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
        else:
            # The scraper crashed before it could write its own failure file.
            data = {"status": "failed", "error": f"no output file ({os.path.basename(path)})"}

        if data["status"] == "failed" and site in failed_rows:
            print(f"  [{site}] still failing — SCRAPE_FAILED rows already in the sheet")
            continue
        if data["status"] != "failed" and site in failed_rows:
            rows_to_delete += failed_rows[site]

        # Row 0 = header, new rows start at index 1; accumulate from there
        site_start = 1 + len(rows_to_insert)

        if data["status"] == "failed":
            print(f"  [{site}] SCRAPE_FAILED — {data.get('error', 'unknown error')}")
            for _ in range(5):
                rows_to_insert.append([run_date, site, "SCRAPE_FAILED", "", "", "", ""])
            site_row_ranges.append((site, site_start, site_start + 5))
        else:
            preds = data["predictions"][:5]
            for pred in preds:
                rows_to_insert.append([
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

    if not rows_to_insert:
        print("No rows to insert — nothing new for this date.")
        return

    if rows_to_delete:
        # Bottom-up so earlier deletions don't shift later indices.
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"deleteDimension": {"range": {
                "sheetId": sheet_id, "dimension": "ROWS", "startIndex": i, "endIndex": i + 1}}}
                for i in sorted(rows_to_delete, reverse=True)]},
        ).execute()
        print(f"  Replaced {len(rows_to_delete)} SCRAPE_FAILED row(s) with fresh results")

    num_new = len(rows_to_insert)

    # Insert blank rows immediately after the header (index 1) so new data lands at top
    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"requests": [{
            "insertDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": 1,
                    "endIndex": 1 + num_new,
                },
                "inheritFromBefore": False,
            }
        }]},
    ).execute()

    # Write data into the newly opened rows starting at A2
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{PREDICTIONS_TAB}!A2",
        valueInputOption="RAW",
        body={"values": rows_to_insert},
    ).execute()
    print(f"\nInserted {num_new} rows at top of '{PREDICTIONS_TAB}'")

    print(f"[color] Calling apply_site_colors: sheet_id={sheet_id}, ranges={site_row_ranges}")
    try:
        apply_site_colors(service, sheet_id, site_row_ranges)
        print("[color] apply_site_colors completed successfully")
    except Exception as e:
        print(f"[color] ERROR in apply_site_colors: {e}")


# ---------------------------------------------------------------------------
# Mode: results
# ---------------------------------------------------------------------------

def write_results(service, all_rows, run_date, details):
    """
    Write Result (col F) and Correct (col G) for run_date's scored predictions.
    Details whose "correct" is None are pending and left blank. Every row with
    the same (date, site, home, away) gets the value, so duplicate rows from a
    repeated morning run don't stay blank. Returns the number of cells-rows written.
    """
    lookup = {}  # (date, site, home, away) -> [1-based sheet row numbers]
    for i, row in enumerate(all_rows):
        if i == 0 or len(row) < 4:
            continue
        lookup.setdefault((row[0], row[1], row[2], row[3]), []).append(i + 1)

    updates = []
    for detail in details:
        if detail.get("correct") is None:
            continue  # pending — retried on the next run
        key = (run_date, detail["site"], detail["home_team"], detail["away_team"])
        row_nums = lookup.get(key)
        if not row_nums:
            print(f"  WARNING: no sheet row found for {key}")
            continue
        for row_num in row_nums:
            updates.append({
                "range": f"{PREDICTIONS_TAB}!F{row_num}:G{row_num}",
                "values": [[detail["result"], detail["correct"]]],
            })

    if updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()
        print(f"Updated {len(updates)} rows with results for {run_date} in '{PREDICTIONS_TAB}'")
    else:
        print(f"No rows updated for {run_date}.")
    return len(updates)


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

    all_rows = read_tab(service, PREDICTIONS_TAB)
    if not all_rows:
        print("ERROR: Predictions tab is empty.")
        sys.exit(1)

    write_results(service, all_rows, run_date, details)

    # Re-fetch sheet to get latest data for leaderboard rebuild
    fresh_rows = read_tab(service, PREDICTIONS_TAB)
    rebuild_leaderboard(service, fresh_rows)


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

def compute_leaderboard(all_rows):
    """
    Build the Leaderboard table from all Predictions rows (header included).

    Layout:
      Site | Win % | Correct | Scored | 2026-03-11 | 2026-03-10 | ...  (newest date first)
      forebet | 61.5% | 32 | 52 | 80% | 60% | ...

    Win % is the overall hit rate (all correct / all scored predictions), so a day
    with 1 scored pick doesn't weigh as much as a day with 5. Scored = rows whose
    Correct value is Y or N (UNMATCHED and pending rows don't count).
    Each date cell = that day's hit rate; "—" when nothing was scored.
    Duplicate rows (same date/site/home/away) count once.
    Rows are sorted by Win % descending. Returns (header, data_rows).
    """
    counters = {}  # { (date, site): {"total": int, "correct": int} }
    all_dates = set()
    seen = set()  # duplicate rows (repeated morning runs) are counted once

    for i, row in enumerate(all_rows):
        if i == 0:
            continue  # skip header
        if len(row) < 3:
            continue
        run_date, site, home = row[0], row[1], row[2]
        correct_val = row[6] if len(row) > 6 else ""

        if not run_date or site not in SITES or home == "SCRAPE_FAILED":
            continue
        key = tuple(row[:4])
        if key in seen:
            continue
        seen.add(key)

        all_dates.add(run_date)
        c = counters.setdefault((run_date, site), {"total": 0, "correct": 0})
        if correct_val in ("Y", "N"):
            c["total"] += 1
            if correct_val == "Y":
                c["correct"] += 1

    sorted_dates = sorted(all_dates, reverse=True)  # newest first

    data_rows = []
    for site in SITES:
        day_cells = []
        site_correct = site_total = 0
        for d in sorted_dates:
            c = counters.get((d, site), {"total": 0, "correct": 0})
            site_correct += c["correct"]
            site_total += c["total"]
            day_cells.append(f"{c['correct'] / c['total'] * 100:.0f}%" if c["total"] else "—")
        win_pct = f"{site_correct / site_total * 100:.1f}%" if site_total else "—"
        data_rows.append([site, win_pct, site_correct, site_total] + day_cells)

    data_rows.sort(key=lambda r: float(r[1].rstrip("%")) if r[1] != "—" else -1, reverse=True)
    header = ["Site", "Win %", "Correct", "Scored"] + sorted_dates
    return header, data_rows


def rebuild_leaderboard(service, all_rows):
    """Rewrite the Leaderboard tab (see compute_leaderboard) with per-site row colours."""
    sheet_id = get_or_create_tab(service, LEADERBOARD_TAB)
    header, data_rows = compute_leaderboard(all_rows)
    sorted_dates = header[4:]

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

    # Step 4: apply per-site pastel row colours (same palette as Predictions tab)
    num_cols = len(header)
    color_requests = []
    for row_idx, row in enumerate(data_rows):
        site = row[0]
        color = SITE_COLORS.get(site)
        if not color:
            continue
        color_requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_idx + 1,  # +1 to skip header
                    "endRowIndex": row_idx + 2,
                    "startColumnIndex": 0,
                    "endColumnIndex": num_cols,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": color}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        })
    if color_requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": color_requests},
        ).execute()
        print(f"  [color] Leaderboard colours applied for {len(color_requests)} row(s)")


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
