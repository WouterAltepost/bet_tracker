"""
scrape_vitibet.py — Scrape top 5 football predictions from vitibet.com

The site shows a table of today's quick tips. Each row with a tip cell
(class barvapodtipek*) contains the predicted outcome.

Tip class mapping:
  barvapodtipek1  → "1" (home win)
  barvapodtipek2  → "2" (away win)
  barvapodtipek10 → "1" (home win or draw — home leaning)
  barvapodtipek02 → "2" (draw or away win — away leaning)
  barvapodtipek0  → "X" (draw)

Output: .tmp/predictions_vitibet_{date}.json
"""

import asyncio
import json
import os
from datetime import date

from playwright.async_api import async_playwright

SITE = "vitibet"
URL = "https://www.vitibet.com/index.php?clanek=quicktips&sekce=fotbal&lang=en"

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


def tip_class_to_prediction(classes):
    """
    Parse barvapodtipek* CSS class to 1/X/2.
    vitibet uses: 1=home, 0=draw, 2=away, 10=home+draw, 02=draw+away
    """
    for cls in (classes or []):
        if cls.startswith("barvapodtipek"):
            suffix = cls.replace("barvapodtipek", "")
            if suffix == "1":
                return "1"
            if suffix == "2":
                return "2"
            if suffix in ("0", "x", "X"):
                return "X"
            if suffix == "10":
                return "1"   # home win or draw → lean home
            if suffix == "02":
                return "2"   # draw or away → lean away
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
    Target: second table on the page (index 1), rows that have a barvapodtipek* cell.
    Columns: date(0) | blank(1) | home_team(2) | away_team(3) | blank(4) | ...
    Tip cell: any td with class starting with 'barvapodtipek'
    """
    predictions = []

    rows = await page.query_selector_all("table tr")
    for row in rows:
        cells = await row.query_selector_all("td")
        if len(cells) < 4:
            continue

        # Find the tip cell by class
        tip_cell = None
        tip_classes = None
        for cell in cells:
            classes = await cell.get_attribute("class")
            if classes and "barvapodtipek" in classes:
                tip_cell = cell
                tip_classes = classes.split()
                break

        if not tip_cell:
            continue

        home_team = (await cells[2].inner_text()).strip()
        away_team = (await cells[3].inner_text()).strip()

        if not home_team or not away_team:
            continue

        prediction = tip_class_to_prediction(tip_classes)
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
