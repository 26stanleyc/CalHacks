# Bitdeer model prices (M0)

Probed 2026-10-24 08:45 UTC by `scripts/probe_integrations.py`.

## Prices

**Bitdeer's OpenAI-compatible API does not expose pricing.** `GET /v1/models` returns
`id`, `object`, `created` and `owned_by` only, with no price field, so the numbers below
have to be read off the Bitdeer console at https://www.bitdeer.ai/ and filled in by hand.

Once known, put them in `integrations/model_prices.py` (or set
`BITDEER_PRICE_IN_<MODEL>` / `BITDEER_PRICE_OUT_<MODEL>`) and the cost ledger in
`integrations.bitdeer.UsageLedger` starts reporting real spend. Until then token counts
are recorded and cost is reported as $0 with the model listed under `unpriced_models`.

| Role | Model | $/1M input | $/1M output |
|---|---|---|---|
| critic | `zai-org/GLM-5.3` | _console_ | _console_ |
| critic_second | `deepseek-ai/DeepSeek-V4.1-Flash` | _console_ | _console_ |
| investigator | `deepseek-ai/DeepSeek-V4.1-Flash` | _console_ | _console_ |
| timing | `zai-org/GLM-5.3-Flash` | _console_ | _console_ |
| graph | `deepseek-ai/DeepSeek-V4.1-Flash` | _console_ | _console_ |
| identity | `zai-org/GLM-5.3` | _console_ | _console_ |
| velocity | `deepseek-ai/DeepSeek-V4-Flash` | _console_ | _console_ |
| mandate | `zai-org/GLM-5.3-Flash` | _console_ | _console_ |
| outsider_a | `deepseek-ai/DeepSeek-V4-Flash` | _console_ | _console_ |
| outsider_b | `zai-org/GLM-5.3-Flash` | _console_ | _console_ |
| page_shopper | `deepseek-ai/DeepSeek-V4-Flash` | _console_ | _console_ |
| threat_extractor | `zai-org/GLM-5.3` | _console_ | _console_ |
| chat | `zai-org/GLM-5.3-Flash` | _console_ | _console_ |
| held_out | `Qwen/Qwen3.8-27B` | _console_ | _console_ |

## Models the API listed

- `deepseek-ai/DeepSeek-V4.1-Flash`
- `zai-org/GLM-5.3`
- `Qwen/Qwen3.8-27B`
- `zai-org/GLM-5.3-Flash`
- `deepseek-ai/DeepSeek-V4-Flash`
- `BAAI/bge-m3`
- `BAAI/bge-reranker-v2-m3`
- `seedream-5.0-lite`

## One live call per chat model (spec 8.2, M0)

| Model | Result |
|---|---|
| `Qwen/Qwen3.8-27B` | ok |
| `deepseek-ai/DeepSeek-V4-Flash` | ok |
| `deepseek-ai/DeepSeek-V4.1-Flash` | ok |
| `zai-org/GLM-5.3` | ok |
| `zai-org/GLM-5.3-Flash` | ok |

## Budget check (spec 4.1)

The $0.60-per-round estimate cannot be confirmed until the console prices are in.
Recompute it then; if a round comes out over $0.60, shrink traffic or the escalation
cap first, as 4.1 says.
