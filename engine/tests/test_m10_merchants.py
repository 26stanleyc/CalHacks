"""M10: malicious merchants, page-reading shoppers and merchant-risk features.

The four things M10 has to be true for:

1. a poisoned page scores above the detector's threshold and a clean one below it;
2. with no defense, a poisoned page talks at least 30% of the shopper sessions on it into a
   purchase that breaks their owner's spending rules;
3. with the defense on, at least 80% of those payments are blocked or step-up checked;
4. honest shoppers at the same poisoned stores are *not* flagged, so (3) cannot be passed by
   refusing everything at a store with a bad page.
"""

import pytest

from contracts.interfaces import PageScanner
from contracts.schemas import AttackStyle, Side
from engine.arena.merchant import HIJACK_SHARE, MaliciousMerchantAttacker, storefronts_for
from engine.engine import FraudEngine
from engine.features import PageRisk
from engine.page_hijack import BREACH_TARGET, CAUGHT_TARGET, run_arm
from engine.sim.marketplace import MarketplaceWorld
from engine.sim.pages import PAGE_INJECTIONS, WorldPageScanner, product_page
from integrations.page_scanner import INJECTION_THRESHOLD, InjectionDetector


# --- 1. the pages ------------------------------------------------------------------------------


def test_a_poisoned_page_scores_above_the_threshold_and_a_clean_one_below():
    detector = InjectionDetector()
    clean = product_page("USB-C Charger (20W)", 2_499, merchant="VoltMart")
    poisoned = product_page("USB-C Charger (20W)", 2_499, merchant="MegaDeals", injections=PAGE_INJECTIONS)
    clean_score, _ = detector.score(clean)
    bad_score, findings = detector.score(poisoned)
    assert clean_score < INJECTION_THRESHOLD <= bad_score
    assert bad_score - clean_score > 0.5  # a wide margin, not a threshold squeak
    assert findings


@pytest.mark.parametrize("injection", PAGE_INJECTIONS)
def test_every_hidden_payload_is_caught_on_its_own(injection):
    """Each payload has to work alone: a store only needs one of them."""
    score, _ = InjectionDetector().score(product_page("Charger", 2_499, merchant="MegaDeals",
                                                      injections=(injection,)))
    assert score >= INJECTION_THRESHOLD


def test_the_visible_part_of_a_poisoned_page_is_identical_to_a_clean_one():
    """The hidden text is the only difference, which is why a human browsing never sees it."""
    clean = product_page("Charger", 2_499, merchant="MegaDeals")
    poisoned = product_page("Charger", 2_499, merchant="MegaDeals", injections=PAGE_INJECTIONS)
    clean_lines = [line.strip() for line in clean.splitlines() if line.strip()]
    bad_lines = [line.strip() for line in poisoned.splitlines() if line.strip()]
    assert clean_lines == [line for line in bad_lines if line not in PAGE_INJECTIONS]
    assert [line for line in bad_lines if line in PAGE_INJECTIONS] == list(PAGE_INJECTIONS)


def test_the_world_scanner_conforms_and_handles_an_unknown_url():
    world = MarketplaceWorld(seed=42)
    scanner = WorldPageScanner(world.pages)
    assert isinstance(scanner, PageScanner)
    url = world.page_urls()[0]
    scan = scanner.scan(url)
    assert scan.url == url and scan.injection_score == 0.0 and scan.merchant_id
    unknown = scanner.scan("https://nobody.example/nothing")  # never raises: it is on a decision path
    assert unknown.injection_score == 0.0


def test_simulated_pages_never_reach_the_apify_fallback():
    """The storefronts are not real URLs. Handing them to Apify would be hundreds of failed scrapes
    a round, so the world's own pages must be read locally and only real URLs fall through."""
    reached = []

    class Fallback:
        def scan(self, url):
            reached.append(url)
            return InjectionDetector().scan_text(url, "fetched from the network")

    world = MarketplaceWorld(seed=42)
    scanner = WorldPageScanner(world.pages, fallback=Fallback())
    for url in world.page_urls()[:20]:
        scanner.scan(url)
    assert reached == []
    scanner.scan("https://a-real-store.example/p/1")
    assert reached == ["https://a-real-store.example/p/1"]


def test_the_production_engine_reads_simulated_pages_locally(monkeypatch):
    """engine.entrypoints.build_engine must layer the scanners the same way."""
    monkeypatch.setenv("ENGINE_STORE", "memory")  # no Postgres, and no leak into other tests
    from engine.entrypoints import build_engine

    engine = build_engine()
    try:
        assert isinstance(engine.scanner, WorldPageScanner)
        assert engine.scanner.fallback is not None  # real URLs still have somewhere to go
        assert engine.scan_page(engine.world.page_urls()[0]).injection_score == 0.0
    finally:
        engine.stop()


# --- the lineage -------------------------------------------------------------------------------


def test_the_lineage_stays_on_a_buyer_side_shelf_through_retuning():
    """bench/metrics.py splits losses by `playbook.shelf.side`, so re-tuning must not move it."""
    world = MarketplaceWorld(seed=42)
    attacker = MaliciousMerchantAttacker(_brain(), world.products, world.identities, world.round_seconds,
                                         world.victims(), WorldPageScanner(world.pages))
    assert attacker.playbook.shelf.side == Side.BUYER
    assert attacker.shelf.side == Side.BUYER and attacker.shelf.style == AttackStyle.HIJACK_INJECTION
    nudged = attacker._challenge(round=1, wave=0)
    assert nudged.shelf.side == Side.BUYER
    rebuilt = attacker._playbook("something else", dict(attacker.playbook.params), {}, list(PAGE_INJECTIONS))
    assert rebuilt.shelf.side == Side.BUYER


def test_it_buys_no_identities():
    """It spends its victims' money through their own accounts, so identities cost it nothing."""
    world = MarketplaceWorld(seed=42)
    attacker = MaliciousMerchantAttacker(_brain(), world.products, world.identities, world.round_seconds,
                                         world.victims(), WorldPageScanner(world.pages))
    assert attacker._identity_cost({"pr-whoever"}) == 0


def test_a_clean_page_hijacks_nobody():
    """The hijack probability is driven by the page's score, so a store with nothing hidden in its
    page captures no sessions at all."""
    world = MarketplaceWorld(seed=42)
    attacker = MaliciousMerchantAttacker(_brain(), world.products, world.identities, world.round_seconds,
                                         world.victims(), _CleanScanner())
    txs = attacker.generate("r", 1, 0, 12, 42)
    assert txs and not any(attacker.truth(tx).is_fraud for tx in txs)
    assert not any(s.hijacked for s in attacker.sessions)


def test_an_honest_session_is_labeled_honest_and_stays_inside_the_mandate():
    world = MarketplaceWorld(seed=42)
    attacker = MaliciousMerchantAttacker(_brain(), world.products, world.identities, world.round_seconds,
                                         world.victims(), WorldPageScanner(world.pages))
    attacker.pages()  # publish the poisoned pages the scanner will read
    for url, (html, merchant) in attacker.pages().items():
        world.pages.put(url, html, merchant)
    txs = attacker.generate("r", 1, 0, 20, 42)
    by_id = {tx.tx_id: tx for tx in txs}
    honest = [s for s in attacker.sessions if not s.hijacked]
    assert honest, "some sessions should resist the page"
    for session in honest:
        tx = by_id[session.tx_id]
        assert not attacker.truth(tx).is_fraud and tx.attacker_id is None
        assert tx.amount_cents <= tx.mandate.per_payment_cap_cents
        assert not tx.mandate.allowed_categories or tx.category in tx.mandate.allowed_categories
        assert tx.listed_price_cents == tx.amount_cents  # an honest sale at the advertised price


def test_a_hijacked_payment_hides_its_listed_price():
    """The store omits the figure that would expose the overcharge, so only the page's own price
    (m_page_price_ratio) can catch it."""
    world = MarketplaceWorld(seed=42)
    attacker = MaliciousMerchantAttacker(_brain(), world.products, world.identities, world.round_seconds,
                                         world.victims(), WorldPageScanner(world.pages))
    for url, (html, merchant) in attacker.pages().items():
        world.pages.put(url, html, merchant)
    txs = attacker.generate("r", 1, 0, 20, 42)
    hijacked = [tx for tx in txs if attacker.truth(tx).is_fraud]
    assert hijacked
    assert all(tx.listed_price_cents is None for tx in hijacked)


def test_storefronts_are_stable_for_the_run():
    first = storefronts_for("a", 3, "electronics")
    assert [f.merchant_id for f in first] == [f.merchant_id for f in storefronts_for("a", 3, "electronics")]
    assert len({f.url for f in first}) == 3


def test_the_hijack_share_is_a_share_not_a_certainty():
    """Not every shopper falls for it; the measurement would be meaningless if they all did."""
    assert 0.0 < HIJACK_SHARE < 1.0


# --- the merchant-risk features ----------------------------------------------------------------


def test_merchant_risk_features_are_absent_until_a_page_is_scanned():
    """Missing, not zero: a rule must not fire for a store nobody has looked at."""
    world = MarketplaceWorld(seed=42)
    engine = FraudEngine(seed=42, world=world, n_legit=20, fraud_per_persona=4, arena=False,
                         threat_intel=False, run_id="m10-feat", allowlist={"t"})
    service = engine.pipeline.features
    tx = next(tx for tx, _ in world.legit_traffic("m10-feat", 1, 5, 0.0, world.round_seconds))
    assert "m_page_injection" not in service.compute(tx, commit=False).values
    engine._prime_pages()
    values = service.compute(tx, commit=False).values
    assert values["m_page_injection"] == 0.0  # an honest store's page
    # The price ratio is a merchant-risk feature and lives only where merchant risk is. At an honest
    # store it would be a basket size, not an overcharge: see CONTRACT_NOTES M10-f.
    assert "m_page_price_ratio" not in values


def test_the_price_ratio_appears_at_a_flagged_store():
    world = MarketplaceWorld(seed=42)
    engine = FraudEngine(seed=42, world=world, n_legit=20, fraud_per_persona=6, arena=False,
                         threat_intel=False, run_id="m10-ratio", allowlist={"t"})
    engine.run_round(requested_by="t")
    poisoned = {f.merchant_id for f in next(a for a in engine.attackers if "malicious" in a.attacker_id).storefronts}
    at_bad_store = [f.values for tx, f in zip(engine.store.transactions, engine.store.features)
                    if tx.merchant_id in poisoned]
    assert at_bad_store
    assert all(v["m_page_injection"] >= INJECTION_THRESHOLD for v in at_bad_store)
    assert all("m_page_price_ratio" in v for v in at_bad_store)


def test_a_poisoned_store_raises_merchant_risk_for_the_payments_made_there():
    world = MarketplaceWorld(seed=42)
    engine = FraudEngine(seed=42, world=world, n_legit=20, fraud_per_persona=6, arena=False,
                         threat_intel=False, run_id="m10-risk", allowlist={"t"})
    engine.run_round(requested_by="t")
    attacker = next(a for a in engine.attackers if "malicious" in a.attacker_id)
    poisoned = {f.merchant_id for f in attacker.storefronts}
    scored = [(tx.merchant_id, f.values.get("m_page_injection"))
              for tx, f in zip(engine.store.transactions, engine.store.features)]
    bad = [s for m, s in scored if m in poisoned and s is not None]
    good = [s for m, s in scored if m not in poisoned and s is not None]
    assert bad and max(bad) >= INJECTION_THRESHOLD
    assert good and max(good) < INJECTION_THRESHOLD


def test_page_injection_is_not_treated_as_evidence_about_the_payer():
    """Every shopper at a store reads the same page, so a learned rule may not rest on it alone."""
    world = MarketplaceWorld(seed=42)
    catalog = FraudEngine(seed=42, world=world, n_legit=10, fraud_per_persona=2, arena=False,
                          threat_intel=False, run_id="m10-payer", allowlist={"t"}).pipeline.catalog
    assert catalog.about_payer("m_page_injection") is False
    assert catalog.about_payer("m_page_price_ratio") is True


def test_page_risk_never_fetches_on_the_decision_path():
    calls = []

    class Counting:
        def scan(self, url):
            calls.append(url)
            return InjectionDetector().scan_text(url, "")

    world = MarketplaceWorld(seed=42)
    risk = PageRisk(Counting(), world.page_url_for)
    tx = next(tx for tx, _ in world.legit_traffic("r", 1, 3, 0.0, world.round_seconds))
    assert risk.of(tx) is None and calls == []  # nothing primed: no scan, no fetch
    risk.prime([world.page_url_for(tx)])
    assert len(calls) == 1 and risk.of(tx) is not None
    risk.of(tx), risk.of(tx)
    assert len(calls) == 1  # still one: later payments read the cache


# --- 2, 3 and 4: the measured targets ----------------------------------------------------------


@pytest.fixture(scope="module")
def arms():
    return {name: run_arm(name, seed=42, rounds=2, n_legit=200, fraud_per_persona=20)
            for name in ("off", "on")}


def test_with_no_defense_poisoned_pages_push_shoppers_past_their_spending_rules(arms):
    off = arms["off"]
    assert off.sessions >= 20
    assert off.breach_share >= BREACH_TARGET, f"only {off.breach_share:.0%} of sessions breached"
    assert off.settled_breaches == off.breaching  # with nothing in the way, every one of them pays
    assert off.buyer_loss_cents > 0


def test_with_the_defense_on_those_payments_are_blocked_or_stepped_up(arms):
    on = arms["on"]
    assert on.breaching > 0
    assert on.caught_share >= CAUGHT_TARGET, f"only {on.caught_share:.0%} caught"
    assert on.buyer_loss_cents < arms["off"].buyer_loss_cents


def test_the_defense_does_not_pass_by_refusing_everything_at_a_poisoned_store(arms):
    on = arms["on"]
    assert on.honest_sessions > 0
    assert on.honest_flagged == 0, "honest shoppers at a poisoned store were flagged"
    assert on.false_positive_rate <= 0.02  # the project's false-positive cap


def test_losses_are_split_by_side(arms):
    """The dashboard's "protects both sides" line needs both numbers to be real."""
    on, off = arms["on"], arms["off"]
    assert off.buyer_loss_cents > 0 and off.merchant_loss_cents > 0
    assert on.buyer_loss_cents < off.buyer_loss_cents


# --- helpers -----------------------------------------------------------------------------------


def _brain():
    from engine.brains import Brain

    return Brain()


class _CleanScanner:
    """A scanner that says every page is clean, to show the hijack follows the score."""

    def scan(self, url: str):
        return InjectionDetector().scan_text(url, "An ordinary product page. Price: $24.99")
