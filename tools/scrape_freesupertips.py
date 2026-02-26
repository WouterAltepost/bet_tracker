"""
scrape_freesupertips.py — Scrape top 5 1X2 football predictions from freesupertips.com

The listing page shows featured match prediction links (a.Prediction).
Each link leads to a match detail page where the main prediction is in:
    div.IndividualTipPrediction > h4
    e.g. "Atalanta to Win", "Draw", "Borussia Dortmund to Win"

Non-1X2 tips (BTTS, over/under goals, etc.) are skipped. All available links
are checked until 5 valid 1X2 predictions are found. If fewer than 5 are
available that day, the output includes what was found with a warning.

Output: .tmp/predictions_freesupertips_{date}.json
"""

import asyncio
import json
import os
from datetime import date

from playwright.async_api import async_playwright

SITE = "freesupertips"
LISTING_URL = "https://www.freesupertips.com/predictions/"
BASE_URL = "https://www.freesupertips.com"

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


NON_1X2_PATTERNS = [
    "both teams to score", "btts",
    "over ", "under ",
    "asian handicap", "handicap",
    "clean sheet",
    "first goalscorer", "last goalscorer", "anytime scorer",
    "correct score",
    "half time", "half-time",
    "total goals",
]


def is_1x2_tip(tip_text):
    """Return True if the tip looks like a 1X2 prediction, False for BTTS/goals/etc."""
    t = tip_text.strip().lower()
    for pattern in NON_1X2_PATTERNS:
        if pattern in t:
            return False
    return True


def parse_prediction(tip_text, home_team, away_team):
    """
    Convert tip text like "Atalanta to Win" or "Draw" to 1/X/2.
    Uses case-insensitive partial matching against team names.
    """
    t = tip_text.strip().lower()

    if "draw" in t:
        return "X"

    # Check if team name appears in tip text
    home_lower = home_team.lower()
    away_lower = away_team.lower()

    # Try exact and partial match — compare significant words
    home_words = set(home_lower.split())
    away_words = set(away_lower.split())
    tip_words = set(t.split())

    home_overlap = home_words & tip_words
    away_overlap = away_words & tip_words

    if home_overlap and not away_overlap:
        return "1"
    if away_overlap and not home_overlap:
        return "2"

    # Fallback: check if tip contains home/away team name as substring
    if any(word in t for word in home_lower.split() if len(word) > 3):
        return "1"
    if any(word in t for word in away_lower.split() if len(word) > 3):
        return "2"

    return None


async def scrape():
    run_date = str(date.today())
    print(f"[{SITE}] Scraping {LISTING_URL} for {run_date} ...")

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
            await page.goto(LISTING_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            # Get all featured match links with team names
            match_links = await get_match_links(page)

            if not match_links:
                raise ValueError("No match links found on listing page")

            print(f"[{SITE}] Found {len(match_links)} match links, fetching 1X2 tips...")

            predictions = []
            for match in match_links:
                if len(predictions) >= 5:
                    break
                try:
                    tip = await get_match_tip(context, match["url"])
                    if not tip:
                        print(f"  [{SITE}] No tip found for {match['home']} vs {match['away']}, skipping")
                        continue

                    if not is_1x2_tip(tip):
                        print(f"  [{SITE}] Skipping non-1X2 tip '{tip}' for {match['home']} vs {match['away']}")
                        continue

                    prediction = parse_prediction(tip, match["home"], match["away"])
                    if not prediction:
                        print(f"  [{SITE}] Could not parse 1X2 tip '{tip}' for {match['home']} vs {match['away']}, skipping")
                        continue

                    predictions.append({
                        "home_team": match["home"],
                        "away_team": match["away"],
                        "prediction": prediction,
                    })
                    print(f"  [{SITE}] {match['home']} vs {match['away']} → {prediction} (tip: {tip})")

                except Exception as e:
                    print(f"  [{SITE}] Error processing {match.get('home', '?')} vs {match.get('away', '?')}: {e}")
                    continue

            if 0 < len(predictions) < 5:
                print(f"[{SITE}] WARNING: Only {len(predictions)}/5 valid 1X2 predictions found — remaining tips were non-1X2 or unparseable")

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


async def get_match_links(page):
    """Extract all match links with team names from the listing page."""
    links = []
    anchors = await page.query_selector_all("a.Prediction")

    for anchor in anchors:
        href = await anchor.get_attribute("href")
        if not href:
            continue

        # Full URL
        url = href if href.startswith("http") else BASE_URL + href

        # Team names are in div.Team elements inside the anchor
        team_els = await anchor.query_selector_all("div.Team")
        if len(team_els) >= 2:
            home = (await team_els[0].inner_text()).strip()
            away = (await team_els[1].inner_text()).strip()
        else:
            # Fallback: parse from href slug "team-a-vs-team-b-predictions-..."
            slug = href.split("/predictions/")[-1].rstrip("/")
            if "-vs-" in slug:
                parts = slug.split("-vs-")[0:2]
                home = parts[0].replace("-", " ").title()
                away = parts[1].split("-predictions")[0].replace("-", " ").title()
            else:
                continue

        if home and away:
            links.append({"home": home, "away": away, "url": url})

    return links


async def get_match_tip(context, url):
    """
    Visit individual match page and return the tip text from
    div.IndividualTipPrediction > h4
    """
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        tip_el = await page.query_selector("div.IndividualTipPrediction h4")
        if tip_el:
            return (await tip_el.inner_text()).strip()

        return None
    finally:
        await page.close()


if __name__ == "__main__":
    asyncio.run(scrape())
