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

# Competition name substrings that indicate leagues not covered by football-data.org free API.
SKIP_SECTIONS = {
    # English lower leagues
    "championship", "league one", "league two", "national league",
    # South American
    "argentina", "brazil", "chile", "colombia", "peru", "uruguay",
    "paraguay", "ecuador", "venezuela", "bolivia", "copa",
    # Other non-API regions
    "turkish", "greek", "australian", "chinese", "mls", "mexican", "saudi",
    "j league", "k league",
}

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

    # Comprehensive stealth init script — hides common automation fingerprints
    STEALTH_SCRIPT = """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-GB', 'en']});
        window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}, app: {}};
        const origQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (p) =>
            p.name === 'notifications'
                ? Promise.resolve({state: Notification.permission})
                : origQuery(p);
    """

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-size=1280,900",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-GB",
            timezone_id="Europe/London",
            extra_http_headers={
                "Accept-Language": "en-GB,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            },
        )
        await context.add_init_script(STEALTH_SCRIPT)
        page = await context.new_page()

        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            # Wait for match rows to appear, fall back to networkidle then fixed delay
            try:
                await page.wait_for_selector("tr.pzcnt", timeout=15000)
            except Exception:
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
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
    Target: tr.pzcnt rows that contain a div.neonboxvsml badge.
    Row structure: [Match Name] [Predicted Score] [H/D/A badge] [View Tip]
    Match name format: "Team A v Team B" — split on " v "

    We walk all <tr> elements in DOM order to track competition section headings
    (non-pzcnt rows with short text and no " v ") so we can skip uncovered leagues.
    """
    # Walk all tr elements in order to capture competition heading context
    raw_items = await page.evaluate("""() => {
        const items = [];
        let currentSection = '';

        document.querySelectorAll('tr').forEach(tr => {
            if (tr.classList.contains('pzcnt')) {
                const badge = tr.querySelector('div.neonboxvsml');
                if (!badge) return;
                const cells = tr.querySelectorAll('td');
                if (!cells.length) return;
                items.push({
                    matchText: cells[0].textContent.trim(),
                    badgeText: badge.textContent.trim(),
                    section: currentSection,
                });
            } else {
                // Potential competition header row: short text, no " v " separator
                const text = tr.textContent.trim().split('\\n')[0].trim();
                if (text.length > 2 && text.length < 70 && !text.includes(' v ')) {
                    currentSection = text;
                }
            }
        });

        return items;
    }""")

    predictions = []
    for item in raw_items:
        match_text = item.get("matchText", "")
        badge_text = item.get("badgeText", "")
        section = item.get("section", "")

        prediction = badge_to_prediction(badge_text)
        if not prediction:
            continue

        if " v " not in match_text:
            continue
        parts = match_text.split(" v ", 1)
        home_team = parts[0].strip()
        away_team = parts[1].strip()
        if not home_team or not away_team:
            continue

        # Skip matches from leagues not covered by football-data.org free API
        section_lower = section.lower()
        if any(skip in section_lower for skip in SKIP_SECTIONS):
            print(f"  [{SITE}] Skipping {home_team} vs {away_team} — uncovered league ({section})")
            continue

        predictions.append({
            "home_team": home_team,
            "away_team": away_team,
            "prediction": prediction,
        })
        print(f"  [{SITE}] [{section}] {home_team} vs {away_team} → {prediction}")

    return predictions


if __name__ == "__main__":
    asyncio.run(scrape())
