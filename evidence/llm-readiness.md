# Real-model readiness (2026-10-24, Stanley)

What the engine will send to Bitdeer, measured on 6 mock rounds of the full system (seed 42) with
`uv run python -m engine.llm_readiness --rounds 6`. Token counts are estimates (3.5 characters a
token). Output tokens come from the mock's answers, which are shorter than a real model's, so the
output side is a floor.

636 LLM calls over 6 mock rounds

| role | model | calls/round | input tokens mean / max | output tokens mean | tokens/round | $/round |
|---|---|---|---|---|---|---|
| critic | zai-org/GLM-5.3 | 5.0 | 5,745 / 8,054 | 437 | 30,912 | $0.000 |
| critic_second | deepseek-ai/DeepSeek-V4.1-Flash | 1.3 | 7,675 / 7,841 | 589 | 11,019 | $0.000 |
| graph | deepseek-ai/DeepSeek-V4.1-Flash | 2.0 | 2,300 / 5,229 | 30 | 4,659 | $0.000 |
| held_out | Qwen/Qwen3.8-27B | 0.2 | 274 / 274 | 540 | 136 | $0.000 |
| identity | zai-org/GLM-5.3 | 1.8 | 735 / 1,608 | 30 | 1,403 | $0.000 |
| investigator | deepseek-ai/DeepSeek-V4.1-Flash | 71.2 | 939 / 1,254 | 28 | 68,819 | $0.000 |
| mandate | zai-org/GLM-5.3-Flash | 2.0 | 2,818 / 6,619 | 36 | 5,708 | $0.000 |
| outsider_a | deepseek-ai/DeepSeek-V4-Flash | 9.8 | 797 / 918 | 97 | 8,798 | $0.000 |
| outsider_b | zai-org/GLM-5.3-Flash | 1.7 | 826 / 991 | 157 | 1,638 | $0.000 |
| spawn_graph | zai-org/GLM-5.3 | 0.5 | 659 / 710 | 165 | 412 | $0.000 |
| spawn_identity | deepseek-ai/DeepSeek-V4.1-Flash | 0.8 | 645 / 781 | 188 | 694 | $0.000 |
| spawn_mandate | deepseek-ai/DeepSeek-V4.1-Flash | 0.8 | 727 / 756 | 201 | 774 | $0.000 |
| spawn_orchestrator | deepseek-ai/DeepSeek-V4-Flash | 2.8 | 536 / 653 | 175 | 2,015 | $0.000 |
| spawn_timing | deepseek-ai/DeepSeek-V4.1-Flash | 0.5 | 648 / 731 | 162 | 405 | $0.000 |
| spawn_velocity | zai-org/GLM-5.3 | 0.5 | 982 / 1,180 | 157 | 570 | $0.000 |
| threat_extractor | zai-org/GLM-5.3 | 1.0 | 597 / 609 | 121 | 718 | $0.000 |
| timing | zai-org/GLM-5.3-Flash | 2.0 | 1,392 / 3,306 | 28 | 2,841 | $0.000 |
| velocity | deepseek-ai/DeepSeek-V4-Flash | 2.0 | 4,229 / 8,751 | 76 | 8,609 | $0.000 |

Total: 150,128 tokens and $0.00 per round (budget $0.60; the run pauses above $1.20).
No price recorded for Qwen/Qwen3.8-27B, deepseek-ai/DeepSeek-V4-Flash, deepseek-ai/DeepSeek-V4.1-Flash, zai-org/GLM-5.3, zai-org/GLM-5.3-Flash: counted as $0. Set BITDEER_PRICE_IN_/OUT_<MODEL>.

## Cost estimate

No Bitdeer prices are recorded in `integrations/model_prices.py` yet, so the tool reports $0.
**Illustrative estimate (assumed prices, not Bitdeer's):** about 150k tokens a round, almost all
input; about 34k of it goes to GLM-5.3, mostly the critic. Assume:
- GLM-5.3 at $0.60 in / $2.20 out per million tokens (the example in `model_prices.py`);
- the Flash models at $0.15 / $0.50 (Z.ai's and DeepSeek's list prices).

That is about **$0.04 a round**. Even with 5x longer answers, or a reasoning model's hidden tokens on
the investigator's 70 calls a round, it stays well under the $0.60 budget. The run pauses only above
$1.20 a round. Set the real prices with `BITDEER_PRICE_IN_<MODEL>` / `BITDEER_PRICE_OUT_<MODEL>`, then
re-run the tool.

## Checks

`engine/tests/test_llm_readiness.py` covers:
- every role has an assigned model;
- every prompt fits in 24k tokens (the largest is about 8.8k, velocity reflection);
- every prompt names each field of its answer schema and quotes its data safely;
- the 24 saved prompts in `engine/tests/fixtures/prompts/` (the largest per role and schema) replay cleanly.

The field check found 4 prompt templates that never named some of their answer fields. All are fixed:
- the 6 spawn roles were missing `params`, `param_bounds`, `free_text_payloads` and `reasoning`;
- the critic's miss assignment was missing `assignments`;
- outsider rewrites were missing `reasoning`;
- reflection was missing `notes`.

## First real run

Once credits are live, send the saved prompts to the real models:

    uv run python -m engine.llm_readiness --replay engine/tests/fixtures/prompts

It reports, per prompt, whether the answer parsed, the real token counts and the latency, so format
problems show up before a full round runs.
