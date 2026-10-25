# What CRS is worth

Identical traffic and seeds, 4 rounds each, mock bureau (so this is repeatable offline; the live sandbox answers the same products).

| CRS | fraud settled | fraud stopped | false positives | over-cap vetoes | CRS rules fired |
|---|---|---|---|---|---|
| off (where we started) | $20,813 | 47.6% | 1.11% | 38 | 0 |
| escalation only (uncertain band) | $22,926 | 46.6% | 0.97% | 39 | 0 |
| enrolled (every person, every payment) | $9,472 | 71.5% | 1.43% | 215 | 428 |

Mean across 3 seeds. 'Over-cap vetoes' are payments refused because they exceeded the mandate's cap: with enrollment on, that cap is the one CRS underwrote, so an identity the bureau cannot stand behind is held to a floor before any scoring happens.

Reproduce: `uv run python -m engine.crs_ablation --seeds 42 43 44 --rounds 4`
