"""
generate_claude_predictions.py — Generate top 5 football predictions using Claude AI.

Uses claude-sonnet-4-6 with the hosted web_search tool to:
1. Research today's football schedule across major European leagues and competitions
2. Analyze form, H2H, injuries, home/away record, and betting odds
3. Select the 5 games it is most confident about predicting correctly
4. Return a 1X2 prediction for each (1=Home, X=Draw, 2=Away)

Requires ANTHROPIC_API_KEY in .env or shell environment.

Output: .tmp/predictions_claude_{date}.json
"""

import json
import os
import re
import sys
import traceback
from datetime import date

from dotenv import load_dotenv

load_dotenv()

SITE = "claude"
MODEL = "claude-sonnet-4-6"
MAX_ITERATIONS = 10

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(BASE_DIR, ".tmp")

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}

PROMPT_TEMPLATE = """\
Today is {date}.

Your task is to generate the 5 most confident 1X2 football predictions for today's matches.

Steps:
1. Search for today's football fixtures across the major European leagues and competitions: \
Premier League, Championship, La Liga, Bundesliga, Serie A, Ligue 1, Champions League, \
Europa League, Conference League, Eredivisie, Primeira Liga.
2. For each promising fixture, research: recent form (last 5 games), head-to-head record, \
home/away performance, injury/suspension news, betting odds, and any other relevant context.
3. Select the 5 matches you are MOST confident about — favour matches with clear favourites \
and minimal uncertainty. Avoid matches where you are unsure.
4. For each selected match, predict: 1 (Home win), X (Draw), or 2 (Away win).

Return your final answer as ONLY a JSON code block (no other text after it) in this exact format:

```json
[
  {{"home_team": "Team A", "away_team": "Team B", "prediction": "1"}},
  {{"home_team": "Team C", "away_team": "Team D", "prediction": "X"}},
  {{"home_team": "Team E", "away_team": "Team F", "prediction": "2"}},
  {{"home_team": "Team G", "away_team": "Team H", "prediction": "1"}},
  {{"home_team": "Team I", "away_team": "Team J", "prediction": "2"}}
]
```

Rules:
- Use the teams' common English names (e.g. "Real Madrid", "PSG", "Man City")
- prediction must be exactly "1", "X", or "2"
- Return exactly 5 predictions
- Do NOT include any text after the closing ``` of the JSON block
"""


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


def extract_json(text):
    """Extract and parse the JSON predictions block from Claude's response."""
    match = re.search(r"```json\s*([\s\S]+?)\s*```", text)
    if not match:
        raise ValueError("No ```json ... ``` block found in response")
    return json.loads(match.group(1))


def validate_predictions(preds):
    """Validate the parsed predictions list."""
    if not isinstance(preds, list):
        raise ValueError(f"Expected a list, got {type(preds).__name__}")
    if len(preds) != 5:
        raise ValueError(f"Expected 5 predictions, got {len(preds)}")
    for i, p in enumerate(preds):
        for key in ("home_team", "away_team", "prediction"):
            if key not in p:
                raise ValueError(f"Prediction {i} missing key '{key}'")
        if p["prediction"] not in ("1", "X", "2"):
            raise ValueError(
                f"Prediction {i} has invalid value '{p['prediction']}' (must be 1/X/2)"
            )
    return preds


def run_agentic_loop(client, run_date):
    """
    Run the multi-turn agentic loop with web_search.
    Returns the final text response from Claude.
    """
    prompt = PROMPT_TEMPLATE.format(date=run_date)
    messages = [{"role": "user", "content": prompt}]

    for iteration in range(MAX_ITERATIONS):
        print(f"  [claude] API call #{iteration + 1}...")
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=4096,
            tools=[WEB_SEARCH_TOOL],
            messages=messages,
            betas=["web-search-2025-03-05"],
        )

        block_types = [getattr(b, "type", type(b).__name__) for b in response.content]
        print(f"  [claude] stop_reason={response.stop_reason}, blocks={block_types}")

        if response.stop_reason == "end_turn":
            # Extract all text from the final response
            final_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_text += block.text
            print(f"  [claude] Done after {iteration + 1} API call(s)")
            return final_text

        # Tool use — append assistant turn and continue
        messages.append({"role": "assistant", "content": response.content})

        # For the hosted web_search tool, Anthropic includes tool_result blocks
        # directly in the response content. We pass them back as the next user turn.
        tool_results = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "tool_result":
                tool_results.append(block)

        if not tool_results:
            # Shouldn't happen with hosted tool, but avoid infinite loop
            print(f"  [claude] WARNING: stop_reason={response.stop_reason} but no tool_result blocks found in {block_types}")
            break

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Agentic loop did not complete within {MAX_ITERATIONS} iterations")


def main():
    run_date = str(date.today())
    print(f"[{SITE}] Generating predictions for {run_date} using {MODEL}...")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        error_msg = "ANTHROPIC_API_KEY not found in environment or .env"
        print(f"[{SITE}] PRED_FAILED — {error_msg}")
        path = write_output(run_date, [], error=error_msg)
        print(f"[{SITE}] Failed output written to {path}")
        sys.exit(0)

    try:
        import anthropic
    except ImportError:
        error_msg = "anthropic package not installed — run: pip install anthropic"
        print(f"[{SITE}] PRED_FAILED — {error_msg}")
        path = write_output(run_date, [], error=error_msg)
        print(f"[{SITE}] Failed output written to {path}")
        sys.exit(0)

    client = anthropic.Anthropic(api_key=api_key)

    try:
        final_text = run_agentic_loop(client, run_date)
    except Exception as e:
        traceback.print_exc()
        error_msg = f"API error: {type(e).__name__}: {e}"
        print(f"[{SITE}] PRED_FAILED — {error_msg}")
        path = write_output(run_date, [], error=error_msg)
        print(f"[{SITE}] Failed output written to {path}")
        sys.exit(0)

    try:
        raw = extract_json(final_text)
        predictions = validate_predictions(raw)
    except (ValueError, json.JSONDecodeError) as e:
        error_msg = f"Parse/validation error: {e}"
        print(f"[{SITE}] PRED_FAILED — {error_msg}")
        print(f"  [claude] Raw response snippet: {final_text[:500]}")
        path = write_output(run_date, [], error=error_msg)
        print(f"[{SITE}] Failed output written to {path}")
        sys.exit(0)

    for p in predictions:
        label = {"1": "Home win", "X": "Draw", "2": "Away win"}[p["prediction"]]
        print(f"  [{SITE}] {p['home_team']} vs {p['away_team']} → {p['prediction']} ({label})")

    print(f"[{SITE}] Extracted {len(predictions)} predictions")
    path = write_output(run_date, predictions)
    print(f"[{SITE}] Output: {path}")


if __name__ == "__main__":
    main()
