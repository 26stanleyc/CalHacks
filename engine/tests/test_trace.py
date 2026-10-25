"""The demo trace: the Bureau reports every message hop, and `engine.trace` prints them."""

import pytest

from contracts.schemas import Decision, FeatureVector, Transaction, Vote
from engine import trace
from engine.bureau import EngineBureau, Hop
from engine.pipeline import DefensePipeline
from engine.smoke import sample_transaction
from engine.tests.test_m00_foundations import free_port


@pytest.fixture
def traced_bureau():
    hops: list[Hop] = []
    bureau = EngineBureau(DefensePipeline(), port=free_port(), seed_prefix="test-trace", on_hop=hops.append)
    bureau.start()
    yield bureau, hops
    bureau.stop()


def test_one_payment_reports_every_hop_in_order(traced_bureau):
    bureau, hops = traced_bureau
    decision = bureau.submit_sync(sample_transaction("test-run"), timeout=5.0)
    specialists = {f"specialist-{lane.value}" for lane in bureau.lanes}

    assert (hops[0].sender, hops[0].receiver, hops[0].message) == ("ingress", "orchestrator", "SubmitTx")
    assert isinstance(hops[0].payload, Transaction)
    asks = [h for h in hops if h.message == "ScoreRequest"]
    assert {h.receiver for h in asks} == specialists and len(asks) == len(specialists)
    assert all(h.sender == "orchestrator" and isinstance(h.payload, FeatureVector) for h in asks)
    replies = [h for h in hops if h.message == "VoteReply"]
    assert {h.sender for h in replies} == specialists and len(replies) == len(specialists)
    assert {h.payload.model_dump_json() for h in replies} == {v.model_dump_json() for v in decision.votes}
    assert all(isinstance(h.payload, Vote) for h in replies)
    assert (hops[-1].sender, hops[-1].receiver, hops[-1].message) == ("orchestrator", "ingress", "DecisionReply")
    assert isinstance(hops[-1].payload, Decision) and hops[-1].payload == decision
    assert len(hops) == 2 + 2 * len(specialists)


def test_trace_shows_the_card_tester_blocked_by_two_specialists(capsys):
    trace.main(["--pace", "0"])
    out = capsys.readouterr().out
    votes = {line.split()[2]: line for line in out.splitlines() if "VoteReply" in line}  # "+0.7 ms <sender> ..."
    assert "BLOCK" in votes["specialist-timing"] and "timing.rapid_fire" in votes["specialist-timing"]
    assert "BLOCK" in votes["specialist-velocity"] and "velocity.small_burst" in votes["specialist-velocity"]
    assert "Decision: BLOCK" in out
    assert "10 messages between 6 agents" in out


def test_trace_shows_an_honest_payment_allowed(capsys):
    trace.main(["--scenario", "legit", "--pace", "0"])
    out = capsys.readouterr().out
    assert "Decision: ALLOW" in out
    assert "BLOCK" not in out
