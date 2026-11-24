"""
scrape_vitibet.py — Scrape top 5 football predictions from vitibet.com

The quick-tips page was redesigned (2026) into a livescore-style list of every
match of the day with Vitibet's 1/X/2 percentages, an INDEX strength value and
a tip badge. See extract_predictions() for the structure and the ranking rule.

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


# Vitibet league IDs are API-Football IDs. These are the competitions that
# football-data.org's free tier can score (see fetch_results.py).
SKIP_KEYWORDS = ("women", "femen", "frauen", "u17", "u18", "u19", "u20", "u21", "u23", "youth")

SCORABLE_LEAGUE_IDS = {
    39,   # England: Premier League
    40,   # England: Championship
    140,  # Spain: La Liga
    78,   # Germany: Bundesliga
    135,  # Italy: Serie A
    61,   # France: Ligue 1
    88,   # Netherlands: Eredivisie
    94,   # Portugal: Primeira Liga
    71,   # Brazil: Serie A
    2,    # UEFA Champions League
    13,   # Copa Libertadores
    1,    # FIFA World Cup
    4,    # UEFA European Championship
}

TIP_FALLBACK = {"1": "1", "2": "2", "X": "X", "0": "X", "1X": "1", "10": "1", "X2": "2", "02": "2"}


def pick_prediction(probs, tip):
    """Most likely single outcome from Vitibet's own 1/X/2 percentages.
    Falls back to the tip badge (double-chance tips lean to the non-draw side)."""
    if probs and all(k in probs for k in ("1", "X", "2")):
        return max(("1", "X", "2"), key=lambda k: probs[k])
    return TIP_FALLBACK.get((tip or "").strip().upper())


async def scrape():
    run_date = str(date.today())
    os.makedirs(TMP_DIR, exist_ok=True)  # debug files are written before write_output()
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
            await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            try:
                await page.wait_for_selector("a.livescore-match-row", timeout=15000)
            except Exception:
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
    Vitibet (redesigned 2026) renders a livescore-style list:
      div.livescore-matches[data-date]           — one block per day
        div.livescore-league[data-lid]           — league (API-Football id), .league-title
          a.livescore-match-row[data-status]     — scheduled | live | finished
            .livescore-team-name x2              — home, away
            .idx-val                             — Vitibet INDEX (strength of the edge)
            .prob-item.pct-item (.prob-head/.prob-val) — 1/X/2 percentages
            .tip-indicator-circle                — tip badge: 1, 2, 1X, X2

    "Top 5" = Vitibet's own "sort by INDEX" ordering (largest |INDEX| first),
    restricted to today's not-yet-started matches, preferring scorable leagues
    and then Vitibet's highlighted top leagues.
    """
    raw = await page.evaluate("""(runDate) => {
        const out = [];
        document.querySelectorAll('div.livescore-matches').forEach(day => {
            if (day.dataset.date !== runDate) return;
            day.querySelectorAll('div.livescore-league').forEach(lg => {
                const title = (lg.querySelector('.league-title') || {}).textContent || '';
                const lid = parseInt(lg.dataset.lid || '0', 10);
                const top = lg.classList.contains('top-league');
                lg.querySelectorAll('a.livescore-match-row').forEach(row => {
                    const names = [...row.querySelectorAll('.livescore-match-teams-col .livescore-team-name')]
                        .map(e => e.textContent.trim());
                    const probs = {};
                    row.querySelectorAll('.livescore-match-prob-col .pct-item').forEach(it => {
                        const k = (it.querySelector('.prob-head') || {}).textContent;
                        const v = (it.querySelector('.prob-val') || {}).textContent;
                        if (k && v) probs[k.trim()] = parseFloat(v);
                    });
                    const idx = (row.querySelector('.idx-val') || {}).textContent;
                    const tip = (row.querySelector('.livescore-match-circle-col .tip-indicator-circle') || {}).textContent;
                    const t = row.querySelector('.local-time');
                    out.push({
                        league: title.trim(), lid, top,
                        status: row.dataset.status || '',
                        home: names[0] || '', away: names[1] || '',
                        index: idx ? parseFloat(idx) : null,
                        probs, tip: (tip || '').trim(),
                        kickoff: t ? t.dataset.time : null,
                    });
                });
            });
        });
        return out;
    }""", run_date)

    print(f"[{SITE}] {len(raw)} matches listed for {run_date}")
    candidates = []
    for m in raw:
        if m["status"] != "scheduled" or not m["home"] or not m["away"]:
            continue
        if m["index"] is None or not m["tip"]:
            continue  # no Vitibet tip for this match
        if any(k in f"{m['league']} {m['home']} {m['away']}".lower() for k in SKIP_KEYWORDS):
            continue  # women's / youth — results sources can't tell them apart reliably
        prediction = pick_prediction(m["probs"], m["tip"])
        if not prediction:
            continue
        m["prediction"] = prediction
        m["scorable"] = m["lid"] in SCORABLE_LEAGUE_IDS
        candidates.append(m)

    candidates.sort(key=lambda m: (not m["scorable"], not m["top"], -abs(m["index"])))
    print(f"[{SITE}] {len(candidates)} tipped, not-yet-started; "
          f"{sum(m['scorable'] for m in candidates)} in scorable leagues")

    predictions = []
    for m in candidates[:5]:
        print(f"  [{SITE}] [{m['league']}] {m['home']} vs {m['away']} → {m['prediction']} "
              f"(tip {m['tip']}, index {m['index']}, probs {m['probs']})")
        predictions.append({
            "home_team": m["home"],
            "away_team": m["away"],
            "prediction": m["prediction"],
            "league": m["league"],
            "kickoff": m["kickoff"],
        })
    return predictions


if __name__ == "__main__":
    asyncio.run(scrape())
