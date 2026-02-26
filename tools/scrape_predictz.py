"""
scrape_predictz.py — Scrape top 5 football predictions from predictz.com

The site shows match tables per competition (Champions League, Premier League, etc.)
Each row has: Match Name | Predicted Score | H/D/A badge | View Tip link
Badge values: H = Home Win (1), D = Draw (X), A = Away Win (2)

Output: .tmp/predictions_predictz_{date}.json
"""

import asyncio
import json
import os
from datetime import date

from playwright.async_api import async_playwright

SITE = "predictz"
URL = "https://www.predictz.com/"

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


def badge_to_prediction(badge_text):
    """Convert H/D/A badge to 1/X/2."""
    t = badge_text.strip().upper()
    if t == "H":
        return "1"
    if t == "D":
        return "X"
    if t == "A":
        return "2"
    return None


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
            await page.wait_for_timeout(2000)

            predictions = await extract_predictions(page)

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


async def extract_predictions(page):
    """
    Target: tr.pzcnt rows that contain a div.neonboxvsml badge.
    Row structure: [Match Name] [Predicted Score] [H/D/A badge] [View Tip]
    Match name format: "Team A v Team B" — split on " v "
    """
    predictions = []

    rows = await page.query_selector_all("tr.pzcnt")
    for row in rows:
        badge = await row.query_selector("div.neonboxvsml")
        if not badge:
            continue

        cells = await row.query_selector_all("td")
        if not cells:
            continue

        match_text = (await cells[0].inner_text()).strip()
        badge_text = (await badge.inner_text()).strip()

        prediction = badge_to_prediction(badge_text)
        if not prediction:
            continue

        # Split "Team A v Team B" on " v "
        if " v " in match_text:
            parts = match_text.split(" v ", 1)
            home_team = parts[0].strip()
            away_team = parts[1].strip()
        else:
            continue

        if home_team and away_team:
            predictions.append({
                "home_team": home_team,
                "away_team": away_team,
                "prediction": prediction,
            })

    return predictions


if __name__ == "__main__":
    asyncio.run(scrape())
