"""M3 checks (spec 8.2): outsider personas and waves, over 3 rounds of the standard 400-payment
size (300 legitimate payments plus 25 from each of the 4 outsider lineages) on Ryan's marketplace."""

from collections import Counter

import pytest

from contracts.schemas import Action, AttackFeedback, Decision, GroundTruth, Lane
from engine.arena import playbooks
from engine.arena.outsiders import PERSONAS, OutsiderAttacker, PlaybookRewrite
from engine.brains import Brain, MockBrains
from engine.engine import FraudEngine, resolve
from engine.sim.marketplace import MarketplaceWorld

pytestmark = pytest.mark.m03

TEAM = "agent1qteam"


@pytest.fixture(scope="module")
def run():
    engine = FraudEngine(world=MarketplaceWorld(seed=42), allowlist={TEAM}, arena=False, hijacker=False)
    seen: list[tuple[str, int, list[AttackFeedback]]] = []
    fingerprints: list[tuple[int, str]] = []
    for attacker in engine.attackers:
        original = attacker.observe

        def spy(feedback, original=original, attacker=attacker):
            seen.append((attacker.attacker_id, attacker._where[1], feedback))
            fingerprints.append((attacker._where[0], engine.pipeline.fingerprint()))
            original(feedback)
        attacker.observe = spy
    metrics = [engine.run_round(requested_by=TEAM) for _ in range(3)]
    return engine, metrics, seen, fingerprints


def test_each_persona_sends_at_least_25_payments_a_round_in_a_400_payment_round(run):
    engine, _, _, _ = run
    per_round = Counter((t.round, t.attacker_id) for t in engine.store.transactions if t.attacker_id)
    assert {a for _, a in per_round} == {f"outsider-{p.name}" for p in PERSONAS}
    assert min(per_round.values()) >= 25
    for round_no in (1, 2, 3):
        assert sum(t.round == round_no for t in engine.store.transactions) == 400


def test_personas_only_see_outcomes(run):
    _, _, seen, _ = run
    assert seen and all(f.objected_by is None and f.settled is not None for _, _, fb in seen for f in fb)
    attacker = OutsiderAttacker(PERSONAS[0], Brain(), [], {}, 86_400)
    with pytest.raises(ValueError):
        attacker.observe([AttackFeedback(tx_id="t", outcome=Action.BLOCK, objected_by=[Lane.TIMING])])


def test_extraction_is_logged_per_persona_per_round(run):
    engine, metrics, _, _ = run
    rows = engine.store.rows["attackers"]
    assert {(r["attacker_id"], r["round"]) for r in rows} == {
        (f"outsider-{p.name}", n) for p in PERSONAS for n in (1, 2, 3)}
    assert all(r["source"] == "outsider" and "extraction_cents" in r["payload"] for r in rows)
    settled_fraud = {s.tx_id for s in engine.store.settlements if s.settled}
    truth = {g.tx_id: g for g in engine.store.ground_truth}
    for round_no, m in zip((1, 2, 3), metrics):
        extracted = sum(r["payload"]["extraction_cents"] for r in rows if r["round"] == round_no)
        assert extracted >= sum(t.amount_cents for t in engine.store.transactions
                                if t.round == round_no and truth[t.tx_id].is_fraud and t.tx_id in settled_fraud)
        assert m.loss_merchant_side_cents == extracted


def test_each_round_has_3_waves_and_the_defense_is_identical_across_them(run):
    engine, _, seen, fingerprints = run
    assert {t.wave for t in engine.store.transactions if t.round == 1} == {0, 1, 2}
    assert Counter(w for _, w, _ in seen) == {0: 12, 1: 12, 2: 12}  # 4 lineages x 3 rounds per wave
    for round_no in (1, 2, 3):
        assert len({fp for r, fp in fingerprints if r == round_no}) == 1


def _bad(log) -> bool:
    return log.summary["attempts"] and log.summary["caught"] > log.summary["attempts"] / 2


def test_a_persona_caught_on_most_of_a_wave_changes_its_playbook_before_the_next(run):
    engine, _, _, _ = run
    pairs = [(a, before, after) for a in engine.attackers for before, after in zip(a.wave_logs, a.wave_logs[1:])]
    bad = [(a, before, after) for a, before, after in pairs if _bad(before)]
    assert bad, "the seed rulebooks should catch most of at least one wave"
    assert all(after.strategy_changed for _, _, after in bad), [(a.attacker_id, after.trigger) for a, _, after in bad]


def test_a_persona_only_rewrites_its_strategy_after_a_bad_wave(run):
    engine, _, _, _ = run
    for attacker in engine.attackers:
        for before, after in zip(attacker.wave_logs, attacker.wave_logs[1:]):
            if not _bad(before):
                assert not after.strategy_changed, (attacker.attacker_id, after.round, after.wave, after.trigger)


def test_playbook_rows_hold_the_playbook_in_effect_during_each_wave(run):
    engine, _, _, _ = run
    waves = engine.store.rows["playbook_waves"]
    assert len(waves) == 4 * 3 * 3
    for persona in PERSONAS:
        first = next(w for w in waves if w["attacker_id"] == f"outsider-{persona.name}")
        assert (first["round"], first["wave"]) == (1, 0) and not first["strategy_changed"]
        assert first["playbook"]["params"] == {k: float(v) for k, v in persona.params.items()}
    played = {(t.attacker_id, t.round, t.wave) for t in engine.store.transactions if t.attacker_id}
    assert {(w["attacker_id"], w["round"], w["wave"]) for w in waves} == played


def test_a_blocked_persona_rewrites_its_playbook(run):
    engine, _, _, _ = run
    for attacker in engine.attackers:
        logs = attacker.wave_logs
        for before, after in zip(logs, logs[1:]):
            if after.strategy_changed:
                assert (after.playbook.strategy, after.playbook.params) != (before.playbook.strategy, before.playbook.params)
    assert any(a.generation > 0 for a in engine.attackers)


class BrokenModel(MockBrains):
    """Writes playbooks that break the realism limits half the time."""

    def __init__(self):
        super().__init__()
        self.n = 0

    def complete(self, messages, *, role, response_schema=None, tools=None, max_tokens=1024):
        response = super().complete(messages, role=role, response_schema=response_schema, tools=tools, max_tokens=max_tokens)
        self.n += 1
        if response_schema is PlaybookRewrite and self.n % 2:
            response.parsed = response.parsed.model_copy(update={"params": {**response.parsed.params, "interval_s": 0.01}})
        return response


def _validity(engine) -> tuple[int, int]:
    last_round = max(r["round"] for r in engine.store.rows["attackers"])
    rows = [r["payload"]["rewrites"] for r in engine.store.rows["attackers"] if r["round"] == last_round]
    attempted = sum(r["attempted"] - r["failed"] for r in rows)  # outages aren't schema answers
    return sum(r["valid"] + r["unchanged"] for r in rows), attempted


def test_invalid_model_playbooks_fall_back_to_the_previous_playbook_and_are_logged():
    engine = FraudEngine(world=MarketplaceWorld(seed=42), allowlist={TEAM}, arena=False, brain=Brain(BrokenModel()), hijacker=False)
    for _ in range(3):
        engine.run_round(requested_by=TEAM)
    rejected = 0
    for attacker in engine.attackers:
        for before, after in zip(attacker.wave_logs, attacker.wave_logs[1:]):
            assert playbooks.validate(after.playbook) == []
            if after.trigger and "invalid rewrite" in after.trigger:
                rejected += 1
                assert after.playbook.strategy == before.playbook.strategy and not after.strategy_changed
    assert rejected
    valid, attempted = _validity(engine)
    assert attempted and valid / attempted < 0.95  # measured, not true by construction


def test_at_least_95_percent_of_model_playbooks_are_valid(run):
    engine, _, _, _ = run
    valid, attempted = _validity(engine)
    assert attempted > 0 and valid / attempted >= 0.95
    for attacker in engine.attackers:
        for log in attacker.wave_logs:
            assert playbooks.validate(log.playbook) == [], log.playbook


def test_the_engine_resolves_each_personas_step_ups_at_its_own_pass_rate():
    engine = FraudEngine(world=MarketplaceWorld(seed=42), allowlist={TEAM}, arena=False, n_legit=20, fraud_per_persona=150,
                         escalation_cap=0, hijacker=False)  # no investigator: every fraud payment really is stepped up
    decide = engine.pipeline.orchestrator.decide

    def always_step_up(tx, votes, features):
        decision = decide(tx, votes, features)
        return decision.model_copy(update={"action": Action.STEP_UP, "vetoed_by": None}) if tx.attacker_id else decision
    engine.pipeline.orchestrator.decide = always_step_up
    for _ in range(2):
        engine.run_round(requested_by=TEAM)
    for attacker in engine.attackers:
        summaries = [log.summary for log in attacker.wave_logs]
        stepped = sum(s["stepped_up"] for s in summaries)
        assert stepped == sum(s["attempts"] for s in summaries) > 100
        settled_share = sum(s["settled"] for s in summaries) / stepped
        rate = attacker.persona.step_up_pass
        tolerance = 3 * (rate * (1 - rate) / stepped) ** 0.5  # three standard deviations of a binomial share
        assert abs(settled_share - rate) <= max(0.05, tolerance), (attacker.attacker_id, settled_share, stepped)


@pytest.mark.parametrize("persona", PERSONAS, ids=lambda p: p.name)
def test_step_up_pass_rates_match_the_configured_rates(persona):
    from contracts.conformance import sample_tx

    passed = 0
    for i in range(1_000):
        tx = sample_tx(f"tx-{persona.name}-{i}")
        decision = Decision(tx_id=tx.tx_id, action=Action.STEP_UP, weighted_score=0.5, votes=[])
        passed += resolve(tx, GroundTruth(tx_id=tx.tx_id, is_fraud=True), decision, persona.step_up_pass, 0.25).settled
    assert abs(passed / 1_000 - persona.step_up_pass) <= 0.05


def test_rounds_are_deterministic_for_a_seed():
    def amounts():
        engine = FraudEngine(world=MarketplaceWorld(seed=7), allowlist={TEAM}, arena=False, hijacker=False)
        engine.run_round(requested_by=TEAM)
        engine.run_round(requested_by=TEAM)
        return [(t.tx_id, t.amount_cents, t.ts) for t in engine.store.transactions]
    assert amounts() == amounts()


@pytest.mark.skipif(__import__("os").environ.get("ENGINE_DB_TEST") != "1",
                    reason="set ENGINE_DB_TEST=1 to run against a throwaway Postgres schema")
def test_outsider_logs_land_in_postgres_and_feed_the_dashboard_drift_query():
    import os
    from pathlib import Path

    import psycopg

    from engine.store import PostgresStore

    schema = "engine_m03_check"
    admin = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    admin.execute(f"drop schema if exists {schema} cascade")
    admin.execute(f"create schema {schema}")
    try:
        admin.execute(f"set search_path to {schema}")
        admin.execute((Path(__file__).resolve().parents[2] / "contracts" / "db.sql").read_text())
        store = PostgresStore(schema=schema)
        engine = FraudEngine(store=store, run_id="test-m03", world=MarketplaceWorld(seed=42), allowlist={TEAM}, arena=False, hijacker=False)
        engine.run_round(requested_by=TEAM)
        assert admin.execute("select count(*) from attackers").fetchone()[0] == 4
        assert admin.execute("select count(*) from playbook_waves").fetchone()[0] == 12
        drift = admin.execute(  # dashboard/src/lib/queries.ts outsiderDrift
            """select w.attacker_id, coalesce(a.name, w.attacker_id) as name, w.round, w.wave,
                      (w.playbook->'params'->>'amount_cents')::float as amount_cents,
                      (w.playbook->'params'->>'interval_s')::float as interval_s,
                      w.playbook->>'strategy' as strategy, w.strategy_changed, w.trigger
                 from playbook_waves w
                 join (select distinct on (attacker_id) attacker_id, payload->>'name' as name
                         from attackers where run_id = %s and source = 'outsider'
                        order by attacker_id, round desc) a using (attacker_id)
                where w.run_id = %s order by w.attacker_id, w.round, w.wave""", ("test-m03", "test-m03")).fetchall()
        assert len(drift) == 12 and all(row[4] and row[5] and row[6] for row in drift)
        store.close()
    finally:
        admin.execute(f"drop schema if exists {schema} cascade")
        admin.close()


def test_validate_rejects_bad_numbers_and_bounds():
    good = OutsiderAttacker(PERSONAS[1], Brain(), [], {}, 86_400).playbook
    assert playbooks.validate(good) == []
    for bad in ({"param_bounds": {"interval_s": (float("nan"), float("inf"))}},
                {"param_bounds": {"interval_s ": (1.0, 10.0)}},
                {"param_bounds": {"interval_s": (900.0, 100.0)}},
                {"params": {**good.params, "n_accounts": 2.5}},
                {"params": {**good.params, "amount_cents": float("inf")}}):
        assert playbooks.validate(good.model_copy(update=bad)), bad


def test_clamp_keeps_whole_numbers_inside_their_bounds():
    out = playbooks.clamp({"n_accounts": 2.4, "n_merchants": 7.6}, {"n_accounts": (2.5, 10.0), "n_merchants": (1.0, 7.5)})
    assert out == {"n_accounts": 3.0, "n_merchants": 7.0}


def test_an_account_keeps_its_identity_and_mandate_all_round(run):
    engine, _, _, _ = run
    features = {f.tx_id: f.values for f in engine.store.features}
    seen: dict[tuple, set] = {}
    for tx in engine.store.transactions:
        if tx.attacker_id:
            key = (tx.round, tx.principal_id)
            seen.setdefault(key, set()).add((features[tx.tx_id]["i_identity_status"], tx.mandate.per_payment_cap_cents,
                                             tx.mandate.daily_cap_cents))
    assert seen and all(len(values) == 1 for values in seen.values())


def test_a_many_accounts_playbook_spreads_over_its_accounts_across_waves():
    from engine.sim.world import World

    world = World(seed=42)
    farmer = next(p for p in PERSONAS if p.name == "account_farmer")
    attacker = OutsiderAttacker(farmer, Brain(), world.products, world.identities, 86_400)
    attacker.challenger = None
    principals = set()
    for wave in range(3):
        txs = attacker.generate("acct", 1, wave, 8, 42)
        principals |= {t.principal_id for t in txs if "c-r" not in t.principal_id}
    assert len(principals) == sum(1 for _ in range(3) for _ in range(8 - int(8 * 0.4)))  # one payment per account


def test_resuming_a_run_picks_the_lineages_back_up():
    from engine.store import MemoryStore

    store = MemoryStore()
    first = FraudEngine(store=store, run_id="resume", world=MarketplaceWorld(seed=42), allowlist={TEAM}, arena=False, hijacker=False)
    for _ in range(2):
        first.run_round(requested_by=TEAM)
    again = FraudEngine(store=store, run_id="resume", world=MarketplaceWorld(seed=42), allowlist={TEAM}, arena=False, hijacker=False)
    assert again.round == 2
    for before, after in zip(first.attackers, again.attackers):
        assert after.playbook == before.playbook and after.generation == before.generation
