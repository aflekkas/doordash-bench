# DoorDash Bench v2: published measurements

Four tasks for each of five models: three next-order holdouts plus the user-confirmed current craving.

| Model | Probability score /100 | Est. USD / prediction | Tokens / prediction | Exact picks |
| --- | ---: | ---: | ---: | ---: |
| Claude Sonnet 5.5 | 54.1 | $0.007770 | 1470.75 | 0/4 |
| Claude Opus 5.5 | 53.6 | $0.013985 | 1468.25 | 0/4 |
| GPT Astra | 51.5 | $0.053337 | 6343.25 | 0/4 |
| GPT Sol | 51.0 | $0.009257 | 6343.75 | 0/4 |
| Meta Muse Spark | 49.3 | $0.000128 | 970.50 | 0/4 |

Uniform probabilities: 58.3/100. Smoothed order-frequency baseline: 50.2/100. Repeat-latest baseline: 0/100.

Every model missed every exact top-1 pick. Their distributions differ, so normalized Brier scoring separates degrees of uncertainty. Scores are not accuracy percentages.

The static six-choice menu includes future historical meal choices by design: this is known-menu forecasting, not restaurant discovery. IDs are alphabetical and unrelated to chronology. Historical prompts contain strictly older orders, one fresh session each.

## Cost basis

All dollar values are API-equivalent estimates, not observed subscription charges. Claude/OpenCode CLI totals are preserved. Codex costs use measured uncached, cached and output token counts with the dated OpenAI standard short-context rates in the manifest. Reasoning/cache token subsets are not charged twice. Prediction costs exclude the shared setup and evaluator. Observed cache warmth was not standardized.

Accepted contestant prediction estimates total **$0.337907** over 20 calls. Shared setup/evaluator estimates total **$0.021352**. These totals exclude development attempts.

Accepted contestant usage: **66,386 tokens** including CLI context/cache/reasoning/output. Shared setup/evaluation: **2,332 tokens**.

## Provenance

The answer/menu/spec/prompt hashes were saved before the accepted model runs. Cost reference pricing was recorded afterward as offline analysis; no predictions were changed. Evaluator exact-hit counts agree with Python.

A development attempt was excluded because sequential menu IDs revealed chronological ordering. Review caught that before the leaderboard was accepted; the fixed alphabetical-ID protocol ran once for all five models. Development records remain private in gitignored scratch. The manifest records the excluded attempt.

CLI tool attempts, invalid JSON, missing probabilities, or failed tasks yield incomplete scores. There were no such failures in the accepted 20 predictions. No automatic retries, sample selection, or answer-aware rewriting.

[Full raw report](run.json) · [Manifest](manifest.json) · [PNG](chart.png) · [Vector SVG](chart.svg)
