# Published run: October 3, 2026

One person, one hidden craving, one successful prediction per model.

Target: **Dave's #2: 2 Sliders w/ Fries**.

| Model | Guess | Closeness /100 | CLI wall time | Reply tokens |
| --- | --- | ---: | ---: | ---: |
| Claude Opus 5.5 | Bowls of Rice | 15 | 3.496s | 83 |
| Claude Sonnet 5.5 | Bowls of Rice | 15 | 2.483s | 121 |
| GPT Astra | Bowls of Rice | 15 | 5.743s | 47 |
| GPT Sol | Bowls of Rice | 15 | 5.068s | 47 |
| Meta Muse Spark | Bowls of Rice | 15 | 7.021s | 65 |

Both newest-order and frequency baselines also scored **15/100**. Nobody predicted the target restaurant.

All five model predictions used installed CLIs. No DoorDash cart or checkout action was taken.

Muse first failed because our isolation adapter had copied a redacted auth placeholder from OpenCode debug config. After fixing the adapter, it received **the exact same prompt once more**, producing its only valid prediction. The failed attempt is in [attempts/muse-auth-failure.json](attempts/muse-auth-failure.json). The judge ran once before the repair and once with all five answers; both classifications agree.

The rubric and target/prompt hashes were saved before contestant runs. They are local run commitments, not a public preregistration. Future harness prompts explicitly prioritize rice-bowl classification over other chicken; the published original judge already put every bowl in that category.

CLI-reported model metadata is preserved: Claude lists auxiliary Haiku usage as well as the requested model; Codex and OpenCode did not return a model identifier in the parsed completion stream. Names on the chart identify the **requested** models. No harness fallback was used.

Reply token counts omit reasoning and input/cache tokens. Full provider-specific usage is in [run.json](run.json). These CLI system prompts add overhead; the benchmark is small, not token-free.

The chart uses categorical partial credit from a blinded Opus evaluation, followed by deterministic Python scoring. It is not a multi-question accuracy rate.

Caption:

> I built DoorDash Bench to find out which frontier model knows what I want for dinner. All five tied with counting orders. They told me to eat rice. I wanted Dave’s. AGI is cancelled.
