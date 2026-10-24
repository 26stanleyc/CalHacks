"""Ryan's marketplace (data/, M1) as the engine's world: his catalog and scripted legitimate
shoppers, with the engine's scripted fraud personas buying from the same merchants.

Like engine/entrypoints.py, this is an adapter to another folder; the engine core never imports it.
"""

import random
from dataclasses import dataclass

from contracts.schemas import Mandate
from data.catalog import load_catalog
from data.shoppers import ROUND_SECONDS, Population, generate_legit_traffic
from engine.sim.world import Product, World

# The per-payment caps Ryan's shoppers carry (data/shoppers.py), so a fraud account's mandate looks like theirs.
HONEST_CAPS_CENTS = (5_000, 10_000, 15_000, 20_000, 25_000, 30_000, 40_000, 50_000, 60_000, 80_000, 100_000, 150_000)


@dataclass
class MarketplaceWorld(World):
    n_shoppers: int = 0  # legitimate shoppers come from the population instead
    population: Population | None = None
    round_seconds = ROUND_SECONDS  # one simulated day per round, like Ryan's traffic

    def __post_init__(self):
        self.population = self.population or Population(self.seed)
        self.products = [Product(p.product_id, p.merchant_id, p.category, p.price_cents) for p in load_catalog()]
        self.identities.update({s.principal.principal_id: s.principal.identity for s in self.population.shoppers})
        super().__post_init__()

    def fraud_mandate(self, principal_id: str, amount_cents: int, categories: list[str], daily_need_cents: int,
                      rng: random.Random) -> Mandate:
        """Caps like Ryan's shoppers': the smallest honest cap above the planned amounts, a daily cap
        3-5x that (or enough for the plan), and the categories the account will buy."""
        cap = next((c for c in HONEST_CAPS_CENTS if c >= amount_cents * 1.2), HONEST_CAPS_CENTS[-1])
        daily = max(cap * rng.choice([3, 4, 5]), daily_need_cents)
        words = ", ".join(c.replace("_", " ") for c in categories[:3])
        return Mandate(mandate_id=f"md-{principal_id}", principal_id=principal_id, per_payment_cap_cents=cap,
                       daily_cap_cents=daily, allowed_categories=categories,
                       request_text=f"buy {words} as needed, under ${cap // 100} per payment")

    def legit_traffic(self, run_id, round, n, start_ts, span_s):
        return [(lt.tx, lt.truth) for lt in generate_legit_traffic(run_id, round, n, self.seed, self.population)]
