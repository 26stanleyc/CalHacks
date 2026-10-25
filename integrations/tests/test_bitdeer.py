"""The Bitdeer `LLMClient` (milestone E3), driven by a fake transport: no network, no credits.

Covers the three things the contract promises callers - text, structured output and tool
calls - plus the two that come from the real API's behaviour: reasoning models that spend
the completion budget before answering, and a billing refusal that must not stop the run.
"""

import json

import httpx
import pytest
from pydantic import BaseModel, Field

from contracts.config import model_for
from contracts.llm import LLMClient, Message, MockLLM, ToolSpec
from contracts.schemas import PageScan
from integrations import model_prices
from integrations.bitdeer import (
    BitdeerBillingError,
    BitdeerError,
    BitdeerLLM,
    FallbackLLM,
    UsageLedger,
    make_llm,
)

KEY = "test-key-not-a-real-one"


class Verdict(BaseModel):
    verdict: str
    score: float = Field(ge=0.0, le=1.0)


def reply(content: str = "", *, tool_calls=None, finish="stop", usage=None, model="zai-org/GLM-5.3-Flash"):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "model": model,
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def client_for(handler) -> BitdeerLLM:
    """A `BitdeerLLM` whose HTTP calls go to `handler` instead of the network."""
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request, len(requests) - 1)

    llm = BitdeerLLM(
        api_key=KEY,
        base_url="https://bitdeer.test/v1",
        client=httpx.Client(transport=httpx.MockTransport(record)),
    )
    llm.requests = requests  # type: ignore[attr-defined]
    return llm


def always(*responses):
    """Answer each successive call with the next response, repeating the last."""

    def handler(request, index):
        return httpx.Response(200, json=responses[min(index, len(responses) - 1)])

    return handler


def body_of(request: httpx.Request) -> dict:
    return json.loads(request.content)


# --- the shared contract --------------------------------------------------------------


def test_satisfies_the_llm_contract():
    assert isinstance(client_for(always(reply("hi"))), LLMClient)
    assert isinstance(FallbackLLM(client_for(always(reply("hi")))), LLMClient)


def test_a_missing_api_key_is_an_error_not_a_silent_mock(monkeypatch):
    monkeypatch.delenv("BITDEER_API_KEY", raising=False)
    with pytest.raises(BitdeerError, match="BITDEER_API_KEY"):
        BitdeerLLM()


# --- text ------------------------------------------------------------------------------


def test_returns_the_models_text():
    llm = client_for(always(reply("PONG")))
    response = llm.complete([Message(role="user", content="ping")], role="chat")
    assert response.text == "PONG"
    assert response.input_tokens == 10 and response.output_tokens == 20


def test_the_model_comes_from_the_role_not_the_caller():
    llm = client_for(always(reply("ok")))
    llm.complete([Message(role="user", content="x")], role="critic")
    assert body_of(llm.requests[0])["model"] == model_for("critic") == "zai-org/GLM-5.3"


def test_every_request_sends_a_user_agent():
    """The default Python user agent gets a Cloudflare 403 from Bitdeer."""
    llm = client_for(always(reply("ok")))
    llm.complete([Message(role="user", content="x")], role="chat")
    assert llm.requests[0].headers["user-agent"].startswith("calhacks")
    assert llm.requests[0].headers["authorization"] == f"Bearer {KEY}"


def test_the_answer_budget_gets_reasoning_headroom():
    """Bitdeer's models think inside the completion budget, so `max_tokens` is not it."""
    llm = client_for(always(reply("ok")))
    llm.complete([Message(role="user", content="x")], role="chat", max_tokens=100)
    assert body_of(llm.requests[0])["max_tokens"] > 100


def test_a_cut_off_answer_is_retried_with_more_room():
    """A reasoning model can burn the whole budget and return nothing; one retry fixes it."""
    llm = client_for(always(reply("", finish="length"), reply("the answer")))
    response = llm.complete([Message(role="user", content="x")], role="chat", max_tokens=50)
    assert response.text == "the answer"
    assert len(llm.requests) == 2
    assert body_of(llm.requests[1])["max_tokens"] > body_of(llm.requests[0])["max_tokens"]


def test_a_cut_off_answer_that_had_content_is_not_retried():
    llm = client_for(always(reply("partial", finish="length")))
    assert llm.complete([Message(role="user", content="x")], role="chat").text == "partial"
    assert len(llm.requests) == 1


# --- structured output ----------------------------------------------------------------


def test_structured_output_is_parsed_into_the_schema():
    llm = client_for(always(reply(json.dumps({"verdict": "block", "score": 0.9}))))
    response = llm.complete([Message(role="user", content="x")], role="critic", response_schema=Verdict)
    assert isinstance(response.parsed, Verdict)
    assert response.parsed.verdict == "block" and response.parsed.score == 0.9


def test_the_schema_is_sent_as_json_schema():
    llm = client_for(always(reply(json.dumps({"verdict": "allow", "score": 0.1}))))
    llm.complete([Message(role="user", content="x")], role="critic", response_schema=Verdict)
    response_format = body_of(llm.requests[0])["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "Verdict"


def test_a_fenced_json_answer_still_parses():
    fenced = "```json\n{\"verdict\": \"block\", \"score\": 0.5}\n```"
    llm = client_for(always(reply(fenced)))
    response = llm.complete([Message(role="user", content="x")], role="critic", response_schema=Verdict)
    assert isinstance(response.parsed, Verdict)


def test_an_answer_outside_the_schema_is_repaired():
    """score=1.6 breaks the le=1.0 bound, which the real API does produce."""
    bad = json.dumps({"verdict": "block", "score": 1.6})
    good = json.dumps({"verdict": "block", "score": 1.0})
    llm = client_for(always(reply(bad), reply(good)))
    response = llm.complete([Message(role="user", content="x")], role="critic", response_schema=Verdict)
    assert isinstance(response.parsed, Verdict) and response.parsed.score == 1.0
    assert "did not match the required schema" in body_of(llm.requests[1])["messages"][-1]["content"]


def test_a_schema_that_never_validates_returns_text_rather_than_raising():
    llm = client_for(always(reply("not json at all")))
    response = llm.complete([Message(role="user", content="x")], role="critic", response_schema=Verdict)
    assert response.parsed is None
    assert response.text


def test_a_rejected_response_format_degrades_to_plain_json_mode():
    """If Bitdeer will not take a schema, structured output still has to work."""

    def handler(request, index):
        if index == 0:
            return httpx.Response(400, json={"error": "response_format not supported"})
        return httpx.Response(200, json=reply(json.dumps({"verdict": "allow", "score": 0.2})))

    llm = client_for(handler)
    response = llm.complete([Message(role="user", content="x")], role="critic", response_schema=Verdict)
    assert isinstance(response.parsed, Verdict)
    assert body_of(llm.requests[1])["response_format"] == {"type": "json_object"}


def test_a_nested_contract_schema_parses():
    scan = {"url": "https://x.test/p", "injection_score": 0.9, "findings": ["hidden text"], "scanned_at": "2026-01-01T00:00:00Z"}
    llm = client_for(always(reply(json.dumps(scan))))
    response = llm.complete([Message(role="user", content="x")], role="chat", response_schema=PageScan)
    assert isinstance(response.parsed, PageScan) and response.parsed.injection_score == 0.9


# --- tool calls -----------------------------------------------------------------------

SCAN_TOOL = ToolSpec(
    name="scan_page",
    description="Scan a page",
    parameters={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
)


def test_tool_calls_are_returned():
    calls = [{"id": "c1", "type": "function", "function": {"name": "scan_page", "arguments": '{"url": "https://x.test"}'}}]
    llm = client_for(always(reply("", tool_calls=calls, finish="tool_calls")))
    response = llm.complete([Message(role="user", content="x")], role="chat", tools=[SCAN_TOOL])
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "scan_page"
    assert response.tool_calls[0].arguments == {"url": "https://x.test"}


def test_tools_are_sent_in_the_openai_shape():
    llm = client_for(always(reply("ok")))
    llm.complete([Message(role="user", content="x")], role="chat", tools=[SCAN_TOOL])
    sent = body_of(llm.requests[0])["tools"][0]
    assert sent["type"] == "function" and sent["function"]["name"] == "scan_page"


def test_unparseable_tool_arguments_become_an_empty_dict():
    calls = [{"id": "c1", "type": "function", "function": {"name": "scan_page", "arguments": "{not json"}}]
    llm = client_for(always(reply("", tool_calls=calls, finish="tool_calls")))
    response = llm.complete([Message(role="user", content="x")], role="chat", tools=[SCAN_TOOL])
    assert response.tool_calls[0].arguments == {}


def test_a_nameless_tool_call_is_dropped():
    calls = [{"id": "c1", "type": "function", "function": {"arguments": "{}"}}]
    llm = client_for(always(reply("", tool_calls=calls, finish="tool_calls")))
    assert llm.complete([Message(role="user", content="x")], role="chat", tools=[SCAN_TOOL]).tool_calls == []


# --- billing: nothing may stop because credits ran out -------------------------------


@pytest.mark.parametrize(
    "status,body",
    [
        (402, {"error": "payment required"}),
        (403, {"error": "insufficient balance"}),
        (429, {"error": "you have exceeded your current quota"}),
        (401, {"error": "billing is not set up"}),
    ],
)
def test_a_billing_refusal_is_its_own_error(status, body):
    llm = client_for(lambda request, index: httpx.Response(status, json=body))
    with pytest.raises(BitdeerBillingError):
        llm.complete([Message(role="user", content="x")], role="chat")


def test_an_ordinary_auth_failure_is_not_treated_as_billing():
    llm = client_for(lambda request, index: httpx.Response(401, json={"error": "invalid api key"}))
    with pytest.raises(BitdeerError) as caught:
        llm.complete([Message(role="user", content="x")], role="chat")
    assert not isinstance(caught.value, BitdeerBillingError)


def test_the_fallback_switches_to_mock_on_a_billing_refusal():
    llm = client_for(lambda request, index: httpx.Response(402, json={"error": "insufficient credit"}))
    fallback = FallbackLLM(llm)

    response = fallback.complete([Message(role="user", content="x")], role="chat")
    assert fallback.fell_back is True
    assert response.model.startswith("mock:")
    assert "credit" in fallback.fell_back_reason

    # And it stays on the mock rather than retrying Bitdeer on every call.
    before = len(llm.requests)
    fallback.complete([Message(role="user", content="y")], role="chat")
    assert len(llm.requests) == before
    assert fallback.usage()["fell_back_to_mock"] is True


def test_the_fallback_keeps_structured_output_working():
    llm = client_for(lambda request, index: httpx.Response(402, json={"error": "insufficient credit"}))
    response = FallbackLLM(llm).complete([Message(role="user", content="x")], role="chat", response_schema=PageScan)
    assert isinstance(response.parsed, PageScan), "the mock must still satisfy the schema"


def test_a_non_billing_error_is_not_swallowed():
    llm = client_for(lambda request, index: httpx.Response(500, json={"error": "upstream exploded"}))
    with pytest.raises(BitdeerError):
        FallbackLLM(llm).complete([Message(role="user", content="x")], role="chat")


def test_a_transport_failure_is_reported_as_a_bitdeer_error():
    def boom(request, index):
        raise httpx.ConnectError("no route to host")

    with pytest.raises(BitdeerError, match="request failed"):
        client_for(boom).complete([Message(role="user", content="x")], role="chat")


# --- make_llm -------------------------------------------------------------------------


def test_make_llm_is_mock_without_a_key(monkeypatch):
    monkeypatch.delenv("BITDEER_API_KEY", raising=False)
    assert isinstance(make_llm(), MockLLM)


def test_make_llm_is_mock_when_the_flag_says_so(monkeypatch):
    monkeypatch.setenv("BITDEER_API_KEY", KEY)
    monkeypatch.setenv("USE_MOCK_LLM", "1")
    assert isinstance(make_llm(), MockLLM)


def test_make_llm_uses_bitdeer_when_a_key_is_set(monkeypatch):
    monkeypatch.setenv("BITDEER_API_KEY", KEY)
    monkeypatch.setenv("BITDEER_BASE_URL", "https://bitdeer.test/v1")
    assert isinstance(make_llm(), FallbackLLM)


def test_force_mock_overrides_the_config(monkeypatch):
    monkeypatch.setenv("BITDEER_API_KEY", KEY)
    assert isinstance(make_llm(force_mock=True), MockLLM)


# --- the cost ledger ------------------------------------------------------------------


def test_usage_is_recorded_per_model_and_per_role():
    llm = client_for(always(reply("ok", usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})))
    llm.complete([Message(role="user", content="x")], role="critic")
    llm.complete([Message(role="user", content="y")], role="critic")
    usage = llm.usage()
    assert usage["calls"] == 2
    assert usage["input_tokens"] == 200 and usage["output_tokens"] == 100
    assert usage["by_role"]["critic"]["calls"] == 2


def test_a_repaired_call_is_billed_once_per_request():
    """The repair is a second request, so it is a second billed call - not three."""
    bad = json.dumps({"verdict": "block", "score": 1.6})
    good = json.dumps({"verdict": "block", "score": 1.0})
    llm = client_for(always(reply(bad), reply(good)))
    llm.complete([Message(role="user", content="x")], role="critic", response_schema=Verdict)
    assert llm.usage()["calls"] == len(llm.requests) == 2


def test_unknown_prices_cost_zero_and_are_reported():
    """Bitdeer does not publish prices over the API, so spend must not be guessed."""
    assert model_prices.cost_usd("zai-org/GLM-5.3", 1_000_000, 1_000_000) == 0.0
    assert "zai-org/GLM-5.3" in model_prices.unpriced_models()


def test_a_price_can_be_set_from_the_environment(monkeypatch):
    monkeypatch.setenv("BITDEER_PRICE_IN_ZAI_ORG_GLM_5_3", "0.60")
    monkeypatch.setenv("BITDEER_PRICE_OUT_ZAI_ORG_GLM_5_3", "2.20")
    assert model_prices.price_for("zai-org/GLM-5.3") == (0.60, 2.20)
    assert model_prices.cost_usd("zai-org/GLM-5.3", 1_000_000, 500_000) == pytest.approx(0.60 + 1.10)
    assert "zai-org/GLM-5.3" not in model_prices.unpriced_models()


def test_the_ledger_totals_add_up():
    ledger = UsageLedger()
    ledger.record(model="m", role="critic", input_tokens=10, output_tokens=5, cost_usd=0.01)
    ledger.record(model="m", role="timing", input_tokens=20, output_tokens=5, cost_usd=0.02)
    snapshot = ledger.snapshot()
    assert snapshot["calls"] == 2 and snapshot["input_tokens"] == 30
    assert snapshot["cost_usd"] == pytest.approx(0.03)
    assert set(snapshot["by_role"]) == {"critic", "timing"}
