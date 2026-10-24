"""Engine tests run the arena with a smaller number-tuning budget than precompute's 200 tries
(spec 3.9 point 5), so a 10-round run takes seconds; tests that check tuning itself set their own."""

import pytest

from engine.arena import sandbox


@pytest.fixture(autouse=True, scope="session")
def small_tuning_budget():
    full, sandbox.TUNING_TRIES = sandbox.TUNING_TRIES, 30
    yield
    sandbox.TUNING_TRIES = full
