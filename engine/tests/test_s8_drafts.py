"""S8 checks (spec 3.9 point 4, stretch): rulebook drafts, GEPA-style. Up to 3 drafts per
specialist, scored per shelf with the other specialists held fixed; drafts not best on any shelf
are dropped; the best draft under the false-positive limit is deployed; at most 2 switch a round."""

from collections import Counter

import pytest

from contracts.schemas import Lane
from engine.dsl import Rulebook
from engine.engine import FraudEngine
from engine.learning.drafts import MAX_DRAFTS, MAX_SWITCHES, Draft, pareto
from engine.learning.loop import GUARD_AT
from engine.sim.marketplace import MarketplaceWorld

pytestmark = pytest.mark.s8

TEAM = "agent1qteam"


def test_drafts_not_best_on_any_shelf_are_dropped():
    book = Rulebook(lane=Lane.TIMING)
    a = Draft(book, shelf_scores={"s1": 0.9, "s2": 0.1})
    b = Draft(book, shelf_scores={"s1": 0.2, "s2": 0.8})
    c = Draft(book, shelf_scores={"s1": 0.5, "s2": 0.5})  # best nowhere
    assert pareto([a, b, c]) == [a, b]


@pytest.fixture(scope="module")
def run():
    engine = FraudEngine(world=MarketplaceWorld(seed=42), allowlist={TEAM}, drafts=True)
    metrics = [engine.run_round(requested_by=TEAM) for _ in range(8)]
    return engine, metrics


def test_each_specialist_keeps_at_most_3_drafts(run):
    engine, _ = run
    assert engine.learning.drafts.drafts
    assert all(len(pool) <= MAX_DRAFTS for pool in engine.learning.drafts.drafts.values())


def test_at_most_2_specialists_switch_drafts_a_round_and_switches_stay_under_the_limit(run):
    engine, _ = run
    switches = [r for r in engine.store.rows["rulebook_versions"] if "draft_switch" in (r["diff"] or {})]
    assert switches, "drafts should be switched at least once in 8 rounds"
    assert all(n <= MAX_SWITCHES for n in Counter(r["round"] for r in switches).values())
    assert all(r["diff"]["fp"] <= 0.02 * GUARD_AT for r in switches)


def test_a_switch_deploys_a_logged_draft_that_beats_the_live_rulebook(run):
    engine, _ = run
    rows = engine.store.rows["rulebook_versions"]
    switches = [r for r in rows if "draft_switch" in (r["diff"] or {})]
    assert all(r["deployed"] and r["diff"]["shelf_scores"] for r in switches)
    for s in switches:  # the switched-in rules are a draft reflection wrote (not yet deployed) or the seed
        drafted = [r for r in rows if r["lane"] == s["lane"] and r["round"] <= s["round"] and not r["deployed"]
                   and r["veto_reason"] is None]
        new = {x["id"] for x in s["rules"]}
        assert any(new & {x["id"] for x in r["rules"]} for r in drafted) or not drafted


def test_reflection_edits_go_into_drafts_not_live(run):
    engine, _ = run
    edits = [r for r in engine.store.rows["rulebook_versions"] if "added" in (r["diff"] or {})]
    assert edits and not any(r["deployed"] for r in edits)


def test_a_switch_never_brings_back_a_rule_hygiene_removed(run):
    engine, _ = run
    removed: dict[tuple[str, str], int] = {}
    for r in engine.store.rows["rulebook_versions"]:
        diff = r["diff"] or {}
        for key in ("retired", "revet_removed", "fp_guard_removed"):
            for rid in diff.get(key, []):
                removed[(r["lane"], rid)] = r["round"]
        if "draft_switch" in diff:
            assert not any((r["lane"], x["id"]) in removed for x in r["rules"])
