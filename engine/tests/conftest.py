"""Engine tests run the arena with a smaller number-tuning budget than precompute's 200 tries
(spec 3.9 point 5), so a 10-round run takes seconds; tests that check tuning itself set their own."""

import os

import pytest

from engine.arena import sandbox


@pytest.fixture(autouse=True, scope="session")
def small_tuning_budget():
    full, sandbox.TUNING_TRIES = sandbox.TUNING_TRIES, 30
    yield
    sandbox.TUNING_TRIES = full


@pytest.fixture(autouse=True, scope="session")
def no_live_crs():
    """Tests never log in to the real CRS sandbox, whatever .env says: failed logins lock the account.
    Set rather than removed, so a later load_dotenv() can't bring .env's value back."""
    saved, os.environ["CRS_ESCALATION"] = os.environ.get("CRS_ESCALATION"), "0"
    yield
    if saved is None:
        del os.environ["CRS_ESCALATION"]
    else:
        os.environ["CRS_ESCALATION"] = saved
