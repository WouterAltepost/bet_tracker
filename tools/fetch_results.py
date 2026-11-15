"""
fetch_results.py — Fetch football match results from two sources and merge them:

  1. football-data.org (free tier, API key) — official but only ~13 competitions.
  2. ESPN public scoreboard (no key) — ~150-250 matches/day across almost every
     league the prediction sites tip (English lower leagues, Scotland, MLS,
     Liga MX, Turkey, Saudi, Brazil, Argentina, ...).

football-data matches come first in the list so they win ties in matching.
If one source fails the other is still used; only if both fail is it an error.
Results are always the 90-minute (regulation) result, which is what 1X2 bets
settle on — extra time and penalties are ignored.

Maps results to 1/X/2 format:
  HOME_TEAM  → "1"
  DRAW       → "X"
  AWAY_TEAM  → "2"

Includes every match in a 3-day window around the date (date-1 .. date+1) with
its status; "result" is only set for FINISHED matches. The window catches
late kick-offs that fall on the next UTC day; scoring uses status to keep
not-yet-finished predictions pending instead of marking them UNMATCHED.

Output: .tmp/results_{date}.json

football-data free-tier competitions (EL/ECL/Nations League are NOT included;
ESPN covers those):
  CL   Champions League
  PL   Premier League
  ELC  Championship
  PD   La Liga
  BL1  Bundesliga
  SA   Serie A
  FL1  Ligue 1
  DED  Eredivisie
  PPL  Primeira Liga
  BSA  Brasileirão, CLI Libertadores, WC / EC international tournaments
"""

import json
import os
import re
import sys
import time
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(BASE_DIR, ".tmp")
ENV_FILE = os.path.join(BASE_DIR, ".env")

load_dotenv(ENV_FILE)

API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY")
API_BASE = "https://api.football-data.org/v4"
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard"

# ESPN status name -> football-data style status used by scoring.
ESPN_STATUS = {
    "STATUS_FULL_TIME": "FINISHED",
    "STATUS_FINAL": "FINISHED",
    "STATUS_FINAL_AET": "FINISHED",
    "STATUS_FINAL_PEN": "FINISHED",
    "STATUS_POSTPONED": "POSTPONED",
    "STATUS_CANCELED": "CANCELLED",
    "STATUS_ABANDONED": "SUSPENDED",
    "STATUS_SUSPENDED": "SUSPENDED",
    "STATUS_FORFEIT": "AWARDED",
}

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
        # dateTo appears to be exclusive, so +2 makes date+1 fully included.
        "dateTo": str(d + timedelta(days=2)),
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

    return resp.json().get("matches", [])


def parse_matches(raw_matches):
    """Returns (matches, finished_count, other_count). Unfinished matches are
    kept with result=None so scoring can tell "not played yet" from "not covered"."""
    matches = []
    finished = 0
    other = 0

    for m in raw_matches:
        status = m.get("status", "")
        ft = m.get("score", {}).get("fullTime", {}) or {}
        result = None

        if status == "FINISHED":
            finished += 1
            score = m.get("score", {}) or {}
            regular = score.get("regularTime") or {}
            if score.get("duration", "REGULAR") != "REGULAR" and regular.get("home") is not None:
                # Went to extra time / penalties: 1X2 settles on the 90-minute score,
                # and "winner" would be the extra-time or shoot-out winner.
                ft = regular
                result = None
            else:
                result = WINNER_MAP.get(score.get("winner"))
            if not result:
                home_score, away_score = ft.get("home"), ft.get("away")
                if home_score is not None and away_score is not None:
                    result = "1" if home_score > away_score else "X" if home_score == away_score else "2"
        else:
            other += 1

        home_team = m.get("homeTeam", {}).get("name", "") or ""
        away_team = m.get("awayTeam", {}).get("name", "") or ""
        matches.append({
            "home_team": home_team,
            "away_team": away_team,
            "short_home": m.get("homeTeam", {}).get("shortName") or home_team,
            "short_away": m.get("awayTeam", {}).get("shortName") or away_team,
            "status": status,
            "utc_date": m.get("utcDate", ""),
            "result": result,
            "home_score": ft.get("home"),
            "away_score": ft.get("away"),
            "competition": m.get("competition", {}).get("name", ""),
            "source": "football-data",
        })

    return matches, finished, other


# ---------------------------------------------------------------------------
# ESPN
# ---------------------------------------------------------------------------

def _minute(display_clock):
    """"34'" -> 34, "90'+3'" -> 90, "105'+1'" -> 105. None if unparseable."""
    m = re.match(r"\s*(\d+)", display_clock or "")
    return int(m.group(1)) if m else None


def espn_regulation_score(comp, home_id, away_id, home_final, away_final):
    """90-minute score for a match that went to extra time, rebuilt from the goal
    list. Returns (home, away) or None if the goal list doesn't add up."""
    goals = [d for d in comp.get("details", []) if d.get("scoringPlay") and not d.get("shootout")]
    totals = {home_id: 0, away_id: 0}
    regulation = {home_id: 0, away_id: 0}
    for g in goals:
        team = (g.get("team") or {}).get("id")
        minute = _minute((g.get("clock") or {}).get("displayValue"))
        if team not in totals or minute is None:
            return None
        totals[team] += 1
        if minute <= 90:
            regulation[team] += 1
    if totals[home_id] != home_final or totals[away_id] != away_final:
        return None  # incomplete goal list — don't guess
    return regulation[home_id], regulation[away_id]


# ESPN names women's / youth sides like the senior men's team ("Arsenal"), so
# the competition slug is used to add the marker the matcher relies on.
ESPN_SIDE_SLUGS = [
    (("women", "womens", "liga-f", "frauen", "feminin", "vrouwen", "nwsl", "damallsvenskan"), "Women"),
    (("u23", "u-23"), "U23"), (("u21", "u-21"), "U21"), (("u20", "u-20"), "U20"),
    (("u19", "u-19", "youth"), "U19"), (("u17", "u-17"), "U17"),
]


def espn_side_marker(e):
    text = " ".join([
        (e.get("season") or {}).get("slug", ""),
        ((e.get("competitions") or [{}])[0].get("altGameNote") or ""),
    ]).lower()
    for keys, marker in ESPN_SIDE_SLUGS:
        if any(k in text for k in keys):
            return marker
    return ""


def parse_espn_event(e):
    comp = (e.get("competitions") or [{}])[0]
    teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
    home, away = teams.get("home"), teams.get("away")
    if not home or not away:
        return None
    st = (comp.get("status") or {}).get("type") or {}
    status = ESPN_STATUS.get(st.get("name"), "FINISHED" if st.get("completed") else st.get("name", "SCHEDULED"))

    result = home_score = away_score = None
    if status == "FINISHED":
        try:
            home_score, away_score = int(home.get("score")), int(away.get("score"))
        except (TypeError, ValueError):
            home_score = away_score = None
        if home_score is not None and st.get("name") in ("STATUS_FINAL_AET", "STATUS_FINAL_PEN"):
            reg = espn_regulation_score(comp, home["team"]["id"], away["team"]["id"], home_score, away_score)
            home_score, away_score = reg if reg else (None, None)
        if home_score is not None:
            result = "1" if home_score > away_score else "X" if home_score == away_score else "2"

    def names(c):
        t = c.get("team") or {}
        full = t.get("displayName") or t.get("name") or ""
        return full, t.get("shortDisplayName") or full

    home_team, short_home = names(home)
    away_team, short_away = names(away)
    marker = espn_side_marker(e)
    if marker:
        home_team, short_home, away_team, short_away = (
            n if marker.lower() in n.lower().split() else f"{n} {marker}"
            for n in (home_team, short_home, away_team, short_away))
    return {
        "home_team": home_team,
        "away_team": away_team,
        "short_home": short_home,
        "short_away": short_away,
        "status": status,
        "utc_date": e.get("date", ""),
        "result": result,
        "home_score": home_score,
        "away_score": away_score,
        "competition": (e.get("season") or {}).get("slug", ""),
        "source": "espn",
    }


def fetch_espn(run_date):
    """ESPN scoreboard for date-1 .. date+1 (ESPN days are US-time, so the
    window covers every UTC kick-off on run_date). Raises if every day fails."""
    d = date.fromisoformat(run_date)
    matches, errors = [], []
    seen = set()
    for offset in (-1, 0, 1):
        day = (d + timedelta(days=offset)).strftime("%Y%m%d")
        try:
            resp = requests.get(ESPN_URL, params={"dates": day, "limit": 1000}, timeout=20)
            resp.raise_for_status()
            events = resp.json().get("events", [])
        except Exception as ex:
            errors.append(f"{day}: {ex}")
            continue
        for e in events:
            if e.get("id") in seen:
                continue
            seen.add(e.get("id"))
            m = parse_espn_event(e)
            if m:
                matches.append(m)
        time.sleep(0.5)
    if len(errors) == 3:
        raise RuntimeError("ESPN: " + "; ".join(errors))
    for err in errors:
        print(f"  WARNING ESPN {err}")
    return matches


def get_results(run_date):
    """Fetch both sources, merge (football-data first), save. Raises only if
    both sources fail."""
    matches, errors = [], []
    try:
        fd, finished, other = parse_matches(fetch_matches(run_date))
        matches += fd
        print(f"  {run_date}: football-data {len(fd)} matches in window, {finished} finished, {other} not finished")
    except Exception as e:
        errors.append(f"football-data: {e}")
        print(f"  WARNING football-data failed: {e}")
    try:
        es = fetch_espn(run_date)
        matches += es
        print(f"  {run_date}: ESPN {len(es)} matches in window, {sum(1 for m in es if m['result'])} with a result")
    except Exception as e:
        errors.append(str(e))
        print(f"  WARNING ESPN failed: {e}")
    if len(errors) == 2:
        raise RuntimeError(" | ".join(errors))
    write_output(run_date, matches)
    return matches


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date.today()), help="Date in YYYY-MM-DD format")
    args = parser.parse_args()
    run_date = args.date
    print(f"fetch_results.py — date={run_date}")

    try:
        matches = get_results(run_date)
        print(f"Output: {os.path.join(TMP_DIR, f'results_{run_date}.json')}")
        done = [m for m in matches if m["result"]]
        if done:
            print("\nSample results:")
            for m in done[:5]:
                print(f"  {m['home_team']} {m['home_score']}-{m['away_score']} {m['away_team']} → {m['result']}")

    except Exception as e:
        print(f"ERROR: {e}")
        write_output(run_date, [], error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
