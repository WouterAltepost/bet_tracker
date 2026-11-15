"""
scrape_forebet.py — Scrape top 5 football predictions from forebet.com

The site renders match rows as div.rcnt containing:
  span.homeTeam  — home team name
  span.awayTeam  — away team name
  span.forepr    — 1X2 prediction ("1", "X", or "2")

Output: .tmp/predictions_forebet_{date}.json
"""

import asyncio
import json
import os
from datetime import date

from playwright.async_api import async_playwright

SITE = "forebet"
URL = "https://www.forebet.com/"

# Fixtures we never pick (results sources don't separate these from senior men's sides reliably).
SKIP_KEYWORDS = ("women", "femen", "frauen", "u17", "u18", "u19", "u20", "u21", "u23", "youth")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(BASE_DIR, ".tmp")


def write_output(run_date, predictions, error=None):
    os.makedirs(TMP_DIR, exist_ok=True)
    output = {
        "date": run_date,
        "site": SITE,
        "status": "ok" if not error else "failed",
        "error": error,
        "predictions": predictions,
    }
    path = os.path.join(TMP_DIR, f"predictions_{SITE}_{run_date}.json")
    with open(path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    return path


async def scrape():
    run_date = str(date.today())
    print(f"[{SITE}] Scraping {URL} for {run_date} ...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            predictions = await extract_predictions(page, run_date)

            if not predictions:
                html_path = os.path.join(TMP_DIR, f"debug_{SITE}_{run_date}.html")
                with open(html_path, "w") as f:
                    f.write(await page.content())
                screenshot_path = os.path.join(TMP_DIR, f"debug_{SITE}_{run_date}.png")
                await page.screenshot(path=screenshot_path, full_page=True)
                raise ValueError(
                    f"No predictions extracted. Debug files:\n"
                    f"  Screenshot: {screenshot_path}\n  HTML: {html_path}"
                )

            print(f"[{SITE}] Extracted {len(predictions)} predictions")
            path = write_output(run_date, predictions[:5])
            print(f"[{SITE}] Output: {path}")

        except Exception as e:
            error_msg = str(e)
            print(f"[{SITE}] ERROR: {error_msg}")
            screenshot_path = os.path.join(TMP_DIR, f"debug_{SITE}_{run_date}.png")
            try:
                await page.screenshot(path=screenshot_path, full_page=True)
                print(f"[{SITE}] Debug screenshot: {screenshot_path}")
            except Exception:
                pass
            path = write_output(run_date, [], error=error_msg)
            print(f"[{SITE}] Failed output written to {path}")

        finally:
            await browser.close()


async def extract_predictions(page, run_date):
    """
    Target: div.rcnt elements (each is a match prediction row).
    Each row contains:
      span.homeTeam / span.awayTeam — teams
      span.forepr                   — 1/X/2 prediction
      .fprc span x3                 — Forebet's 1/X/2 probabilities (%)
      time[itemprop=startDate]      — match date (YYYY-MM-DD)
      .shortagDiv img[onclick]      — getstag(..., 'League name', ...)

    "Top 5" = today's matches ranked by the probability Forebet gives its own
    pick (most confident first). Women's/youth fixtures are skipped.
    """
    rows = await page.evaluate("""() => [...document.querySelectorAll('div.rcnt')].map(r => {
        const t = s => (r.querySelector(s) || {}).textContent || '';
        const time = r.querySelector("time[itemprop='startDate']");
        const tag = r.querySelector('.shortagDiv img[onclick]');
        const m = tag ? tag.getAttribute('onclick').match(/getstag\\([^,]*,[^,]*,'([^']*)','([^']*)'/) : null;
        return {
            home: t('span.homeTeam').trim(),
            away: t('span.awayTeam').trim(),
            pred: t('span.forepr').trim().toUpperCase(),
            date: time ? time.getAttribute('datetime') : null,
            probs: [...r.querySelectorAll('.fprc span')].map(x => parseInt(x.textContent, 10)),
            league: m ? (m[1] ? m[1] + ' ' : '') + m[2] : '',
        };
    })""")

    predictions = []
    seen = set()
    for r in rows:
        home_team, away_team, prediction = r["home"], r["away"], r["pred"]
        if not home_team or not away_team or prediction not in ("1", "X", "2"):
            continue
        # The homepage can include matches from other days — keep today's only.
        if r["date"] and r["date"][:10] != run_date:
            continue
        if any(k in f"{r['league']} {home_team} {away_team}".lower() for k in SKIP_KEYWORDS):
            continue
        if (home_team, away_team) in seen:
            continue
        seen.add((home_team, away_team))
        probs = r["probs"] if len(r["probs"]) == 3 else [0, 0, 0]
        confidence = probs["1X2".index(prediction)]
        predictions.append({
            "home_team": home_team,
            "away_team": away_team,
            "prediction": prediction,
            "confidence": confidence,
            "league": r["league"],
        })

    predictions.sort(key=lambda p: -p["confidence"])  # stable: page order breaks ties
    for p in predictions[:5]:
        print(f"  [{SITE}] {p['confidence']}% {p['home_team']} vs {p['away_team']} → {p['prediction']} ({p['league']})")
    return predictions


if __name__ == "__main__":
    asyncio.run(scrape())
