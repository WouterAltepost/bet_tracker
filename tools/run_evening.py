"""
run_evening.py — Evening run with catch-up: score every date that still has
unscored predictions in the Google Sheet, then rebuild the Leaderboard.

Usage:
  python tools/run_evening.py                 # score all pending dates up to today
  python tools/run_evening.py --dry-run       # show what would be written, write nothing
  python tools/run_evening.py --date=2026-03-14
  python tools/run_evening.py --rescore       # also re-check rows that already have a value

Why: predictions are read from the sheet (not .tmp/, which is lost when the
server restarts), and a missed evening run is caught up automatically the
next time this runs. Predictions whose match isn't finished yet stay blank and
are retried; after FINAL_AFTER_DAYS they become UNMATCHED / VOID.

Steps per date: fetch_results.get_results → score_predictions.score → update_sheet.write_results.
"""

import argparse
import sys
import time
from collections import defaultdict
from datetime import date

from fetch_results import get_results
from score_predictions import (
    is_final,
    load_predictions_from_rows,
    score,
    write_scores,
)
from update_sheet import (
    PREDICTIONS_TAB,
    SITES,
    get_service,
    read_tab,
    rebuild_leaderboard,
    write_results,
)

API_PAUSE_SECONDS = 7  # football-data.org free tier: 10 requests/minute


def current_values(all_rows):
    """(date, site, home, away) -> set of Correct values on the sheet for that key."""
    values = defaultdict(set)
    for row in all_rows[1:]:
        if len(row) >= 4:
            values[tuple(row[:4])].add(row[6] if len(row) > 6 else "")
    return values


def pending_dates(all_rows, today):
    """Dates (<= today) that have at least one unscored, non-failed prediction row."""
    dates = set()
    for row in all_rows[1:]:
        if len(row) < 5 or row[1] not in SITES or row[2] == "SCRAPE_FAILED":
            continue
        if row[0] <= today and not (row[6] if len(row) > 6 else ""):
            dates.add(row[0])
    return sorted(dates)


def main():
    parser = argparse.ArgumentParser(description="Score pending predictions and rebuild the leaderboard")
    parser.add_argument("--date", help="Only this date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would change; write nothing")
    parser.add_argument("--rescore", action="store_true",
                        help="Re-check rows that already have a value (default: only fill blanks)")
    args = parser.parse_args()

    today = str(date.today())
    service = get_service()
    all_rows = read_tab(service, PREDICTIONS_TAB)
    if len(all_rows) < 2:
        print("Predictions tab is empty — nothing to score.")
        return

    if args.date:
        dates = [args.date]
    elif args.rescore:
        dates = sorted({r[0] for r in all_rows[1:] if r and r[0] <= today})
    else:
        dates = pending_dates(all_rows, today)
    print(f"run_evening.py — today={today}, dates to score: {dates or 'none'}"
          f"{' (DRY RUN)' if args.dry_run else ''}")

    existing = current_values(all_rows)
    fetch_failures = 0
    written = 0
    changes = []  # (date, site, home, away, old, new)

    for i, run_date in enumerate(dates):
        if i:
            time.sleep(API_PAUSE_SECONDS)
        print(f"\n=== {run_date} ===")
        try:
            results = get_results(run_date)
        except Exception as e:
            fetch_failures += 1
            print(f"  ERROR fetching results for {run_date}: {e} — skipped, will retry next run")
            continue

        preds = load_predictions_from_rows(all_rows, run_date)
        summary, details = score(preds, results, is_final(run_date))
        write_scores(run_date, summary, details)

        to_write = []
        for d in details:
            if d["correct"] is None:
                continue
            key = (run_date, d["site"], d["home_team"], d["away_team"])
            old = existing.get(key, {""})
            if not args.rescore and "" not in old:
                continue  # already scored — only fill blanks
            if old != {d["correct"]}:
                changes.append((*key, "/".join(sorted(v or "blank" for v in old)), d["correct"]))
            to_write.append(d)

        if args.dry_run:
            print(f"  would write {len(to_write)} prediction(s)")
        elif to_write:
            written += write_results(service, all_rows, run_date, to_write)

    print(f"\n{len(changes)} value change(s):")
    for c in changes:
        print(f"  {c[0]} {c[1]:13} {c[2]} vs {c[3]}: {c[4]} → {c[5]}")

    if not args.dry_run:
        rebuild_leaderboard(service, read_tab(service, PREDICTIONS_TAB))
        print(f"\nDone: {written} row(s) written across {len(dates)} date(s).")

    if dates and fetch_failures == len(dates):
        sys.exit(1)  # nothing could be fetched — surface as an error


if __name__ == "__main__":
    main()
