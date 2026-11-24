"""
scrape_statarea.py — Scrape top 5 football predictions from statarea.com

Statarea publishes one page per day (/predictions/date/YYYY-MM-DD/) grouped by
competition, each match with Statarea's tip and 1/X/2 probabilities. "Top 5" =
today's not-yet-started, tipped matches with the highest probability on
Statarea's most likely outcome, limited to matches the results sources can
score.

Output: .tmp/predictions_statarea_{date}.json
"""

import asyncio
import json
import os
from datetime import date

from playwright.async_api import async_playwright

from pick_utils import is_skipped, top_scorable

SITE = "statarea"
URL_TEMPLATE = "https://www.statarea.com/predictions/date/{d}/"

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
    os.makedirs(TMP_DIR, exist_ok=True)  # debug files are written before write_output()
    url = URL_TEMPLATE.format(d=run_date)
    print(f"[{SITE}] Scraping {url} for {run_date} ...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                await page.wait_for_selector("div.competition div.match", timeout=15000)
            except Exception:
                await page.wait_for_timeout(3000)

            predictions = await extract_predictions(page, run_date)

            if not predictions:
                title = await page.title()
                print(f"[{SITE}] DIAG http={response.status if response else '?'} title={title!r} url={page.url}")
                html_path = os.path.join(TMP_DIR, f"debug_{SITE}_{run_date}.html")
                with open(html_path, "w") as f:
                    f.write(await page.content())
                raise ValueError(f"No predictions extracted. Debug HTML: {html_path}")

            print(f"[{SITE}] Extracted {len(predictions)} predictions")
            path = write_output(run_date, predictions[:5])
            print(f"[{SITE}] Output: {path}")

        except Exception as e:
            error_msg = str(e)
            print(f"[{SITE}] ERROR: {error_msg}")
            path = write_output(run_date, [], error=error_msg)
            print(f"[{SITE}] Failed output written to {path}")

        finally:
            await browser.close()


async def extract_predictions(page, run_date):
    """
    div.competition (.header .name = "COUNTRY - LEAGUE")
      div.body > div.match, one per match:
        .tip .value          — Statarea's tip (1, X, 2, 1X, X2); class gains
                               success/failed once the match is settled
        .teams .ownheader    — "YYYY-MM-DD HH:MM" (site time, CET)
        .hostteam / .guestteam .name — teams; a.goals holds the score once started
        .inforow .coefrow > .coefbox .value — first three = 1 / X / 2 probabilities (%)
    """
    raw = await page.evaluate("""() => [...document.querySelectorAll('div.competition')].flatMap(c => {
        const league = ((c.querySelector(':scope > .header .name') || {}).textContent || '').trim();
        return [...c.querySelectorAll('div.body > div.match')].map(m => {
            const t = s => ((m.querySelector(s) || {}).textContent || '').trim();
            const tipEl = m.querySelector('.tip .value');
            return {
                league,
                when: t('.teams .ownheader'),
                tip: t('.tip .value'),
                settled: !!tipEl && /success|failed/.test(tipEl.className),
                home: t('.hostteam .name'),
                away: t('.guestteam .name'),
                goals: t('.hostteam a.goals') + t('.guestteam a.goals'),
                probs: [...m.querySelectorAll('.inforow .coefrow > .coefbox .value')]
                    .slice(0, 3).map(v => parseInt(v.textContent, 10)),
            };
        });
    })""")

    print(f"[{SITE}] {len(raw)} matches listed")
    candidates = []
    for m in raw:
        if not m["home"] or not m["away"] or not m["tip"]:
            continue
        if not m["when"].startswith(run_date):
            continue
        if m["settled"] or m["goals"]:
            continue  # already started or finished
        if is_skipped(m["league"], m["home"], m["away"]):
            continue
        probs = m["probs"]
        if len(probs) != 3 or any(p is None or p != p for p in probs):
            continue
        best = max(range(3), key=lambda i: probs[i])
        candidates.append({
            "home_team": m["home"],
            "away_team": m["away"],
            "prediction": "1X2"[best],
            "confidence": probs[best],
            "league": m["league"],
            "kickoff": m["when"],
            "site_tip": m["tip"],
        })

    candidates.sort(key=lambda c: -c["confidence"])  # stable: page order breaks ties
    print(f"[{SITE}] {len(candidates)} tipped, not-yet-started matches today")
    predictions = top_scorable(candidates, run_date, SITE)
    for p in predictions:
        print(f"  [{SITE}] {p['confidence']}% {p['home_team']} vs {p['away_team']} → {p['prediction']} "
              f"(tip {p['site_tip']}, {p['league']})")
    return predictions


if __name__ == "__main__":
    asyncio.run(scrape())
