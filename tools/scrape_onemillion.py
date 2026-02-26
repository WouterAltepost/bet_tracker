"""
scrape_onemillion.py — Scrape top 5 football predictions from onemillionpredictions.com

The site shows 1X2 odds per match. The predicted outcome is the column with
the lowest odds (most likely result: 1 = home win, X = draw, 2 = away win).

Teams are separated by <br/> inside each table cell.

Output: .tmp/predictions_onemillion_{date}.json
"""

import asyncio
import json
import os
from datetime import date

from playwright.async_api import async_playwright

SITE = "onemillion"
URL = "https://onemillionpredictions.com/"

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


def pick_from_odds(odds_1, odds_x, odds_2):
    """Return '1', 'X', or '2' — whichever has the lowest (most likely) odds."""
    try:
        o1 = float(odds_1)
        ox = float(odds_x)
        o2 = float(odds_2)
        if o1 <= ox and o1 <= o2:
            return "1"
        if ox <= o1 and ox <= o2:
            return "X"
        return "2"
    except (ValueError, TypeError):
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
                    f"No predictions extracted. Debug files saved:\n"
                    f"  Screenshot: {screenshot_path}\n"
                    f"  HTML: {html_path}"
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
    The site renders a Ninja Table with CSS classes:
      ninja_clmn_nm_teams  — teams cell (home<br/>away)
      ninja_clmn_nm_1      — home win odds
      ninja_clmn_nm_x      — draw odds
      ninja_clmn_nm_2      — away win odds

    Prediction = column with the lowest odds.
    """
    predictions = []

    rows = await page.query_selector_all("table tr")
    for row in rows:
        teams_cell = await row.query_selector("td.ninja_clmn_nm_teams")
        cell_1 = await row.query_selector("td.ninja_clmn_nm_1")
        cell_x = await row.query_selector("td.ninja_clmn_nm_x")
        cell_2 = await row.query_selector("td.ninja_clmn_nm_2")

        if not teams_cell or not cell_1 or not cell_x or not cell_2:
            continue

        # Teams are separated by <br/> — get_text with separator preserves it
        teams_text = await teams_cell.inner_text()
        teams = [t.strip() for t in teams_text.strip().splitlines() if t.strip()]
        if len(teams) < 2:
            continue

        home_team = teams[0]
        away_team = teams[1]

        odds_1 = (await cell_1.inner_text()).strip()
        odds_x = (await cell_x.inner_text()).strip()
        odds_2 = (await cell_2.inner_text()).strip()

        # Skip league-separator rows (no numeric odds)
        if not any(o.replace(".", "").isdigit() for o in [odds_1, odds_x, odds_2]):
            continue

        prediction = pick_from_odds(odds_1, odds_x, odds_2)
        if not prediction:
            continue

        predictions.append({
            "home_team": home_team,
            "away_team": away_team,
            "prediction": prediction,
        })

    return predictions


if __name__ == "__main__":
    asyncio.run(scrape())
