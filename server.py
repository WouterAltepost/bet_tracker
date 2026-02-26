"""
server.py — HTTP wrapper for bet_tracker morning/evening runs.

Endpoints:
  POST /run-morning  — run all 6 predictors and write predictions to Google Sheet
  POST /run-evening  — fetch results, score predictions, update sheet + leaderboard

Authentication:
  All endpoints require:  Authorization: Bearer <RAILWAY_API_KEY>

Designed to be triggered by n8n on a daily schedule.
"""

import os
import subprocess
import sys
from datetime import date

from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()

app = Flask(__name__)

API_KEY = os.environ.get("RAILWAY_API_KEY")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
STEP_TIMEOUT = 300  # seconds per script — 5 minutes


# ---------------------------------------------------------------------------
# Playwright browser install (Railway filesystem is ephemeral — runs on startup)
# ---------------------------------------------------------------------------

def install_playwright_browsers():
    print("[startup] Installing Playwright browsers...", flush=True)
    result = subprocess.run(
        [PYTHON, "-m", "playwright", "install", "--with-deps", "chromium"],
        capture_output=True,
        text=True,
        cwd=BASE_DIR,
    )
    if result.returncode == 0:
        print("[startup] Playwright browsers ready.", flush=True)
    else:
        print(f"[startup] WARNING: playwright install failed:\n{result.stderr}", flush=True)


install_playwright_browsers()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_api_key():
    """Return a 401 response if the Authorization header is wrong, else None."""
    if not API_KEY:
        return jsonify({"error": "RAILWAY_API_KEY is not configured on the server"}), 500
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {API_KEY}":
        return jsonify({"error": "Unauthorized — invalid or missing API key"}), 401
    return None


def run_step(cmd, label):
    """
    Run a subprocess command and return a result dict.
    Never raises — all errors are captured and returned in the dict.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=BASE_DIR,
            timeout=STEP_TIMEOUT,
        )
        # Combine stdout + stderr; keep last 3000 chars to avoid huge responses
        combined = (result.stdout or "") + (result.stderr or "")
        return {
            "step": label,
            "status": "ok" if result.returncode == 0 else "error",
            "returncode": result.returncode,
            "output": combined[-3000:].strip(),
        }
    except subprocess.TimeoutExpired:
        return {
            "step": label,
            "status": "timeout",
            "returncode": -1,
            "output": f"Timed out after {STEP_TIMEOUT}s",
        }
    except Exception as exc:
        return {
            "step": label,
            "status": "exception",
            "returncode": -1,
            "output": str(exc),
        }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route("/run-morning", methods=["POST"])
def run_morning():
    """
    Run all 6 predictors in sequence, then write predictions to Google Sheet.

    Scraper failures are non-fatal — the run continues and SCRAPE_FAILED is
    written to the sheet for that site. Only update_sheet is skipped if all
    scrapers failed (nothing to write).
    """
    err = check_api_key()
    if err:
        return err

    run_date = str(date.today())
    print(f"[morning] Starting run for {run_date}", flush=True)

    steps_to_run = [
        ([PYTHON, "tools/scrape_forebet.py"],           "scrape_forebet"),
        ([PYTHON, "tools/scrape_predictz.py"],          "scrape_predictz"),
        ([PYTHON, "tools/scrape_onemillion.py"],        "scrape_onemillion"),
        ([PYTHON, "tools/scrape_vitibet.py"],           "scrape_vitibet"),
        ([PYTHON, "tools/scrape_freesupertips.py"],     "scrape_freesupertips"),
        ([PYTHON, "tools/generate_claude_predictions.py"], "generate_claude"),
        ([PYTHON, "tools/update_sheet.py", "--mode=predictions"], "update_sheet"),
    ]

    results = []
    for cmd, label in steps_to_run:
        print(f"[morning] Running {label}...", flush=True)
        result = run_step(cmd, label)
        results.append(result)
        print(f"[morning] {label} → {result['status']}", flush=True)

    scraper_results = results[:-1]
    sheet_result = results[-1]

    # Overall status: ok only if sheet update succeeded
    # (individual scraper failures are expected and handled gracefully)
    if sheet_result["status"] == "ok":
        overall = "ok"
    elif any(r["status"] == "ok" for r in scraper_results):
        overall = "partial"  # some scrapers worked, sheet write failed
    else:
        overall = "error"

    return jsonify({
        "date": run_date,
        "run": "morning",
        "status": overall,
        "steps": results,
    })


@app.route("/run-evening", methods=["POST"])
def run_evening():
    """
    Fetch results → score predictions → update sheet + rebuild leaderboard.

    Steps are sequential and dependent: if fetch_results fails, scoring will
    also fail (no results file). All steps always run so every error is reported.
    """
    err = check_api_key()
    if err:
        return err

    run_date = str(date.today())
    print(f"[evening] Starting run for {run_date}", flush=True)

    steps_to_run = [
        ([PYTHON, "tools/fetch_results.py"],               "fetch_results"),
        ([PYTHON, "tools/score_predictions.py"],           "score_predictions"),
        ([PYTHON, "tools/update_sheet.py", "--mode=results"], "update_sheet"),
    ]

    results = []
    for cmd, label in steps_to_run:
        print(f"[evening] Running {label}...", flush=True)
        result = run_step(cmd, label)
        results.append(result)
        print(f"[evening] {label} → {result['status']}", flush=True)

    overall = "ok" if all(r["status"] == "ok" for r in results) else "error"

    return jsonify({
        "date": run_date,
        "run": "evening",
        "status": overall,
        "steps": results,
    })


@app.route("/health", methods=["GET"])
def health():
    """Simple health check — no auth required."""
    return jsonify({"status": "ok", "date": str(date.today())})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
