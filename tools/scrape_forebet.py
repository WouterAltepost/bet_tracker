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
    Target: div.rcnt elements (each is a match prediction row).
    Each row contains:
      span.homeTeam  — home team
      span.awayTeam  — away team
      span.forepr    — 1/X/2 prediction
    """
    predictions = []

    rows = await page.query_selector_all("div.rcnt")
    for row in rows:
        home_el = await row.query_selector("span.homeTeam")
        away_el = await row.query_selector("span.awayTeam")
        pred_el = await row.query_selector("span.forepr")

        if not home_el or not away_el or not pred_el:
            continue

        home_team = (await home_el.inner_text()).strip()
        away_team = (await away_el.inner_text()).strip()
        prediction = (await pred_el.inner_text()).strip().upper()

        if not home_team or not away_team:
            continue

        # Normalise to 1/X/2
        if prediction not in ("1", "X", "2"):
            continue

        predictions.append({
            "home_team": home_team,
            "away_team": away_team,
            "prediction": prediction,
        })

    return predictions


if __name__ == "__main__":
    asyncio.run(scrape())
