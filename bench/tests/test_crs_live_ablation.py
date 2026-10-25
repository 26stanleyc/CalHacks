import pytest

from bench import crs_live_ablation
from engine import crs_ablation
from engine.crs import HttpCrs


@pytest.fixture
def restore_bureau():
    original = crs_ablation.MockCrs
    yield
    crs_ablation.MockCrs = original


def test_live_bureau_needs_credentials(monkeypatch, restore_bureau):
    for key in crs_live_ablation.REQUIRED:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit, match="CRS_USERNAME"):
        crs_live_ablation.use_live_bureau()


def test_live_bureau_replaces_the_mock(monkeypatch, restore_bureau):
    monkeypatch.setenv("CRS_BASE_URL", "https://api-sandbox.stitchcredit.com/api")
    monkeypatch.setenv("CRS_USERNAME", "test")
    monkeypatch.setenv("CRS_PASSWORD", "test")
    clients = crs_live_ablation.use_live_bureau()
    client = crs_ablation.MockCrs({}, set())
    assert isinstance(client, HttpCrs) and not client.mocked
    assert clients == [client]


def test_report_still_names_the_mock_note():
    """The live run relabels the report by replacing this sentence; fail loudly if it moves."""
    result = crs_ablation.Result("off", 42, 0, 0.0, 0.0, 0, 0)
    assert crs_live_ablation.MOCK_NOTE in crs_ablation.report([result], 1)
