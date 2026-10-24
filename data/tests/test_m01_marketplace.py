"""M1 checks (spec 8.2): marketplace and legitimate traffic. Seed 42, 1,000 transactions."""

from collections import Counter

import pytest

from contracts.schemas import IdentityStatus, TrafficSource
from data.catalog import build_catalog, load_catalog
from data.features import build_features
from data.shoppers import Persona, Population, generate_legit_traffic, traffic_hash

pytestmark = pytest.mark.m01

SEED = 42
N = 1_000


@pytest.fixture(scope="module")
def population():
    return Population(SEED)


@pytest.fixture(scope="module")
def traffic(population):
    return generate_legit_traffic("m01", 1, N, SEED, population)


def test_1000_legit_transactions_each_with_a_feature_vector(traffic, population):
    assert len(traffic) == N
    assert all(lt.tx.source == TrafficSource.LEGIT and not lt.truth.is_fraud for lt in traffic)
    features = build_features([lt.tx for lt in traffic], population.identity)
    assert len(features) == N and all(features[lt.tx.tx_id].values for lt in traffic)


def test_every_persona_appears_at_least_50_times(traffic):
    counts = Counter(lt.persona for lt in traffic)
    assert set(counts) == set(Persona)
    assert min(counts.values()) >= 50, counts


def test_catalog_has_200_products_with_prices():
    catalog = load_catalog()
    assert len(catalog) >= 200
    assert all(p.price_cents > 0 and p.category and p.merchant_id for p in catalog)
    assert len({p.product_id for p in catalog}) == len(catalog)
    assert [p.model_dump() for p in catalog] == [p.model_dump() for p in build_catalog()], "catalog.json is stale"


def test_same_seed_gives_identical_traffic(traffic):
    again = generate_legit_traffic("m01", 1, N, SEED)
    assert traffic_hash([lt.tx for lt in traffic]) == traffic_hash([lt.tx for lt in again])
    other = generate_legit_traffic("m01", 1, N, SEED + 1)
    assert traffic_hash([lt.tx for lt in traffic]) != traffic_hash([lt.tx for lt in other])


def test_every_payment_carries_its_principals_mandate(traffic, population):
    for lt in traffic:
        assert lt.tx.mandate is not None
        assert lt.tx.mandate.principal_id == lt.tx.principal_id
        assert lt.tx.mandate == population.by_principal[lt.tx.principal_id].mandate


def test_legit_traffic_mostly_stays_inside_its_mandate(traffic):
    breaches = [lt for lt in traffic if lt.truth.breaches_mandate]
    assert len(breaches) <= 0.02 * N
    for lt in traffic:
        m = lt.tx.mandate
        assert lt.tx.category in m.allowed_categories
        assert lt.truth.breaches_mandate == (lt.tx.amount_cents > m.per_payment_cap_cents)


def test_identity_profiles_mostly_verified_some_thin_file(population):
    counts = Counter(p.identity for p in population.principals)
    assert set(counts) <= {IdentityStatus.VERIFIED, IdentityStatus.THIN_FILE}
    assert counts[IdentityStatus.VERIFIED] >= 0.8 * len(population.principals)
    assert counts[IdentityStatus.THIN_FILE] >= 0.05 * len(population.principals)


def test_weird_but_legit_personas_look_weird(traffic, population):
    features = build_features([lt.tx for lt in traffic], population.identity)

    def values(persona, name):
        return [features[lt.tx.tx_id].values[name] for lt in traffic if lt.persona == persona]

    assert max(values(Persona.SUBSCRIPTION_BURST, "count_tx_per_agent_10m")) >= 4  # renewals in a burst
    assert sum(v >= 10_000 for v in values(Persona.BULK, "amount_cents")) >= 25  # big orders
    assert max(values(Persona.MULTI_USER_AGENT, "distinct_principals_per_agent_24h")) >= 5  # many users, one agent
    assert sum(values(Persona.TRAVEL_NEW_DEVICE, "agent_first_seen")) >= 20  # new devices


def test_ids_are_unique_and_traffic_is_time_ordered(traffic):
    assert len({lt.tx.tx_id for lt in traffic}) == N
    ts = [lt.tx.ts for lt in traffic]
    assert ts == sorted(ts) and 0 <= ts[0] and ts[-1] < 86_400
    assert {lt.tx.wave for lt in traffic} == {0, 1, 2}
