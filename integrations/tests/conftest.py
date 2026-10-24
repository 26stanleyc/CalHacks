"""Keep the tests offline and independent of whatever is in `.env`.

`contracts/config.py` and `integrations/settings.py` both call `load_dotenv()` at import,
so a developer's real keys are in `os.environ` by the time a test runs. These fixtures pin
the few variables the tests depend on, so the suite behaves the same on a machine with
every key set and on CI with none.
"""

import pytest

INTEGRATION_ENV = (
    "USE_MOCK_LLM",
    "USE_STUB_ENGINE",
    "ENGINE_SOURCE",
    "ENGINE_HTTP_URL",
    "X402_MODE",
    "X402_MAX_SETTLE_CENTS",
    "TEAM_ALLOWLIST_ADDRESSES",
    "TESTNET_WALLET_PRIVATE_KEY",
    "TESTNET_WALLET_ADDRESS",
    "MERCHANT_WALLET_ADDRESS",
    "BITDEER_API_KEY",
    "BITDEER_BASE_URL",
    "APIFY_TOKEN",
)


@pytest.fixture(autouse=True)
def clean_integration_env(monkeypatch):
    """Clear the integration variables; a test that needs one sets it itself."""
    for name in INTEGRATION_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def team_sender() -> str:
    return "agent1qteammember"


@pytest.fixture
def allowlisted(monkeypatch, team_sender):
    monkeypatch.setenv("TEAM_ALLOWLIST_ADDRESSES", team_sender)
    return team_sender
