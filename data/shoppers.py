"""Scripted legitimate shoppers (R2, spec 3.1 and M1).

A `Population` is a fixed set of simulated humans (principals) and their agents, built from a seed:
each principal has an identity profile (mostly verified, some thin-file) and a mandate. Every
round, `generate_legit_traffic` turns the population into deterministic `Transaction`s with
their `GroundTruth`. The same (seed, round, n) always gives identical traffic.

Personas include "weird but legit" behavior, so the defense can't learn that unusual means fraud:
- normal: one or two purchases in waking hours, within a few categories
- bulk: large quantities of supplies in one payment, often over $100
- subscription_burst: several renewals within a couple of minutes, around midnight
- travel_new_device: a new device (agent id) per trip, odd hours, travel purchases
- multi_user_agent: one procurement agent buying on behalf of many principals
"""

import hashlib
import json
import random
from dataclasses import dataclass, field
from enum import StrEnum

from contracts.schemas import GroundTruth, IdentityStatus, Mandate, Principal, Product, TrafficSource, Transaction
from data.catalog import products_by_category

ROUND_SECONDS = 86_400  # one simulated day per round
WAVES = 3  # each round's traffic runs in 3 equal time slices (spec 3.9, point 7)


class Persona(StrEnum):
    NORMAL = "normal"
    BULK = "bulk"
    SUBSCRIPTION_BURST = "subscription_burst"
    TRAVEL_NEW_DEVICE = "travel_new_device"
    MULTI_USER_AGENT = "multi_user_agent"


# Share of legitimate transactions per persona. With n=1,000 every persona gets at least 100.
PERSONA_SHARE: dict[Persona, float] = {
    Persona.NORMAL: 0.50,
    Persona.BULK: 0.10,
    Persona.SUBSCRIPTION_BURST: 0.15,
    Persona.TRAVEL_NEW_DEVICE: 0.10,
    Persona.MULTI_USER_AGENT: 0.15,
}

# Principals per persona in a population (multi-user agents each serve a group of principals).
PERSONA_PRINCIPALS: dict[Persona, int] = {
    Persona.NORMAL: 160,
    Persona.BULK: 25,
    Persona.SUBSCRIPTION_BURST: 40,
    Persona.TRAVEL_NEW_DEVICE: 30,
    Persona.MULTI_USER_AGENT: 60,
}
MULTI_USER_GROUP = 12  # principals per shared procurement agent

THIN_FILE_RATE: dict[Persona, float] = {
    Persona.NORMAL: 0.10,
    Persona.BULK: 0.08,
    Persona.SUBSCRIPTION_BURST: 0.10,
    Persona.TRAVEL_NEW_DEVICE: 0.25,
    Persona.MULTI_USER_AGENT: 0.20,
}

# Legit agents occasionally go a little over cap; the human then approves the step-up.
LEGIT_OVER_CAP_RATE = 0.01

EVERYDAY = ["electronics", "phone_accessories", "kitchen", "groceries", "books", "clothing", "beauty",
            "toys", "sports", "pet_supplies", "health", "digital_media"]
BULK_CATEGORIES = ["office_supplies", "groceries", "pet_supplies", "home_office", "kitchen"]
SUBSCRIPTION_CATEGORIES = ["software", "cloud_compute", "digital_media"]
PROCUREMENT_CATEGORIES = ["office_supplies", "home_office", "software", "electronics", "phone_accessories"]


@dataclass(frozen=True)
class Shopper:
    principal: Principal
    persona: Persona
    agent_id: str  # the principal's usual agent (a shared one for multi-user agents)
    mandate: Mandate
    active_hours: tuple[int, int]  # local hours [start, end) when this shopper usually buys


@dataclass
class LegitTx:
    tx: Transaction
    truth: GroundTruth
    persona: Persona


@dataclass
class Population:
    seed: int
    id_prefix: str = ""
    shoppers: list[Shopper] = field(default_factory=list)

    def __post_init__(self):
        if not self.shoppers:
            self.shoppers = _build_shoppers(self.seed, self.id_prefix)
        self.by_principal = {s.principal.principal_id: s for s in self.shoppers}
        self.by_persona: dict[Persona, list[Shopper]] = {}
        for s in self.shoppers:
            self.by_persona.setdefault(s.persona, []).append(s)

    @property
    def principals(self) -> list[Principal]:
        return [s.principal for s in self.shoppers]

    @property
    def mandates(self) -> list[Mandate]:
        return [s.mandate for s in self.shoppers]

    def identity(self, principal_id: str) -> IdentityStatus | None:
        shopper = self.by_principal.get(principal_id)
        return shopper.principal.identity if shopper else None


def _mandate(rng: random.Random, mid: str, pid: str, persona: Persona, categories: list[str]) -> Mandate:
    per_payment = {
        Persona.NORMAL: rng.choice([5_000, 10_000, 15_000, 25_000]),
        Persona.BULK: rng.choice([60_000, 100_000, 150_000]),
        Persona.SUBSCRIPTION_BURST: rng.choice([5_000, 10_000, 20_000]),
        Persona.TRAVEL_NEW_DEVICE: rng.choice([40_000, 60_000, 80_000]),
        Persona.MULTI_USER_AGENT: rng.choice([20_000, 30_000, 50_000]),
    }[persona]
    words = ", ".join(c.replace("_", " ") for c in categories[:3])
    return Mandate(
        mandate_id=mid, principal_id=pid, per_payment_cap_cents=per_payment,
        daily_cap_cents=per_payment * rng.choice([3, 4, 5]), allowed_categories=categories,
        request_text=f"buy {words} as needed, under ${per_payment // 100} per payment",
    )


def _build_shoppers(seed: int, prefix: str) -> list[Shopper]:
    rng = random.Random(f"population:{seed}")
    shoppers: list[Shopper] = []
    index = 0
    for persona, count in PERSONA_PRINCIPALS.items():
        for k in range(count):
            index += 1
            pid = f"{prefix}p-{index:04d}"
            identity = IdentityStatus.THIN_FILE if rng.random() < THIN_FILE_RATE[persona] else IdentityStatus.VERIFIED
            if persona == Persona.NORMAL:
                categories = rng.sample(EVERYDAY, rng.randint(3, 5))
                hours = (rng.randint(7, 11), rng.randint(19, 24))
            elif persona == Persona.BULK:
                categories = rng.sample(BULK_CATEGORIES, 3)
                hours = (8, 18)
            elif persona == Persona.SUBSCRIPTION_BURST:
                categories = list(SUBSCRIPTION_CATEGORIES)
                hours = (0, 2)  # renewals cluster around midnight
            elif persona == Persona.TRAVEL_NEW_DEVICE:
                categories = ["travel", *rng.sample(EVERYDAY, 2)]
                hours = (0, 24)  # time zones: any hour
            else:
                categories = list(PROCUREMENT_CATEGORIES)
                hours = (8, 20)
            if persona == Persona.MULTI_USER_AGENT:
                agent = f"{prefix}agent-procure-{k // MULTI_USER_GROUP + 1:02d}"
            else:
                agent = f"{prefix}agent-{index:04d}"
            shoppers.append(Shopper(
                principal=Principal(principal_id=pid, identity=identity), persona=persona, agent_id=agent,
                mandate=_mandate(rng, f"{prefix}md-{index:04d}", pid, persona, categories), active_hours=hours,
            ))
    return shoppers


def _quotas(n: int) -> dict[Persona, int]:
    raw = {p: n * share for p, share in PERSONA_SHARE.items()}
    quotas = {p: int(v) for p, v in raw.items()}
    for p in sorted(raw, key=lambda p: raw[p] - quotas[p], reverse=True)[: n - sum(quotas.values())]:
        quotas[p] += 1
    return quotas


def _pick_product(rng: random.Random, categories: list[str], max_cents: int) -> Product:
    by_cat = products_by_category()
    options = [p for c in categories for p in by_cat.get(c, []) if p.price_cents <= max_cents]
    if not options:
        options = sorted((p for c in categories for p in by_cat.get(c, [])), key=lambda p: p.price_cents)[:3]
    return rng.choice(options)


def _session(rng: random.Random, shopper: Shopper, pop: Population, round: int) -> list[dict]:
    """One shopping session as a list of partial transactions (no ids yet)."""
    start, end = shopper.active_hours
    hour = rng.uniform(start, end) % 24
    t0 = hour * 3600.0
    m = shopper.mandate
    out: list[dict] = []

    def buy(t: float, product: Product, qty: int, agent: str, s: Shopper) -> None:
        amount = product.price_cents * qty
        if rng.random() < LEGIT_OVER_CAP_RATE and s.persona in (Persona.BULK, Persona.TRAVEL_NEW_DEVICE):
            qty += 1  # a slightly bigger order than authorized; the human approves on step-up
            amount = product.price_cents * qty
        over_cap = amount > s.mandate.per_payment_cap_cents
        out.append(dict(ts=t, agent_id=agent, principal_id=s.principal.principal_id, merchant_id=product.merchant_id,
                        product_id=product.product_id, category=product.category, amount_cents=amount,
                        listed_price_cents=amount, mandate=s.mandate, breaches=over_cap))

    if shopper.persona == Persona.NORMAL:
        for i in range(1 if rng.random() < 0.75 else 2):
            buy(t0 + i * rng.uniform(60, 900), _pick_product(rng, m.allowed_categories, m.per_payment_cap_cents),
                1, shopper.agent_id, shopper)
    elif shopper.persona == Persona.BULK:
        product = _pick_product(rng, m.allowed_categories, m.per_payment_cap_cents // 5)
        qty = max(5, min(40, m.per_payment_cap_cents // max(1, product.price_cents) // rng.choice([2, 3, 4])))
        buy(t0, product, qty, shopper.agent_id, shopper)
    elif shopper.persona == Persona.SUBSCRIPTION_BURST:
        t = t0
        for _ in range(rng.randint(4, 10)):
            buy(t, _pick_product(rng, m.allowed_categories, m.per_payment_cap_cents), 1, shopper.agent_id, shopper)
            t += rng.uniform(2, 20)
    elif shopper.persona == Persona.TRAVEL_NEW_DEVICE:
        device = f"{shopper.agent_id}-trip{round}-{rng.randint(1, 9)}"  # a new device for each trip
        t = t0
        for i in range(rng.randint(1, 3)):
            cats = ["travel"] if i == 0 else m.allowed_categories
            buy(t, _pick_product(rng, cats, m.per_payment_cap_cents), 1, device, shopper)
            t += rng.uniform(300, 3600)
    else:
        group = [s for s in pop.by_persona[Persona.MULTI_USER_AGENT] if s.agent_id == shopper.agent_id]
        t = t0
        for _ in range(rng.randint(1, 3)):
            s = rng.choice(group)
            product = _pick_product(rng, s.mandate.allowed_categories, s.mandate.per_payment_cap_cents // 2)
            buy(t, product, rng.choice([1, 1, 2, 3]), s.agent_id, s)
            t += rng.uniform(30, 600)
    return out


def generate_legit_traffic(run_id: str, round: int, n: int, seed: int,
                           population: Population | None = None) -> list[LegitTx]:
    """`n` legitimate transactions for one round, sorted by time, split into WAVES by time."""
    pop = population or Population(seed)
    rng = random.Random(f"legit:{seed}:{round}")
    rows: list[tuple[dict, Persona]] = []
    for persona, quota in _quotas(n).items():
        made: list[dict] = []
        while len(made) < quota:
            made.extend(_session(rng, rng.choice(pop.by_persona[persona]), pop, round))
        rows.extend((r, persona) for r in made[:quota])
    rows.sort(key=lambda rp: (min(rp[0]["ts"], ROUND_SECONDS - 1), rp[0]["agent_id"]))

    out: list[LegitTx] = []
    base = (round - 1) * ROUND_SECONDS
    for i, (r, persona) in enumerate(rows):
        offset = min(r["ts"], ROUND_SECONDS - 1)
        tx = Transaction(
            tx_id=f"{run_id}-r{round}-lg{i:04d}", run_id=run_id, round=round,
            wave=int(offset // (ROUND_SECONDS / WAVES)), ts=round_to_ms(base + offset), agent_id=r["agent_id"],
            principal_id=r["principal_id"], merchant_id=r["merchant_id"], product_id=r["product_id"],
            category=r["category"], amount_cents=r["amount_cents"], listed_price_cents=r["listed_price_cents"],
            mandate=r["mandate"], source=TrafficSource.LEGIT,
        )
        out.append(LegitTx(tx=tx, truth=GroundTruth(tx_id=tx.tx_id, is_fraud=False, breaches_mandate=r["breaches"]),
                           persona=persona))
    return out


def round_to_ms(t: float) -> float:
    return round(t, 3)


def traffic_hash(txs: list[Transaction]) -> str:
    blob = json.dumps([t.model_dump(mode="json") for t in txs], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()
