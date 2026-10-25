"""`contracts.llm.LLMClient` over the Claude API: a fast stand-in for the open Bitdeer models while
iterating (LLM_PROVIDER=claude in .env or the environment; ANTHROPIC_API_KEY from .env).

Each role keeps its size class: the full GLM model maps to Claude Sonnet 5.5, the Flash-sized models
(and the reserved Qwen) to Claude Haiku 5.5. Answers that must match a schema are asked for as JSON and
validated, with one repair try quoting the validation error, like the Bitdeer client.
"""

import json
import logging
import os
import threading
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError

from contracts.config import model_for
from contracts.llm import LLMResponse, LLMToolCall, Message, ToolSpec

logger = logging.getLogger(__name__)

CLAUDE_FOR = {
    "zai-org/GLM-5.3": "claude-sonnet-5-5",
    "zai-org/GLM-5.3-Flash": "claude-haiku-5-5",
    "deepseek-ai/DeepSeek-V4-Flash": "claude-haiku-5-5",
    "deepseek-ai/DeepSeek-V4.1-Flash": "claude-haiku-5-5",
    "Qwen/Qwen3.8-27B": "claude-haiku-5-5",
}
PRICES = {"claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50)}  # $ per 1M tokens, in / out
THINKING_HEADROOM = 4_000  # adaptive thinking shares max_tokens with the answer


def _roles(name: str) -> set[str]:
    return {r.strip() for r in os.environ.get(name, "").split(",") if r.strip()}


# Process-wide spend, across every ClaudeLLM in the run (TagTeam's and XGBoost's engines). With
# CLAUDE_BUDGET_USD set, calls past the budget go to the code fallback instead of the API.
_SPENT = {"usd": 0.0, "logged": 0}
_SPENT_LOCK = threading.Lock()


class RoutedToCode(RuntimeError):
    """This role skips the model; the caller's code fallback decides (engine.brains.Brain catches it)."""


class ClaudeLLM:
    def __init__(self, client: anthropic.Anthropic | None = None, effort: str = "low") -> None:
        self.client = client or anthropic.Anthropic(max_retries=3)
        self.effort = effort
        self._lock = threading.Lock()
        self._usage: dict[str, Any] = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "by_model": {}}

    def complete(
        self,
        messages: list[Message],
        *,
        role: str,
        response_schema: type[BaseModel] | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        # Iteration speed-ups (env): CLAUDE_CODE_ROLES skip the model (e.g. the attackers' plan rewrites, which
        # Claude mostly refuses anyway); CLAUDE_ALL_HAIKU=1 runs every role on Haiku.
        if role in _roles("CLAUDE_CODE_ROLES"):
            raise RoutedToCode(role)
        budget = float(os.environ.get("CLAUDE_BUDGET_USD") or 0)
        if budget and _SPENT["usd"] >= budget:
            raise RoutedToCode(f"{role}: budget ${budget:.2f} reached")
        model = CLAUDE_FOR.get(model_for(role), "claude-haiku-5-5")
        if os.environ.get("CLAUDE_ALL_HAIKU") == "1":
            model = "claude-haiku-5-5"
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        if response_schema is not None and not tools:
            system += ("\n\nRespond with only a JSON object (no prose, no code fences) that validates against this "
                       f"JSON schema:\n{json.dumps(response_schema.model_json_schema())}")
        convo = [{"role": "assistant" if m.role == "assistant" else "user",
                  "content": (f"Tool result:\n{m.content}" if m.role == "tool" else m.content) or "(empty)"}
                 for m in messages if m.role != "system"]
        if not convo or convo[0]["role"] != "user":
            convo.insert(0, {"role": "user", "content": "Begin."})
        params: dict[str, Any] = {"model": model, "max_tokens": max_tokens + THINKING_HEADROOM, "messages": convo,
                                  "output_config": {"effort": self.effort},
                                  "cache_control": {"type": "ephemeral"}}  # role prompts repeat every round
        if system.strip():
            params["system"] = system.strip()
        if tools:
            params["tools"] = [{"name": t.name, "description": t.description,
                                "input_schema": t.parameters or {"type": "object", "properties": {}}} for t in tools]

        response = self.client.messages.create(**params)
        text, calls = self._read(response)
        parsed = None
        if response_schema is not None and not tools:
            parsed, error = _validate(response_schema, text)
            if parsed is None and response.stop_reason != "refusal":  # one repair try, quoting the error
                params["messages"] = convo + [{"role": "assistant", "content": text or "(empty)"},
                                              {"role": "user", "content": f"That didn't validate: {error}. "
                                                                          "Reply with only the corrected JSON object."}]
                response = self.client.messages.create(**params)
                text, calls = self._read(response)
                parsed, error = _validate(response_schema, text)
                if parsed is None:
                    logger.warning("Claude did not return valid %s for role %s: %s", response_schema.__name__, role, error)
        return LLMResponse(text=text, parsed=parsed, tool_calls=calls, model=model,
                           input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
                           cost_usd=_cost(model, response.usage.input_tokens, response.usage.output_tokens))

    def _read(self, response) -> tuple[str, list[LLMToolCall]]:
        with self._lock:
            u = self._usage
            u["calls"] += 1
            u["input_tokens"] += response.usage.input_tokens
            u["output_tokens"] += response.usage.output_tokens
            cost = _usage_cost(response.model, response.usage)
            u["cost_usd"] += cost
        with _SPENT_LOCK:
            _SPENT["usd"] += cost
            if int(_SPENT["usd"]) > _SPENT["logged"]:
                _SPENT["logged"] = int(_SPENT["usd"])
                logger.warning("claude spend so far: $%.2f", _SPENT["usd"])
        with self._lock:
            u["by_model"][response.model] = u["by_model"].get(response.model, 0) + 1
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [LLMToolCall(name=b.name, arguments=dict(b.input)) for b in response.content if b.type == "tool_use"]
        return text, calls

    def usage(self) -> dict[str, Any]:
        with self._lock:
            return {**self._usage, "by_model": dict(self._usage["by_model"]), "provider": "claude"}


def _validate(schema: type[BaseModel], text: str) -> tuple[BaseModel | None, str]:
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`").split("\n", 1)[-1]
    start, end = body.find("{"), body.rfind("}")
    try:
        return schema.model_validate_json(body[start:end + 1] if start >= 0 else body), ""
    except (ValidationError, ValueError) as exc:
        return None, str(exc)[:500]


def _usage_cost(model: str, usage) -> float:
    """Input billed at its rate, cache writes at 1.25x and cache reads at 0.1x of it, output at its rate."""
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    cached_in = 1.25 * (getattr(usage, "cache_creation_input_tokens", 0) or 0) + 0.1 * (getattr(usage, "cache_read_input_tokens", 0) or 0)
    return ((usage.input_tokens + cached_in) * price_in + usage.output_tokens * price_out) / 1_000_000


def _cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000
