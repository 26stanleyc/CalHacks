"""The real CRS sandbox client (engine/crs.py HttpCrs) on responses saved from the live sandbox
(engine/tests/fixtures/crs, test person P01), served by a fake transport: no test calls CRS."""

import json
from pathlib import Path

import httpx
import pytest

from contracts.conformance import sample_tx
from contracts.schemas import Action, Decision, FeatureVector, IdentityStatus
from engine.crs import (PERSONA_GROUPS, PERSONAS, THIN_HISTORY_S, CrsEscalation, HttpCrs, MockCrs, crs_from_env,
                        parse_fraud_finder, parse_identity, parse_prefill)

BASE = "https://api-sandbox.stitchcredit.com/api"
FIXTURES = Path(__file__).parent / "fixtures" / "crs"
SAVED = {name: json.loads((FIXTURES / f"{name}_P01.json").read_text())
         for name in ("data_prefill", "identity_verification", "fraud_finder")}
PATHS = {"/api/accurate-append/reverse": "data_prefill", "/api/transunion/identity-manager-verification/basic":
         "identity_verification", "/api/fraud-finder/fraud-finder": "fraud_finder"}
THIN = {"i_identity_status": 1.0, "i_principal_age_s": 0.0}
KNOWN = {"i_identity_status": 0.0, "i_principal_age_s": 10 * THIN_HISTORY_S}


class FakeSandbox:
    """Answers like the CRS sandbox, from the saved P01 responses unless a test overrides one."""

    def __init__(self, login_status=200, answers=None, product_status=200):
        self.login_status, self.product_status = login_status, product_status
        self.answers = {**SAVED, **(answers or {})}
        self.logins, self.requests = 0, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/api/users/login":
            self.logins += 1
            if self.login_status != 200:
                return httpx.Response(self.login_status, json={"codes": ["CRS113"], "messages": ["Access Denied"]})
            return httpx.Response(200, json={"token": "test-token", "name": "sandbox"})
        assert request.headers["Authorization"] == "Bearer test-token"
        product = PATHS[request.url.path]  # anything else (a credit pull) fails the test here
        self.requests.append((product, body))
        if self.product_status != 200:
            return httpx.Response(self.product_status, json={"codes": ["CRS111"]})
        return httpx.Response(200, json=self.answers[product])


def client(sandbox: FakeSandbox, kind_of: dict | None = None) -> HttpCrs:
    identities = {"p-1": IdentityStatus.VERIFIED, **(kind_of or {})}
    return HttpCrs(BASE, "CalhacksDev11", "not-a-real-password", identities, on_file={"p-1"},
                   http=httpx.Client(transport=httpx.MockTransport(sandbox)))


def check(crs_client, features=KNOWN, code_action=Action.STEP_UP) -> Decision:
    tx = sample_tx()  # principal p-1
    decision = Decision(tx_id=tx.tx_id, action=code_action, weighted_score=0.5, votes=[])
    return CrsEscalation(crs_client, low=0.3, high=0.7).check(tx, FeatureVector(tx_id=tx.tx_id, values=features), decision)


# --- parsing the saved live responses ------------------------------------------------------------

def test_saved_prefill_finds_the_person():
    out = parse_prefill(SAVED["data_prefill"])
    assert out["status"] == "found" and out["records"] == 1 and {"firstName", "lastName", "postalCode"} <= set(out["fields"])
    assert parse_prefill({"success": True, "data": {"transactionId": "x", "records": []}}) == {"status": "no_record"}


def test_saved_identity_verification_passes_with_no_messages():
    assert parse_identity(SAVED["identity_verification"])["result"] == "pass"


def with_messages(*codes: str) -> dict:
    body = json.loads(json.dumps(SAVED["identity_verification"]))
    body["addOnProducts"][0]["idManagerVerification"]["messages"] = [{"code": c, "text": f"problem {c}"} for c in codes]
    return body


@pytest.mark.parametrize("codes, result", [(("2201",), "fail"), (("2301",), "fail"), (("2101", "2202"), "fail"),
                                           (("2401",), "review"), (("2101",), "review")])
def test_identity_messages_map_to_fail_or_review(codes, result):
    assert parse_identity(with_messages(*codes))["result"] == result


def test_saved_fraudfinder_score_is_low():
    out = parse_fraud_finder(SAVED["fraud_finder"])
    assert (out["risk_score"], out["risk_level"], out["email_status"], out["phone_line"]) == (11, "low", "valid", "mobile")


# --- the client ------------------------------------------------------------------------------------

def test_logs_in_once_and_reuses_the_token():
    sandbox = FakeSandbox()
    crs = client(sandbox, {"p-2": IdentityStatus.THIN_FILE})
    for principal in ("p-1", "p-2", "p-1"):
        crs.data_prefill(principal), crs.identity_verification(principal, None), crs.fraud_finder(principal)
    assert sandbox.logins == 1


def test_sends_each_test_persons_body_exactly_and_asks_once_per_person():
    sandbox = FakeSandbox()
    crs = client(sandbox)
    persona = crs.persona("p-1")
    for _ in range(3):
        crs.identity_verification("p-1", None)
    assert sandbox.requests == [("identity_verification", PERSONAS[persona]["identity_verification"])]


def test_a_refused_login_is_never_retried():
    sandbox = FakeSandbox(login_status=401)
    crs = client(sandbox)
    first, second = check(crs, code_action=Action.ALLOW), check(crs, code_action=Action.ALLOW)
    assert sandbox.logins == 1
    for out in (first, second):
        assert out.action == Action.STEP_UP and "CRS unavailable" in out.crs.reasons and "CRS113" in out.crs.reasons
        assert not out.crs.mocked


def test_a_product_error_keeps_the_code_decision():
    out = check(client(FakeSandbox(product_status=401)), code_action=Action.BLOCK)
    assert out.action == Action.BLOCK and "CRS unavailable" in out.crs.reasons


def test_only_the_sandbox():
    with pytest.raises(ValueError):
        HttpCrs("https://api.stitchcredit.com/api", "u", "p")


def test_test_persons_stand_in_by_what_a_bureau_would_know():
    crs = client(FakeSandbox(), {"farm-1": IdentityStatus.THIN_FILE, "stolen-1": IdentityStatus.VERIFIED,
                                 "real-thin": IdentityStatus.THIN_FILE})
    crs.on_file |= {"real-thin"}
    assert crs.persona("p-1") in PERSONA_GROUPS["real_full"]
    assert crs.persona("real-thin") in PERSONA_GROUPS["real_thin"]
    assert crs.persona("farm-1") in PERSONA_GROUPS["synthetic"]
    assert crs.persona("stolen-1") in PERSONA_GROUPS["bought_full"]
    assert crs.persona("farm-1") == crs.persona("farm-1")
    assert all(p in PERSONAS for group in PERSONA_GROUPS.values() for p in group)


# --- live responses through the decision ----------------------------------------------------------

def test_saved_p01_responses_allow_a_thin_file_payment_with_all_three_calls():
    out = check(client(FakeSandbox()), THIN)
    assert out.action == Action.ALLOW and not out.crs.mocked
    assert [c.tool for c in out.crs.tool_calls] == ["crs_data_prefill", "crs_identity_verification", "crs_fraud_finder"]
    assert out.explanation.startswith("CRS check: ")


def test_a_failed_live_identity_check_blocks():
    out = check(client(FakeSandbox(answers={"identity_verification": with_messages("2202")})), code_action=Action.ALLOW)
    assert out.action == Action.BLOCK and len(out.crs.tool_calls) == 1


def test_a_high_live_fraudfinder_score_blocks():
    risky = json.loads(json.dumps(SAVED["fraud_finder"]))
    risky["risk"]["score"] = 88
    assert check(client(FakeSandbox(answers={"fraud_finder": risky}))).action == Action.BLOCK


def test_a_fraud_alert_steps_up():
    out = check(client(FakeSandbox(answers={"identity_verification": with_messages("2401")})), code_action=Action.ALLOW)
    assert out.action == Action.STEP_UP


# --- choosing the client --------------------------------------------------------------------------

def test_crs_escalation_picks_the_client(monkeypatch):
    for key in ("CRS_BASE_URL", "CRS_USERNAME", "CRS_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CRS_ESCALATION", "0")
    assert crs_from_env({}, set()) is None
    monkeypatch.setenv("CRS_ESCALATION", "1")
    assert isinstance(crs_from_env({}, set()).client, MockCrs)
    monkeypatch.setenv("CRS_ESCALATION", "live")
    with pytest.raises(ValueError, match="CRS_BASE_URL"):
        crs_from_env({}, set())
    monkeypatch.setenv("CRS_BASE_URL", BASE)
    monkeypatch.setenv("CRS_USERNAME", "u")
    monkeypatch.setenv("CRS_PASSWORD", "p")
    live = crs_from_env({}, set()).client
    assert isinstance(live, HttpCrs) and live._token is None  # no login until the first in-band payment


def test_forward_append_is_answered_locally_and_never_sent():
    sandbox = FakeSandbox()
    crs = client(sandbox, {"farm-1": IdentityStatus.THIN_FILE})
    out = crs.data_append("farm-1")
    assert sandbox.requests == [] and sandbox.logins == 0 and out["simulated"]
    assert out["status"] == ("no_match" if crs.persona("farm-1") in ("P04", "P13") else "found")
