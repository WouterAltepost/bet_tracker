"""
scrape_zulubet.py — Scrape top 5 football predictions from zulubet.com

Zulubet publishes one page per day (tips-DD-MM-YYYY.html) with ~50 matches,
each with Zulubet's 1/X/2 probabilities and a tip. "Top 5" = today's
not-yet-started, tipped matches with the highest probability on Zulubet's most
likely outcome, limited to matches the results sources can score.

Output: .tmp/predictions_zulubet_{date}.json
"""

import asyncio
import json
import os
import re
from datetime import date, datetime, timezone

from playwright.async_api import async_playwright

from pick_utils import is_skipped, top_scorable

SITE = "zulubet"
URL_TEMPLATE = "https://www.zulubet.com/tips-{d:%d-%m-%Y}.html"

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
    url = URL_TEMPLATE.format(d=date.fromisoformat(run_date))
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
                await page.wait_for_selector("table.content_table td.prediction_full", timeout=15000)
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
    table.content_table > tbody > tr, one per match:
      td[0]  <script>mf_usertime('MM/DD/YYYY, HH:MM')</script> — kick-off in UTC
      td[1]  img.flags[title="<country> <league>"] + "Home - Away"
      td.prediction_full x3 — Zulubet's 1 / X / 2 probabilities (%)
      td[6]  Zulubet's tip (1, X, 2, 1X, X2, 12 — empty when it has no tip)
    """
    raw = await page.evaluate("""() => {
        const out = [];
        const table = document.querySelector('table.content_table');
        if (!table) return out;
        table.querySelectorAll(':scope > tbody > tr').forEach(tr => {
            const cells = [...tr.querySelectorAll(':scope > td')];
            const probs = [...tr.querySelectorAll(':scope > td.prediction_full')]
                .map(td => parseInt(td.textContent, 10));
            if (cells.length < 8 || probs.length !== 3) return;
            const flag = cells[1].querySelector('img.flags');
            const time = (cells[0].innerHTML.match(/mf_usertime\\('([^']+)'\\)/) || [])[1] || '';
            out.push({
                kickoff_utc: time,
                league: flag ? (flag.getAttribute('title') || '') : '',
                teams: cells[1].textContent.replace(/\\s+/g, ' ').trim(),
                probs,
                tip: cells[6].textContent.trim(),
            });
        });
        return out;
    }""")

    print(f"[{SITE}] {len(raw)} matches listed")
    now = datetime.now(timezone.utc)
    candidates = []
    for m in raw:
        if " - " not in m["teams"] or not m["tip"]:
            continue
        home, away = (t.strip() for t in m["teams"].split(" - ", 1))
        try:
            kickoff = datetime.strptime(m["kickoff_utc"], "%m/%d/%Y, %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if str(kickoff.date()) != run_date or kickoff <= now:
            continue  # other day, or already started
        if is_skipped(m["league"], home, away):
            continue
        if any(p != p for p in m["probs"]):  # NaN
            continue
        best = max(range(3), key=lambda i: m["probs"][i])
        candidates.append({
            "home_team": home,
            "away_team": away,
            "prediction": "1X2"[best],
            "confidence": m["probs"][best],
            "league": m["league"],
            "kickoff": kickoff.strftime("%Y-%m-%dT%H:%MZ"),
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
