"""Checks every real implementation must pass before merging (spec 8.4, rule 4).

Use them from your own tests, e.g. in bench/tests: `check_voter(XGBoostVoter())`.
"""

from contracts.entrypoints import Engine, NotAllowed
from contracts.interfaces import Attacker, PageScanner, Settlement, ThreatIntelSource, Voter
from contracts.schemas import (
    Action,
    AttackerSource,
    AttackFeedback,
    Decision,
    FeatureVector,
    Mandate,
    PageScan,
    RoundMetrics,
    TrafficSource,
    Transaction,
)


def sample_tx(tx_id: str = "tx-sample", amount_cents: int = 2_500, mandate: Mandate | None = None) -> Transaction:
    return Transaction(tx_id=tx_id, run_id="conformance", round=1, ts=0.0, agent_id="agent-1",
                       principal_id="p-1", merchant_id="m-1", amount_cents=amount_cents,
                       mandate=mandate, source=TrafficSource.LEGIT)


def check_voter(voter: Voter) -> None:
    assert isinstance(voter, Voter)
    tx = sample_tx()
    vote = voter.score(tx, FeatureVector(tx_id=tx.tx_id, values={"amount_cents": 2_500.0}))
    assert vote.tx_id == tx.tx_id and vote.voter == voter.name
    voter.retrain([(FeatureVector(tx_id="a", values={"amount_cents": 1.0}), False),
                   (FeatureVector(tx_id="b", values={"amount_cents": 99_999.0}), True)])


def check_attacker(attacker: Attacker) -> None:
    assert isinstance(attacker, Attacker)
    record = attacker.record()
    first = attacker.generate("conformance", round=1, wave=0, n=5, seed=42)
    again = attacker.generate("conformance", round=1, wave=0, n=5, seed=42)
    assert 0 < len(first) <= 5
    assert [t.model_dump() for t in first] == [t.model_dump() for t in again], "generate must be deterministic"
    for tx in first:
        assert tx.source == TrafficSource.ATTACKER and tx.attacker_id == record.attacker_id
    for name, value in record.playbook.params.items():
        lo, hi = record.playbook.param_bounds.get(name, (value, value))
        assert lo <= value <= hi, f"param {name} outside its realism bounds"
    objected = None if record.source != AttackerSource.SPAWNED else []
    attacker.observe([AttackFeedback(tx_id=first[0].tx_id, outcome=Action.BLOCK, objected_by=objected)])


def check_page_scanner(scanner: PageScanner) -> None:
    assert isinstance(scanner, PageScanner)
    assert isinstance(scanner.scan("https://example.com/product"), PageScan)


def check_threat_intel(source: ThreatIntelSource) -> None:
    assert isinstance(source, ThreatIntelSource)
    for report in source.fetch_new():
        assert report.source_url and report.text


def check_settlement(settlement: Settlement) -> None:
    assert isinstance(settlement, Settlement)
    tx = sample_tx()
    blocked = Decision(tx_id=tx.tx_id, action=Action.BLOCK, weighted_score=0.9, votes=[])
    assert settlement.settle(tx, blocked).settled is False, "blocked payments must never settle"


def check_engine(engine: Engine, team_sender: str) -> None:
    assert isinstance(engine, Engine)
    mandate = Mandate(mandate_id="md-1", principal_id="p-1", per_payment_cap_cents=3_000, daily_cap_cents=10_000)
    tx = sample_tx("tx-over-cap", amount_cents=4_900, mandate=mandate).model_copy(update={"source": TrafficSource.ASI_ONE})
    decision = engine.check_payment(tx, mandate)
    assert decision.tx_id == tx.tx_id and decision.action != Action.ALLOW, "an over-cap payment must not be allowed"
    assert engine.explain(tx.tx_id).tx_id == tx.tx_id
    assert isinstance(engine.scan_page("https://example.com/product"), PageScan)
    assert isinstance(engine.get_metrics(), RoundMetrics)
    try:
        engine.run_round(requested_by="not-on-the-allowlist")
        raise AssertionError("run_round must refuse senders outside the allowlist")
    except NotAllowed:
        pass
    assert isinstance(engine.run_round(requested_by=team_sender), RoundMetrics)
    assert engine.set_freeze(True, requested_by=team_sender) is True
    assert engine.set_freeze(False, requested_by=team_sender) is False
    assert isinstance(engine.latest_threat_intel(limit=3), list)
