"""The arena demo's what-if server: judges' changes reach the engine and come back as a replay."""

from fastapi.testclient import TestClient

from engine.whatif import create_app


def test_a_whatif_run_applies_the_judges_changes():
    client = TestClient(create_app())
    assert {"card_tester", "hijacker"} <= {a["name"] for a in client.get("/whatif/options").json()["attackers"]}
    replay = client.post("/whatif", json={
        "rounds": 1, "attackers": {"structurer": {"enabled": False}},
        "custom": [{"style": "many_accounts", "amount": "over_100"}],
        "defense": {"anomaly_voter": False, "self_red_team": False, "threat_intel": False}}).json()
    assert len(replay["rounds"]) == 1
    names = {a["name"] for a in replay["rounds"][0]["attackers"]}
    assert "structurer" not in names and any(n.startswith("judge many accounts") for n in names)
    payments = [p for w in replay["rounds"][0]["waves"] for p in w["payments"]]
    assert payments and {"legit", "attacker"} <= {p["source"] for p in payments}


def test_bad_settings_are_refused():
    client = TestClient(create_app())
    assert client.post("/whatif", json={"rounds": 50}).status_code == 422
    assert client.post("/whatif", json={"defense": {"fp_cap": 0.5}}).status_code == 422
