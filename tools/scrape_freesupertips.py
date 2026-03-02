"""
scrape_freesupertips.py — Scrape top 5 1X2 football predictions from freesupertips.com

The listing page groups predictions by league section (H2 headings). We scroll
the full page to trigger any lazy-loaded sections, then pick the first prediction
from each section to get variety across competitions. We keep collecting until
we have 5 or exhaust all available matches.

Each match link leads to a detail page where the main prediction is in:
    div.IndividualTipPrediction > h4
    e.g. "Atalanta to Win", "Draw", "Real Madrid to Win and Under 2.5 Match Goals"

Tips are converted to 1/X/2. Compound tips (e.g. "Team to Win and Under 2.5")
have their 1X2 component extracted before falling back to skipping them.
Non-1X2-only tips (BTTS, correct score, etc.) are skipped.

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


def extract_1x2_component(tip_text):
    """
    Try to extract a 1X2 part from a compound tip like
    'Real Madrid to Win and Under 2.5 Match Goals'.
    Splits on ' and ' and returns the first part that passes is_1x2_tip.
    Returns None if no 1X2 component found.
    """
    for part in tip_text.split(" and "):
        part = part.strip()
        if part and is_1x2_tip(part):
            return part
    return None


def parse_prediction(tip_text, home_team, away_team):
    """
    Convert tip text like "Atalanta to Win" or "Draw" to 1/X/2.
    Uses case-insensitive partial matching against team names.
    """
    t = tip_text.strip().lower()

    if "draw" in t:
        return "X"

    home_lower = home_team.lower()
    away_lower = away_team.lower()

    home_words = set(home_lower.split())
    away_words = set(away_lower.split())
    tip_words = set(t.split())

    home_overlap = home_words & tip_words
    away_overlap = away_words & tip_words

    if home_overlap and not away_overlap:
        return "1"
    if away_overlap and not home_overlap:
        return "2"

    # Fallback: substring match on significant words
    if any(word in t for word in home_lower.split() if len(word) > 3):
        return "1"
    if any(word in t for word in away_lower.split() if len(word) > 3):
        return "2"

    return None


async def scroll_page_fully(page):
    """Scroll to bottom in steps to trigger lazy-loaded sections."""
    prev_height = -1
    for _ in range(15):
        height = await page.evaluate("() => document.body.scrollHeight")
        if height == prev_height:
            break
        prev_height = height
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(700)
    await page.evaluate("() => window.scrollTo(0, 0)")
    await page.wait_for_timeout(300)


async def get_match_links(page):
    """
    Extract match links grouped by league section (H2/H3/H4 headings).
    Returns one link per section first for competition variety, then fills
    from remaining matches in each section until the caller has enough.
    """
    await scroll_page_fully(page)

    result = await page.evaluate("""
        () => {
            const groups = {};
            const groupOrder = [];
            let currentGroup = '__top__';

            const allEls = document.body.querySelectorAll('h2, h3, h4, a.Prediction');
            for (const el of allEls) {
                const tag = el.tagName;
                if (tag === 'H2' || tag === 'H3' || tag === 'H4') {
                    const text = el.textContent.trim();
                    if (text.length > 2 && text.length < 80) {
                        currentGroup = text;
                        if (!groups[currentGroup]) {
                            groups[currentGroup] = [];
                            groupOrder.push(currentGroup);
                        }
                    }
                } else if (tag === 'A') {
                    const teamEls = el.querySelectorAll('.Team, div.Team');
                    const home = teamEls[0] ? teamEls[0].textContent.trim() : '';
                    const away = teamEls[1] ? teamEls[1].textContent.trim() : '';
                    const href = el.getAttribute('href') || '';
                    if (home && away && href) {
                        if (!groups[currentGroup]) {
                            groups[currentGroup] = [];
                            groupOrder.push(currentGroup);
                        }
                        groups[currentGroup].push({ home, away, href });
                    }
                }
            }
            return { groups, groupOrder };
        }
    """)

    groups = result.get("groups", {})
    group_order = result.get("groupOrder", [])
    total = sum(len(v) for v in groups.values())
    print(f"[{SITE}] Found {total} prediction link(s) across {len(group_order)} section(s): {group_order}")

    if not groups:
        return []

    links = []
    seen_urls = set()

    # Pass 1: first link from each section (variety across competitions)
    for section in group_order:
        for item in groups[section]:
            href = item["href"]
            url = href if href.startswith("http") else BASE_URL + href
            if url not in seen_urls:
                links.append({"home": item["home"], "away": item["away"], "url": url, "section": section})
                seen_urls.add(url)
                break  # one per section in pass 1

    # Pass 2: fill remaining slots with second+ matches from each section
    for section in group_order:
        if len(links) >= 10:  # generous cap — caller stops at 5
            break
        for item in groups[section][1:]:
            href = item["href"]
            url = href if href.startswith("http") else BASE_URL + href
            if url not in seen_urls:
                links.append({"home": item["home"], "away": item["away"], "url": url, "section": section})
                seen_urls.add(url)

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

            match_links = await get_match_links(page)

            if not match_links:
                raise ValueError("No match links found on listing page")

            print(f"[{SITE}] Processing {len(match_links)} candidate match(es)...")

            predictions = []
            for match in match_links:
                if len(predictions) >= 5:
                    break
                section = match.get("section", "")
                try:
                    tip = await get_match_tip(context, match["url"])
                    if not tip:
                        print(f"  [{SITE}] No tip found for {match['home']} vs {match['away']}, skipping")
                        continue

                    effective_tip = tip
                    if not is_1x2_tip(tip):
                        # Try to salvage a 1X2 component from compound tips
                        effective_tip = extract_1x2_component(tip)
                        if effective_tip:
                            print(f"  [{SITE}] Compound tip '{tip}' → using 1X2 part '{effective_tip}'")
                        else:
                            print(f"  [{SITE}] Skipping non-1X2 tip '{tip}' for {match['home']} vs {match['away']}")
                            continue

                    prediction = parse_prediction(effective_tip, match["home"], match["away"])
                    if not prediction:
                        print(f"  [{SITE}] Could not parse tip '{effective_tip}' for {match['home']} vs {match['away']}, skipping")
                        continue

                    predictions.append({
                        "home_team": match["home"],
                        "away_team": match["away"],
                        "prediction": prediction,
                    })
                    print(f"  [{SITE}] [{section}] {match['home']} vs {match['away']} → {prediction} (tip: {tip})")

                except Exception as e:
                    print(f"  [{SITE}] Error processing {match.get('home', '?')} vs {match.get('away', '?')}: {e}")
                    continue

            if 0 < len(predictions) < 5:
                print(f"[{SITE}] WARNING: Only {len(predictions)}/5 valid 1X2 predictions found — site may have fewer listings today")

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


if __name__ == "__main__":
    asyncio.run(scrape())
