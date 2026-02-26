"""
fetch_results.py — Fetch today's football match results from football-data.org

Uses the free tier API which covers major European competitions.
Maps results to 1/X/2 format:
  HOME_TEAM  → "1"
  DRAW       → "X"
  AWAY_TEAM  → "2"

Only includes FINISHED matches.

Output: .tmp/results_{date}.json

Tracked competitions (free tier):
  CL   Champions League
  EL   Europa League
  ECL  Europa Conference League
  PL   Premier League
  ELC  Championship
  PD   La Liga
  BL1  Bundesliga
  SA   Serie A
  FL1  Ligue 1
  DED  Eredivisie
  PPL  Primeira Liga
"""

import json
import os
import sys
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(BASE_DIR, ".tmp")
ENV_FILE = os.path.join(BASE_DIR, ".env")

load_dotenv(ENV_FILE)

API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY")
API_BASE = "https://api.football-data.org/v4"

WINNER_MAP = {
    "HOME_TEAM": "1",
    "DRAW": "X",
    "AWAY_TEAM": "2",
}


def write_output(run_date, matches, error=None):
    os.makedirs(TMP_DIR, exist_ok=True)
    output = {
        "date": run_date,
        "status": "ok" if not error else "failed",
        "error": error,
        "matches": matches,
    }
    path = os.path.join(TMP_DIR, f"results_{run_date}.json")
    with open(path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    return path


def fetch_matches(run_date):
    if not API_KEY:
        raise ValueError("FOOTBALL_DATA_API_KEY not found in .env")

    headers = {"X-Auth-Token": API_KEY}
    # Query a 3-day window around run_date. The API inconsistently returns
    # incomplete results when dateFrom == dateTo; a range query is reliable.
    d = date.fromisoformat(run_date)
    params = {
        "dateFrom": str(d - timedelta(days=1)),
        "dateTo": str(d + timedelta(days=1)),
    }

    print(f"Fetching matches for {run_date} from football-data.org ...")
    resp = requests.get(f"{API_BASE}/matches", headers=headers, params=params, timeout=15)

    if resp.status_code == 429:
        raise RuntimeError(
            f"Rate limited (429). Wait a minute and retry.\n"
            f"Response: {resp.text[:200]}"
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"API error {resp.status_code}: {resp.text[:300]}"
        )

    # Filter to only matches whose UTC date matches run_date
    all_matches = resp.json().get("matches", [])
    return [m for m in all_matches if m.get("utcDate", "").startswith(run_date)]


def parse_matches(raw_matches):
    matches = []
    finished = 0
    skipped = 0

    for m in raw_matches:
        status = m.get("status", "")
        if status != "FINISHED":
            skipped += 1
            continue

        finished += 1
        winner = m.get("score", {}).get("winner")
        result = WINNER_MAP.get(winner)

        if not result:
            # Match might have ended without a clear winner (e.g. penalties)
            ft = m.get("score", {}).get("fullTime", {})
            home_score = ft.get("home")
            away_score = ft.get("away")
            if home_score is not None and away_score is not None:
                if home_score > away_score:
                    result = "1"
                elif home_score == away_score:
                    result = "X"
                else:
                    result = "2"

        home_team = m.get("homeTeam", {}).get("name", "")
        away_team = m.get("awayTeam", {}).get("name", "")
        short_home = m.get("homeTeam", {}).get("shortName", home_team)
        short_away = m.get("awayTeam", {}).get("shortName", away_team)
        ft = m.get("score", {}).get("fullTime", {})

        matches.append({
            "home_team": home_team,
            "away_team": away_team,
            "short_home": short_home,
            "short_away": short_away,
            "result": result,
            "home_score": ft.get("home"),
            "away_score": ft.get("away"),
            "competition": m.get("competition", {}).get("name", ""),
        })

    return matches, finished, skipped


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date.today()), help="Date in YYYY-MM-DD format")
    args = parser.parse_args()
    run_date = args.date
    print(f"fetch_results.py — date={run_date}")

    try:
        raw = fetch_matches(run_date)
        print(f"API returned {len(raw)} matches total")

        matches, finished, skipped = parse_matches(raw)
        print(f"  Finished: {finished}, Not yet played / postponed: {skipped}")
        print(f"  Stored {len(matches)} results")

        path = write_output(run_date, matches)
        print(f"Output: {path}")

        if matches:
            print("\nSample results:")
            for m in matches[:5]:
                print(f"  {m['home_team']} {m['home_score']}-{m['away_score']} {m['away_team']} → {m['result']}")

    except Exception as e:
        print(f"ERROR: {e}")
        write_output(run_date, [], error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
