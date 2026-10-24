"""M0 integration probes: is each outside service really there? (spec 8.2, M0; milestone E1)

    uv run python scripts/probe_integrations.py
    uv run python scripts/probe_integrations.py --no-llm-calls   # skip the paid calls

Prints one line per integration:

    OK      reached it for real
    MOCK    deliberately standing in for it (no key, or on hold) - the system still runs
    FAIL    it should work and does not; this one needs looking at

Writes `evidence/m0/model-prices.md` and `evidence/m0/crs-decision.md`. Exits non-zero
only on FAIL, so it can gate a commit.

No secret is ever printed: keys are reported as present or missing, and nothing else.
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from contracts.config import MODEL_ROLES, use_mock_llm  # noqa: E402
from integrations.settings import USER_AGENT  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

EVIDENCE = REPO_ROOT / "evidence" / "m0"
TIMEOUT = 30.0

# The 5 chat models from spec 2.1, deduplicated from the role assignment.
CHAT_MODELS = sorted({model for role, model in MODEL_ROLES.items() if role != "held_out"} | {MODEL_ROLES["held_out"]})

OK, MOCK, FAIL = "OK", "MOCK", "FAIL"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.rows.append((name, status, detail))
        mark = {OK: "OK  ", MOCK: "MOCK", FAIL: "FAIL"}[status]
        print(f"  [{mark}] {name}" + (f" - {detail}" if detail else ""), flush=True)

    @property
    def failed(self) -> bool:
        return any(status == FAIL for _, status, _ in self.rows)


def headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Always send a User-Agent: Bitdeer and x402 sit behind Cloudflare, which 403s Python's default."""
    return {"User-Agent": USER_AGENT, "Accept": "application/json", **(extra or {})}


def present(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


# --- Bitdeer -------------------------------------------------------------------------


def probe_bitdeer(report: Report, *, llm_calls: bool) -> None:
    print("\nBitdeer Model Studio")
    base = os.environ.get("BITDEER_BASE_URL", "").rstrip("/")
    if not base:
        report.add("Bitdeer config", FAIL, "BITDEER_BASE_URL is not set")
        return
    if not present("BITDEER_API_KEY"):
        report.add("Bitdeer", MOCK, "BITDEER_API_KEY is missing; everything runs on MockLLM")
        write_model_prices(None, note="No API key at probe time, so no models were listed.")
        return

    auth = headers({"Authorization": f"Bearer {os.environ['BITDEER_API_KEY']}"})
    try:
        response = httpx.get(f"{base}/models", headers=auth, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        report.add("Bitdeer /models", FAIL, f"{type(exc).__name__}: {exc}")
        return
    if response.status_code != 200:
        report.add("Bitdeer /models", FAIL, f"HTTP {response.status_code}")
        return

    listed = [m.get("id") for m in response.json().get("data", []) if m.get("id")]
    missing = [m for m in CHAT_MODELS if m not in listed]
    detail = f"{len(listed)} models listed; all 5 chat models from spec 2.1 present"
    report.add("Bitdeer /models", OK if not missing else FAIL, detail if not missing else f"missing {missing}")

    # Does the API expose prices anywhere? Checked rather than assumed.
    priced = [m for m in response.json().get("data", []) if _price_fields(m)]
    if priced:
        report.add("Bitdeer prices", OK, f"the API exposes pricing on {len(priced)} model(s)")
    else:
        report.add("Bitdeer prices", MOCK, "not exposed by the API; check the Bitdeer console")

    if not llm_calls:
        report.add("Bitdeer completion", MOCK, "skipped (--no-llm-calls)")
        write_model_prices(listed, note="Live calls skipped with --no-llm-calls.")
        return

    # One real call per chat model: proves credits have arrived (spec 8.2, M0).
    results: dict[str, str] = {}
    for model in CHAT_MODELS:
        results[model] = _try_completion(base, auth, model)
    working = [m for m, r in results.items() if r == "ok"]
    billing = [m for m, r in results.items() if r == "billing"]
    if len(working) == len(CHAT_MODELS):
        report.add("Bitdeer completions", OK, f"one real call succeeded on all {len(working)} chat models")
    elif billing:
        report.add("Bitdeer completions", MOCK, f"refused for billing on {len(billing)} model(s); MockLLM covers it")
    elif working:
        report.add("Bitdeer completions", FAIL, f"worked on {len(working)}/{len(CHAT_MODELS)}: {results}")
    else:
        report.add("Bitdeer completions", FAIL, f"no model answered: {results}")

    report.add(
        "LLM mode",
        MOCK if use_mock_llm() else OK,
        "contracts.config.use_mock_llm() is True; calls go to MockLLM"
        if use_mock_llm()
        else "real Bitdeer calls are live",
    )
    write_model_prices(listed, live=results)


def _price_fields(model: dict) -> dict:
    return {k: v for k, v in model.items() if "pric" in k.lower() or "cost" in k.lower()}


def _try_completion(base: str, auth: dict[str, str], model: str) -> str:
    """"ok", "billing" or an error string. Tiny request, generous budget.

    Bitdeer's chat models are reasoning models: they spend the completion budget on hidden
    reasoning first, so a small max_tokens returns empty content rather than an answer.
    """
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
        "max_tokens": 2_000,
    }
    try:
        response = httpx.post(f"{base}/chat/completions", headers=auth, json=body, timeout=120.0)
    except httpx.HTTPError as exc:
        return f"{type(exc).__name__}"
    if response.status_code == 200:
        usage = response.json().get("usage") or {}
        return "ok" if usage.get("total_tokens") else "empty"
    lowered = response.text.lower()
    if response.status_code == 402 or any(
        word in lowered for word in ("insufficient", "balance", "credit", "quota", "billing")
    ):
        return "billing"
    return f"HTTP {response.status_code}"


def write_model_prices(listed: list[str] | None, *, note: str = "", live: dict[str, str] | None = None) -> None:
    """evidence/m0/model-prices.md: what the probe saw, for the budget in spec 4.1."""
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Bitdeer model prices (M0)",
        "",
        f"Probed {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `scripts/probe_integrations.py`.",
        "",
        "## Prices",
        "",
        "**Bitdeer's OpenAI-compatible API does not expose pricing.** `GET /v1/models` returns",
        "`id`, `object`, `created` and `owned_by` only, with no price field, so the numbers below",
        "have to be read off the Bitdeer console at https://www.bitdeer.ai/ and filled in by hand.",
        "",
        "Once known, put them in `integrations/model_prices.py` (or set",
        "`BITDEER_PRICE_IN_<MODEL>` / `BITDEER_PRICE_OUT_<MODEL>`) and the cost ledger in",
        "`integrations.bitdeer.UsageLedger` starts reporting real spend. Until then token counts",
        "are recorded and cost is reported as $0 with the model listed under `unpriced_models`.",
        "",
        "| Role | Model | $/1M input | $/1M output |",
        "|---|---|---|---|",
    ]
    for role, model in MODEL_ROLES.items():
        lines.append(f"| {role} | `{model}` | _console_ | _console_ |")
    lines += [
        "",
        "## Models the API listed",
        "",
    ]
    if listed:
        lines += [f"- `{model}`" for model in listed]
    else:
        lines.append("_The model list could not be read._")
    if live:
        lines += ["", "## One live call per chat model (spec 8.2, M0)", "", "| Model | Result |", "|---|---|"]
        lines += [f"| `{model}` | {result} |" for model, result in live.items()]
    if note:
        lines += ["", f"**Note:** {note}"]
    lines += [
        "",
        "## Budget check (spec 4.1)",
        "",
        "The $0.60-per-round estimate cannot be confirmed until the console prices are in.",
        "Recompute it then; if a round comes out over $0.60, shrink traffic or the escalation",
        "cap first, as 4.1 says.",
        "",
    ]
    (EVIDENCE / "model-prices.md").write_text("\n".join(lines))


# --- x402 ----------------------------------------------------------------------------


def probe_x402(report: Report) -> None:
    print("\nCoinbase x402 facilitator")
    url = os.environ.get("X402_FACILITATOR_URL", "").rstrip("/")
    if not url:
        report.add("x402 config", FAIL, "X402_FACILITATOR_URL is not set")
        return
    try:
        response = httpx.get(f"{url}/supported", headers=headers(), timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError as exc:
        report.add("x402 /supported", FAIL, f"{type(exc).__name__}: {exc}")
        return
    if response.status_code != 200:
        report.add("x402 /supported", FAIL, f"HTTP {response.status_code}")
        return

    kinds = response.json().get("kinds", [])
    base_sepolia = [
        k for k in kinds if k.get("network") in ("eip155:84532", "base-sepolia") and k.get("scheme") == "exact"
    ]
    if base_sepolia:
        report.add("x402 /supported", OK, f"exact on Base Sepolia, {len(kinds)} kinds in total")
    else:
        report.add("x402 /supported", FAIL, "the facilitator does not offer exact on Base Sepolia")

    # Wallets: needed for real settlement, not for the stub used in precompute.
    has_buyer = present("TESTNET_WALLET_PRIVATE_KEY") and present("TESTNET_WALLET_ADDRESS")
    has_merchant = present("MERCHANT_WALLET_ADDRESS")
    if has_buyer and has_merchant:
        report.add("x402 wallets", OK, "buyer and merchant testnet wallets are configured")
    else:
        missing = [
            name
            for name, ok in (("buyer", has_buyer), ("merchant", has_merchant))
            if not ok
        ]
        report.add("x402 wallets", MOCK, f"no {', '.join(missing)} wallet; settlement runs in stub mode")


# --- CRS -----------------------------------------------------------------------------


def probe_crs(report: Report) -> None:
    print("\nCRS identity verification")
    if not present("CRS_API_KEY"):
        report.add("CRS", MOCK, "no API key; on hold (spec 11), simulated identity profiles stay")
        write_crs_decision(on_hold=True, reason="CRS_API_KEY is empty at M0.")
        return

    base = os.environ.get("CRS_BASE_URL", "").rstrip("/")
    if not base:
        report.add("CRS", MOCK, "key is set but CRS_BASE_URL is not; on hold")
        write_crs_decision(on_hold=True, reason="CRS_API_KEY is set but CRS_BASE_URL is empty.")
        return
    try:
        response = httpx.get(base, headers=headers({"Authorization": f"Bearer {os.environ['CRS_API_KEY']}"}), timeout=TIMEOUT)
        reachable = response.status_code < 500
    except httpx.HTTPError as exc:
        report.add("CRS", MOCK, f"unreachable ({type(exc).__name__}); on hold")
        write_crs_decision(on_hold=True, reason=f"CRS_BASE_URL was unreachable: {type(exc).__name__}.")
        return
    if reachable:
        report.add("CRS", OK, f"reachable, HTTP {response.status_code}; a sandbox identity still has to be verified by hand")
        write_crs_decision(on_hold=False, reason=f"CRS_BASE_URL answered HTTP {response.status_code}.")
    else:
        report.add("CRS", MOCK, f"HTTP {response.status_code}; on hold")
        write_crs_decision(on_hold=True, reason=f"CRS_BASE_URL returned HTTP {response.status_code}.")


def write_crs_decision(*, on_hold: bool, reason: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    verdict = "on hold" if on_hold else "available - decide whether to use it"
    lines = [
        "# CRS decision (M0)",
        "",
        f"**{verdict}**",
        "",
        f"Checked {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `scripts/probe_integrations.py`.",
        "",
        f"Reason: {reason}",
        "",
    ]
    if on_hold:
        lines += [
            "Per spec 11, with no CRS sandbox access we keep the simulated identity profiles",
            "(clean / thin-file / flagged) and do not submit to the CRS track. `Principal.crs_profile_id`",
            "stays `None`. The step-up stays a fixed persona pass rate rather than a live",
            "re-verification. Nothing else in the build depends on this.",
            "",
            "To revisit: set `CRS_API_KEY` and `CRS_BASE_URL` in `.env` and run the probe again.",
            "",
        ]
    else:
        lines += [
            "The endpoint answers. Before turning CRS on, spec 11 still needs:",
            "",
            "- sandbox **test identities** we can map simulated principals onto",
            "- identity verification only, never credit pulls",
            "- recorded responses for precompute; live calls capped at 10-20 per round",
            "",
        ]
    (EVIDENCE / "crs-decision.md").write_text("\n".join(lines))


# --- Agentverse ----------------------------------------------------------------------


def probe_agentverse(report: Report) -> None:
    print("\nFetch.ai Agentverse / ASI:One")
    if not present("AGENTVERSE_API_KEY"):
        report.add("Agentverse", MOCK, "no AGENTVERSE_API_KEY; agents run locally and can be linked by hand")
        return

    from uagents_core.config import AgentverseConfig
    from uagents_core.identity import Identity

    config = AgentverseConfig()
    key = os.environ["AGENTVERSE_API_KEY"]

    # The challenge route needs both a real agent address and a valid key, so one call
    # checks reachability and the key at once. Nothing is registered by asking.
    seeds = {"Fraud Desk": "FRAUD_DESK_AGENT_SEED", "Merchant Scanner": "MERCHANT_SCANNER_AGENT_SEED"}
    probe_seed = next((os.environ[v] for v in seeds.values() if present(v)), None)
    if probe_seed is None:
        report.add("Agentverse", MOCK, "no agent seed set, so there is no address to check the key against")
        return

    address = Identity.from_seed(probe_seed, 0).address
    try:
        response = httpx.get(
            f"{config.identity_api}/{address}/challenge",
            headers=headers({"Authorization": f"Bearer {key}"}),
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        report.add("Agentverse", FAIL, f"{config.base_url} unreachable: {type(exc).__name__}")
        return

    if response.status_code == 200:
        report.add("Agentverse", OK, f"{config.base_url} reachable and the API key is accepted")
    elif response.status_code in (401, 403):
        report.add("Agentverse", FAIL, f"HTTP {response.status_code}: the API key was rejected")
    else:
        report.add("Agentverse", FAIL, f"HTTP {response.status_code} from {config.identity_api}")

    for label, var in seeds.items():
        if present(var):
            report.add(f"  {label} identity", OK, Identity.from_seed(os.environ[var], 0).address)
        else:
            report.add(f"  {label} identity", MOCK, f"{var} is not set")

    report.add("Apify", OK if present("APIFY_TOKEN") else MOCK,
               "token present (page scans and threat intel can scrape)" if present("APIFY_TOKEN")
               else "no APIFY_TOKEN; page scans fall back to fixtures")


# --- main ----------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--no-llm-calls",
        action="store_true",
        help="skip the one-call-per-model check, which costs Bitdeer credits",
    )
    args = parser.parse_args()

    print("M0 integration probes")
    print("=" * 60)
    report = Report()
    probe_bitdeer(report, llm_calls=not args.no_llm_calls)
    probe_x402(report)
    probe_crs(report)
    probe_agentverse(report)

    counts = {status: sum(1 for _, s, _ in report.rows if s == status) for status in (OK, MOCK, FAIL)}
    print("\n" + "=" * 60)
    print(f"{counts[OK]} OK, {counts[MOCK]} MOCK, {counts[FAIL]} FAIL")
    print(f"Evidence written to {EVIDENCE.relative_to(REPO_ROOT)}/")
    if report.failed:
        print("\nSomething that should work does not. See the FAIL lines above.")
        return 1
    print("\nEvery integration is either live or deliberately mocked; the system runs either way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
