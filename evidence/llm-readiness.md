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

That is about **$0.04 a round** by the mock-based estimate. **The first real run used far more:**
about 513k tokens a round (3.1M over 6 rounds, hidden reasoning included), so expect roughly
$0.10-0.60 a round at those assumed prices. A round also took about 11 minutes of wall time, so
precompute demo runs rather than running them live. Even with 5x longer answers, or a reasoning model's hidden tokens on
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

## First real run (2026-10-24, Bitdeer live)

The first replay of the 24 saved prompts failed on 6. The two critic miss-assignment calls and four
of the five specialists' rule-writing calls each ran about 140 seconds and returned nothing: the
models spent their whole token budget on hidden reasoning. A probe showed two different causes:
- **GLM-5.3 and GLM-5.3-Flash** accept `reasoning_effort: "low"`. With it, their hidden reasoning
  drops about 10x, and every long prompt answers in 2-22 seconds.
- **DeepSeek-V4.1-Flash and DeepSeek-V4-Flash** ignore `reasoning_effort`. On the rule-writing and
  miss-assignment prompts, they reason past 6,000-16,000 tokens without answering, even when told
  to "think briefly".

Fixes:
- `integrations/bitdeer.py` sends `reasoning_effort: "low"` by default. Override it with
  `BITDEER_REASONING_EFFORT`, or set it empty to send nothing.
- The long analytical roles move to GLM: `graph` to GLM-5.3-Flash, `velocity` to GLM-5.3, and
  `critic_second` to GLM-5.3-Flash. DeepSeek keeps the short tasks, where it answers fine: the
  investigator, outsider rewrites, and attackers spawned for GLM-run specialists.
- Spawned attackers still use the other model family from their parent.

After the fixes, all 24 prompts parse:

| fixture | parsed | input / output tokens | seconds | problem |
|---|---|---|---|---|
| critic__MissAssignments | yes | 12362 / 3211 | 21.8 |  |
| critic__RuleVerdict | yes | 511 / 82 | 3.7 |  |
| critic_second__MissAssignments | yes | 12010 / 820 | 3.4 |  |
| graph__ActionChoice | yes | 393 / 88 | 1.0 |  |
| graph__ReflectionProposal | yes | 7444 / 400 | 2.1 |  |
| held_out__HeldOutDraft | yes | 322 / 3427 | 35.5 |  |
| identity__ActionChoice | yes | 539 / 89 | 0.9 |  |
| identity__ReflectionProposal | yes | 2014 / 240 | 2.5 |  |
| investigator__InvestigatorStep | yes | 1583 / 1541 | 10.8 |  |
| mandate__ActionChoice | yes | 402 / 66 | 0.7 |  |
| mandate__ReflectionProposal | yes | 9733 / 585 | 3.2 |  |
| outsider_a__PlaybookRewrite | yes | 1096 / 952 | 7.0 |  |
| outsider_b__PlaybookRewrite | yes | 1149 / 65 | 0.8 |  |
| spawn_graph__SpawnDraft | yes | 804 / 2947 | 17.0 |  |
| spawn_identity__SpawnDraft | yes | 904 / 1786 | 10.3 |  |
| spawn_mandate__SpawnDraft | yes | 897 / 3511 | 19.8 |  |
| spawn_orchestrator__SpawnDraft | yes | 739 / 749 | 5.7 |  |
| spawn_timing__SpawnDraft | yes | 830 / 4259 | 23.9 |  |
| spawn_velocity__SpawnDraft | yes | 1430 / 6070 | 62.4 |  |
| threat_extractor__Extraction | yes | 612 / 139 | 1.3 |  |
| timing__ActionChoice | yes | 527 / 74 | 0.8 |  |
| timing__ReflectionProposal | yes | 4758 / 725 | 3.6 |  |
| velocity__ActionChoice | yes | 524 / 65 | 0.9 |  |
| velocity__ReflectionProposal | yes | 12534 / 1288 | 10.5 |  |

0 of 24 prompts failed to parse.

Real input tokens ran about 1.5x the first estimate (number-heavy JSON), so the tool now uses 2.3
characters a token. Output tokens include hidden reasoning, so they're much higher than the mock's:
the spawn prompts use 750-6,000 tokens a call. Re-run the replay any time:

    uv run python -m engine.llm_readiness --replay engine/tests/fixtures/prompts
