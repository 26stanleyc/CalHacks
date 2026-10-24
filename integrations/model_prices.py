"""Bitdeer price per million tokens, for the cost ledger and the budget in spec 4.1.

Bitdeer's OpenAI-compatible `/models` route returns ids only, with no pricing field
(checked at M0), so these come from the Bitdeer console and are kept here by hand.
Anything still `None` is counted as $0 and reported as unknown, so a missing price
never blocks a run; `evidence/m0/model-prices.md` records what the probe saw.

Override one at runtime without editing this file:

    BITDEER_PRICE_IN_ZAI_ORG_GLM_5_3=0.6    # $ per 1M input tokens
    BITDEER_PRICE_OUT_ZAI_ORG_GLM_5_3=2.2   # $ per 1M output tokens
"""

import os

# model id -> (input $/Mtok, output $/Mtok); None means "not recorded yet".
PRICE_PER_MTOK: dict[str, tuple[float | None, float | None]] = {
    "zai-org/GLM-5.3": (None, None),
    "zai-org/GLM-5.3-Flash": (None, None),
    "deepseek-ai/DeepSeek-V4.1-Flash": (None, None),
    "deepseek-ai/DeepSeek-V4-Flash": (None, None),
    "Qwen/Qwen3.8-27B": (None, None),
}


def _env_key(model: str) -> str:
    return model.replace("/", "_").replace("-", "_").replace(".", "_").upper()


def price_for(model: str) -> tuple[float | None, float | None]:
    """(input, output) price per million tokens, env override first."""
    key = _env_key(model)
    table_in, table_out = PRICE_PER_MTOK.get(model, (None, None))

    def read(prefix: str, fallback: float | None) -> float | None:
        raw = os.environ.get(f"BITDEER_PRICE_{prefix}_{key}")
        if raw is None or not raw.strip():
            return fallback
        try:
            return float(raw)
        except ValueError:
            return fallback

    return read("IN", table_in), read("OUT", table_out)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Spend for one call. Unknown prices count as $0, so the ledger never guesses."""
    price_in, price_out = price_for(model)
    total = 0.0
    if price_in is not None:
        total += input_tokens / 1_000_000 * price_in
    if price_out is not None:
        total += output_tokens / 1_000_000 * price_out
    return total


def unpriced_models() -> list[str]:
    """Models with no price recorded, so a report can say the spend figure is partial."""
    return [m for m in PRICE_PER_MTOK if price_for(m) == (None, None)]
