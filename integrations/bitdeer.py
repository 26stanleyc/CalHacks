"""The real `LLMClient` (contracts/llm.py) on Bitdeer's OpenAI-compatible API.

Every LLM call in the system goes through `contracts.llm.LLMClient`, so this is the only
place that knows Bitdeer exists. Use `make_llm()` rather than constructing a client:
it honours `contracts.config.use_mock_llm()` and falls back to `MockLLM` if Bitdeer
refuses the call for a billing reason, so the system keeps running on credits trouble.

    from integrations.bitdeer import make_llm
    llm = make_llm()
    answer = llm.complete([Message(role="user", content="...")], role="critic")

Notes from probing the real API at M0:
- Requests need a `User-Agent`; the default Python one gets a Cloudflare 403.
- Bitdeer's chat models are reasoning models. They spend `completion_tokens` on hidden
  `reasoning_content` *before* the answer, so a tight `max_tokens` returns empty content
  with `finish_reason="length"`. `max_tokens` here is the answer budget and the client
  adds reasoning headroom on top, then retries once with more if the answer was cut off.
- `response_format={"type": "json_schema"}` and `tools` both work.
"""

import json
import logging
import os
import threading
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from contracts.config import model_for, use_mock_llm
from contracts.llm import LLMClient, LLMResponse, LLMToolCall, Message, MockLLM, ToolSpec
from integrations import model_prices
from integrations.settings import USER_AGENT

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api-inference.bitdeer.ai/v1"

# Reasoning tokens come out of the same completion budget as the answer, so ask for the
# caller's answer budget plus room to think. Tunable with BITDEER_REASONING_HEADROOM.
DEFAULT_REASONING_HEADROOM = 4_000
DEFAULT_REASONING_EFFORT = "low"  # GLM honors it (about 10x less hidden reasoning); DeepSeek ignores it

# Substrings that mark a refusal as "no credits" rather than a broken request.
_BILLING_MARKERS = (
    "insufficient",
    "balance",
    "credit",
    "quota",
    "billing",
    "payment required",
    "exceeded your current",
)


class BitdeerError(RuntimeError):
    """Bitdeer refused or failed a call."""


class BitdeerBillingError(BitdeerError):
    """Bitdeer refused the call for a billing reason: no credits, or quota exhausted."""


class UsageLedger:
    """Running token and cost totals, per model and per role (spec 4.1 spend tracking)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0
        self.by_model: dict[str, dict[str, float]] = {}
        self.by_role: dict[str, dict[str, float]] = {}

    def record(self, *, model: str, role: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.cost_usd += cost_usd
            for bucket, key in ((self.by_model, model), (self.by_role, role)):
                row = bucket.setdefault(key, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
                row["calls"] += 1
                row["input_tokens"] += input_tokens
                row["output_tokens"] += output_tokens
                row["cost_usd"] += cost_usd

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "calls": self.calls,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cost_usd": round(self.cost_usd, 6),
                "unpriced_models": model_prices.unpriced_models(),
                "by_model": {k: dict(v) for k, v in self.by_model.items()},
                "by_role": {k: dict(v) for k, v in self.by_role.items()},
            }


class BitdeerLLM:
    """`contracts.llm.LLMClient` over Bitdeer Model Studio.

    The model comes from the role via `contracts.config.model_for`, so callers name a
    role ("critic", "investigator", ...) and never a model id.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 180.0,
        ledger: UsageLedger | None = None,
        reasoning_headroom: int | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("BITDEER_API_KEY", "")
        if not self.api_key:
            raise BitdeerError("BITDEER_API_KEY is not set; use make_llm() to fall back to MockLLM.")
        self.base_url = (base_url or os.environ.get("BITDEER_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.ledger = ledger or UsageLedger()
        self.reasoning_headroom = (
            reasoning_headroom
            if reasoning_headroom is not None
            else int(os.environ.get("BITDEER_REASONING_HEADROOM", DEFAULT_REASONING_HEADROOM))
        )
        self.reasoning_effort = os.environ.get("BITDEER_REASONING_EFFORT", DEFAULT_REASONING_EFFORT)
        self._client = client or httpx.Client(timeout=timeout)

    # -- public API ------------------------------------------------------------------

    def complete(
        self,
        messages: list[Message],
        *,
        role: str,
        response_schema: type[BaseModel] | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        model = model_for(role)
        body: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
            "max_tokens": max_tokens + self.reasoning_headroom,
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if tools:
            body["tools"] = [_tool_to_openai(t) for t in tools]
            body["tool_choice"] = "auto"
        if response_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": response_schema.__name__, "schema": response_schema.model_json_schema()},
            }

        raw = self._post(body, schema=response_schema)

        # The answer can be empty when the model spent the whole budget thinking.
        if _was_truncated(raw) and not _content(raw) and not _raw_tool_calls(raw):
            body["max_tokens"] = max_tokens + self.reasoning_headroom * 2
            logger.debug("Bitdeer answer was cut off on %s; retrying with more room", model)
            raw = self._post(body, schema=response_schema)

        response = self._to_response(raw, model=model, role=role)

        if response_schema is not None and response.parsed is None:
            response = self._repair(body, raw, response_schema, model=model, role=role) or response
        return response

    def usage(self) -> dict[str, Any]:
        """Tokens and cost so far, for the spend view."""
        return self.ledger.snapshot()

    def close(self) -> None:
        self._client.close()

    # -- HTTP ------------------------------------------------------------------------

    def _post(self, body: dict[str, Any], *, schema: type[BaseModel] | None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            response = self._client.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise BitdeerError(f"Bitdeer request failed: {exc}") from exc

        if response.status_code != 200:
            detail = response.text[:500]
            if _looks_like_billing(response.status_code, detail):
                raise BitdeerBillingError(f"Bitdeer refused the call ({response.status_code}): {detail}")
            # A schema the API won't take: retry once as a plain JSON object with the
            # schema spelled out in the prompt, so structured output still works.
            if response.status_code == 400 and schema is not None and "response_format" in body:
                return self._post(_degrade_to_json_object(body, schema), schema=None)
            raise BitdeerError(f"Bitdeer returned {response.status_code}: {detail}")

        try:
            return response.json()
        except ValueError as exc:
            raise BitdeerError(f"Bitdeer returned a non-JSON body: {response.text[:200]}") from exc

    # -- response handling -----------------------------------------------------------

    def _to_response(self, raw: dict[str, Any], *, model: str, role: str) -> LLMResponse:
        usage = raw.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        used_model = raw.get("model") or model
        cost = model_prices.cost_usd(used_model, input_tokens, output_tokens)
        self.ledger.record(
            model=used_model, role=role, input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost
        )

        text = _content(raw)
        tool_calls = []
        for call in _raw_tool_calls(raw):
            function = call.get("function") or {}
            name = function.get("name") or ""
            if not name:
                continue
            arguments = function.get("arguments")
            tool_calls.append(LLMToolCall(name=name, arguments=_loads_dict(arguments)))

        return LLMResponse(
            text=text,
            parsed=None,
            tool_calls=tool_calls,
            model=used_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )

    def _repair(
        self,
        body: dict[str, Any],
        raw: dict[str, Any],
        schema: type[BaseModel],
        *,
        model: str,
        role: str,
    ) -> LLMResponse | None:
        """One more try when the answer didn't validate, quoting the validation error."""
        text = _content(raw)
        parsed, error = _parse(text, schema)
        if parsed is not None:
            response = self._to_response(raw, model=model, role=role)
            # _to_response already billed this call; don't bill it twice.
            self.ledger.calls -= 1
            return response.model_copy(update={"parsed": parsed})

        retry = dict(body)
        retry["messages"] = [
            *body["messages"],
            {"role": "assistant", "content": text or ""},
            {
                "role": "user",
                "content": (
                    "That answer did not match the required schema. Reply with the corrected "
                    f"JSON object only, no prose.\n\nValidation error:\n{error}"
                ),
            },
        ]
        try:
            raw_retry = self._post(retry, schema=schema)
        except BitdeerError:
            logger.warning("Bitdeer schema repair call failed for role %s", role)
            return None

        response = self._to_response(raw_retry, model=model, role=role)
        parsed, error = _parse(response.text, schema)
        if parsed is None:
            logger.warning("Bitdeer still did not return valid %s for role %s: %s", schema.__name__, role, error)
            return response
        return response.model_copy(update={"parsed": parsed})


class FallbackLLM:
    """Bitdeer first; `MockLLM` from the moment Bitdeer refuses for a billing reason.

    The point is spec 4.1: credits may be pending or run out mid-run, and nothing in the
    system should stop because of it. The switch is one-way and logged once, and
    `fell_back_reason` records why so a report can say the run went mock.
    """

    def __init__(self, primary: LLMClient, fallback: LLMClient | None = None) -> None:
        self.primary = primary
        self.fallback = fallback or MockLLM()
        self.fell_back = False
        self.fell_back_reason: str | None = None

    def complete(
        self,
        messages: list[Message],
        *,
        role: str,
        response_schema: type[BaseModel] | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        if not self.fell_back:
            try:
                return self.primary.complete(
                    messages, role=role, response_schema=response_schema, tools=tools, max_tokens=max_tokens
                )
            except BitdeerBillingError as exc:
                self.fell_back = True
                self.fell_back_reason = str(exc)
                logger.error("Bitdeer is refusing calls for a billing reason; switching to MockLLM. %s", exc)
        return self.fallback.complete(
            messages, role=role, response_schema=response_schema, tools=tools, max_tokens=max_tokens
        )

    def usage(self) -> dict[str, Any]:
        usage = self.primary.usage() if hasattr(self.primary, "usage") else {}
        return {**usage, "fell_back_to_mock": self.fell_back, "fell_back_reason": self.fell_back_reason}


def make_llm(*, force_mock: bool | None = None) -> LLMClient:
    """The client every part should use.

    `MockLLM` when `contracts.config.use_mock_llm()` says so (no key, or USE_MOCK_LLM=1),
    otherwise Bitdeer wrapped in `FallbackLLM`.
    """
    if force_mock if force_mock is not None else use_mock_llm():
        return MockLLM()
    try:
        return FallbackLLM(BitdeerLLM())
    except BitdeerError as exc:
        logger.error("Could not build the Bitdeer client (%s); using MockLLM", exc)
        return MockLLM()


# --- helpers -------------------------------------------------------------------------


def _tool_to_openai(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters or {"type": "object", "properties": {}},
        },
    }


def _first_message(raw: dict[str, Any]) -> dict[str, Any]:
    choices = raw.get("choices") or []
    if not choices:
        return {}
    return choices[0].get("message") or {}


def _content(raw: dict[str, Any]) -> str:
    return (_first_message(raw).get("content") or "").strip()


def _raw_tool_calls(raw: dict[str, Any]) -> list[dict[str, Any]]:
    return _first_message(raw).get("tool_calls") or []


def _was_truncated(raw: dict[str, Any]) -> bool:
    choices = raw.get("choices") or []
    return bool(choices) and choices[0].get("finish_reason") == "length"


def _loads_dict(value: Any) -> dict[str, Any]:
    """Tool-call arguments arrive as a JSON string; a bad one becomes an empty dict."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        loaded = json.loads(value)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _parse(text: str, schema: type[BaseModel]) -> tuple[BaseModel | None, str]:
    """Validate `text` against `schema`, tolerating a fenced code block around it."""
    candidate = _strip_fence(text)
    if not candidate:
        return None, "the model returned no content"
    try:
        return schema.model_validate_json(candidate), ""
    except ValidationError as exc:
        return None, str(exc)[:1000]
    except ValueError as exc:
        return None, f"not valid JSON: {exc}"


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    return body.rsplit("```", 1)[0].strip()


def _degrade_to_json_object(body: dict[str, Any], schema: type[BaseModel]) -> dict[str, Any]:
    """Drop `json_schema` for plain JSON mode, with the schema moved into the prompt."""
    degraded = dict(body)
    degraded["response_format"] = {"type": "json_object"}
    degraded["messages"] = [
        *body["messages"],
        {
            "role": "user",
            "content": (
                "Reply with a single JSON object and nothing else. It must validate against "
                f"this JSON schema:\n{json.dumps(schema.model_json_schema())}"
            ),
        },
    ]
    return degraded


def _looks_like_billing(status: int, detail: str) -> bool:
    if status == 402:
        return True
    if status not in (401, 403, 429):
        return False
    lowered = detail.lower()
    return any(marker in lowered for marker in _BILLING_MARKERS)
