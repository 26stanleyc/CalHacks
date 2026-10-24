"""Six hand-written held-out attackers (R4, spec 4 "Held-out attackers").

Frozen exam only: they never enter the archive, reflection, learning labels, XGBoost training or
the critic's inputs. Hand-written by Ryan (who writes no seed rules), with no LLM; `model` is None.

Each uses a fraud pattern that none of the six outsider personas (card tester, structurer,
account farmer, prompt-injection attacker, hijacker, malicious merchant) uses:

| Attacker                 | Side     | Style            | Amount     | Pattern                                    |
|--------------------------|----------|------------------|------------|--------------------------------------------|
| gift_card_cashout        | merchant | burst            | over $100  | stolen funds turned into gift cards fast   |
| chargeback_drip          | merchant | slow drip        | $10-$100   | friendly fraud: normal buys, disputed later|
| rented_account_ring      | merchant | many accounts    | over $100  | verified rented accounts behind few devices|
| mandate_replay           | merchant | hijack/injection | $10-$100   | a captured mandate replayed by another payer|
| fee_stacking_merchant    | buyer    | burst            | under $10  | hidden add-on fees right after a purchase  |
| unsolicited_renewal      | buyer    | slow drip        | $10-$100   | fake "renewals" nobody asked for           |

Every number lives in the playbook's params, inside its realism bounds, and `generate` is
deterministic for a given (seed, round, wave).
"""

import random
from functools import lru_cache

from contracts.schemas import (
    AmountBand,
    AttackerRecord,
    AttackerSource,
    AttackFeedback,
    AttackStyle,
    GroundTruth,
    Mandate,
    Playbook,
    Product,
    ShelfKey,
    Side,
    TrafficSource,
    Transaction,
)
from data.catalog import products_by_category
from data.shoppers import ROUND_SECONDS, WAVES, Persona, Population, Shopper


@lru_cache(maxsize=8)
def _victims(seed: int) -> tuple[Shopper, ...]:
    """Legit shoppers from the same population the round's legit traffic uses."""
    return tuple(Population(seed).by_persona[Persona.NORMAL])


class HandWrittenAttacker:
    """Shared plumbing: record, deterministic RNG and timestamps. Subclasses write `_emit`."""

    attacker_id: str
    name: str
    strategy: str
    shelf: ShelfKey
    params: dict[str, float]
    bounds: dict[str, tuple[float, float]]
    payloads: list[str] = []

    def __init__(self):
        for key, value in self.params.items():
            lo, hi = self.bounds[key]
            assert lo <= value <= hi, f"{self.attacker_id}: {key}={value} outside its realism bounds"
        self.feedback: list[AttackFeedback] = []

    def record(self) -> AttackerRecord:
        return AttackerRecord(
            attacker_id=self.attacker_id, name=self.name, source=AttackerSource.HELD_OUT, model=None,
            playbook=Playbook(playbook_id=f"{self.attacker_id}-pb0", attacker_id=self.attacker_id,
                              strategy=self.strategy, params=dict(self.params), param_bounds=dict(self.bounds),
                              shelf=self.shelf, free_text_payloads=list(self.payloads)),
        )

    def generate(self, run_id: str, round: int, wave: int, n: int, seed: int) -> list[Transaction]:
        rng = random.Random(f"{self.attacker_id}:{seed}:{round}:{wave}")
        start = (round - 1) * ROUND_SECONDS + (wave % WAVES) * ROUND_SECONDS / WAVES + rng.uniform(0, 3_600)
        end = round * ROUND_SECONDS - 1  # a slow drip that runs long stays inside its round
        rows = self._emit(rng, n, seed, round)
        out = []
        for i, row in enumerate(rows[:n]):
            out.append(Transaction(
                tx_id=f"{run_id}-r{round}w{wave}-{self.attacker_id}-{i}", run_id=run_id, round=round, wave=wave,
                ts=round_ms(min(end, start + row.pop("dt"))), source=TrafficSource.ATTACKER,
                attacker_id=self.attacker_id,
                **row,
            ))
        return out

    def observe(self, feedback: list[AttackFeedback]) -> None:
        self.feedback.extend(feedback)  # held-out attackers never adapt; kept only for the exam report

    def truth(self, tx: Transaction) -> GroundTruth:
        m = tx.mandate
        breaches = bool(m and (tx.amount_cents > m.per_payment_cap_cents or m.principal_id != tx.principal_id
                               or (m.allowed_categories and tx.category not in m.allowed_categories)))
        return GroundTruth(tx_id=tx.tx_id, is_fraud=True, attacker_id=self.attacker_id, breaches_mandate=breaches)

    def _emit(self, rng: random.Random, n: int, seed: int, rnd: int) -> list[dict]:
        raise NotImplementedError

    # helpers
    def _product(self, rng: random.Random, categories: list[str], near_cents: float) -> Product:
        options = [p for c in categories for p in products_by_category()[c]]
        return min(rng.sample(options, min(6, len(options))), key=lambda p: abs(p.price_cents - near_cents))

    def _jitter(self, rng: random.Random, name: str, spread: float = 0.15) -> float:
        lo, hi = self.bounds[name]
        return min(hi, max(lo, self.params[name] * rng.uniform(1 - spread, 1 + spread)))


class GiftCardCashout(HandWrittenAttacker):
    attacker_id = "ho-gift-card-cashout"
    name = "gift-card cash-out burst"
    strategy = ("A freshly funded wallet (stolen funds) converts to digital gift cards as fast as possible, "
                "spread over a few stores, before the funding is reversed.")
    shelf = ShelfKey(style=AttackStyle.BURST, amount=AmountBand.OVER_100, side=Side.MERCHANT)
    params = {"amount_cents": 15_000.0, "interval_s": 8.0, "n_merchants": 3.0}
    bounds = {"amount_cents": (10_000.0, 25_000.0), "interval_s": (2.0, 120.0), "n_merchants": (1.0, 6.0)}

    def _emit(self, rng, n, seed, rnd):
        principal = f"ho-gc-p-{rnd}"
        mandate = Mandate(mandate_id=f"ho-gc-md-{rnd}", principal_id=principal, per_payment_cap_cents=30_000,
                          daily_cap_cents=300_000, allowed_categories=["gift_cards"],
                          request_text="buy gift cards for the team's holiday bonuses")
        cards = [p for p in products_by_category()["gift_cards"]]
        merchants = sorted({p.merchant_id for p in cards})[: int(self.params["n_merchants"])]
        rows, dt = [], 0.0
        for _ in range(n):
            card = rng.choice([p for p in cards if p.merchant_id in merchants] or cards)
            qty = max(1, round(self._jitter(rng, "amount_cents") / card.price_cents))
            amount = min(int(self.bounds["amount_cents"][1]), card.price_cents * qty)
            rows.append(dict(dt=dt, agent_id=f"ho-gc-agent-{rnd}", principal_id=principal,
                             merchant_id=card.merchant_id, product_id=card.product_id, category=card.category,
                             amount_cents=amount, listed_price_cents=amount, mandate=mandate))
            dt += self._jitter(rng, "interval_s", 0.5)
        return rows


class ChargebackDrip(HandWrittenAttacker):
    attacker_id = "ho-chargeback-drip"
    name = "friendly-fraud chargeback drip"
    strategy = ("A verified buyer makes ordinary-looking digital purchases a few hours apart, all within its "
                "mandate, and disputes every one later. Nothing about a single payment looks wrong.")
    shelf = ShelfKey(style=AttackStyle.SLOW_DRIP, amount=AmountBand.FROM_10_TO_100, side=Side.MERCHANT)
    params = {"amount_cents": 4_500.0, "interval_s": 7_200.0}
    bounds = {"amount_cents": (1_000.0, 9_900.0), "interval_s": (1_800.0, 21_600.0)}

    def _emit(self, rng, n, seed, rnd):
        principal = "ho-cb-p-1"
        mandate = Mandate(mandate_id="ho-cb-md-1", principal_id=principal, per_payment_cap_cents=10_000,
                          daily_cap_cents=60_000, allowed_categories=["software", "digital_media", "cloud_compute"],
                          request_text="renew my apps and buy media I like")
        rows, dt = [], 0.0
        for _ in range(n):
            p = self._product(rng, mandate.allowed_categories, self._jitter(rng, "amount_cents"))
            rows.append(dict(dt=dt, agent_id="ho-cb-agent-1", principal_id=principal, merchant_id=p.merchant_id,
                             product_id=p.product_id, category=p.category, amount_cents=p.price_cents,
                             listed_price_cents=p.price_cents, mandate=mandate))
            dt += self._jitter(rng, "interval_s", 0.3)
        return rows


class RentedAccountRing(HandWrittenAttacker):
    attacker_id = "ho-rented-account-ring"
    name = "rented-account mule ring"
    strategy = ("Rent fully verified accounts from real people and buy resellable electronics through them, "
                "but run every account from the same couple of devices.")
    shelf = ShelfKey(style=AttackStyle.MANY_ACCOUNTS, amount=AmountBand.OVER_100, side=Side.MERCHANT)
    params = {"n_accounts": 12.0, "n_agents": 2.0, "amount_cents": 18_000.0, "interval_s": 240.0}
    bounds = {"n_accounts": (2.0, 40.0), "n_agents": (1.0, 5.0), "amount_cents": (10_500.0, 30_000.0),
              "interval_s": (30.0, 3_600.0)}

    def _emit(self, rng, n, seed, rnd):
        rows, dt = [], 0.0
        for i in range(n):
            k = i % int(self.params["n_accounts"]) + 1
            principal = f"ho-ring-p-{k}"
            mandate = Mandate(mandate_id=f"ho-ring-md-{k}", principal_id=principal, per_payment_cap_cents=30_000,
                              daily_cap_cents=60_000, allowed_categories=["electronics", "phone_accessories"],
                              request_text="buy a new monitor or headphones")
            p = self._product(rng, ["electronics"], self._jitter(rng, "amount_cents"))
            rows.append(dict(dt=dt, agent_id=f"ho-ring-device-{rng.randint(1, int(self.params['n_agents']))}",
                             principal_id=principal, merchant_id=p.merchant_id, product_id=p.product_id,
                             category=p.category, amount_cents=max(p.price_cents, 10_500),
                             listed_price_cents=max(p.price_cents, 10_500), mandate=mandate))
            dt += self._jitter(rng, "interval_s", 0.4)
        return rows


class MandateReplay(HandWrittenAttacker):
    attacker_id = "ho-mandate-replay"
    name = "captured-mandate replay"
    strategy = ("Capture real shoppers' signed mandates and attach them to payments from a different payer, "
                "with a bland 'standing order' memo, staying inside each mandate's caps and categories.")
    shelf = ShelfKey(style=AttackStyle.HIJACK_INJECTION, amount=AmountBand.FROM_10_TO_100, side=Side.MERCHANT)
    params = {"amount_cents": 6_000.0, "interval_s": 900.0, "n_mandates": 3.0}
    bounds = {"amount_cents": (1_000.0, 9_900.0), "interval_s": (60.0, 7_200.0), "n_mandates": (1.0, 10.0)}
    payloads = ["Standing order #{ref}, renewal as authorized."]

    def _emit(self, rng, n, seed, rnd):
        captured = [v.mandate for v in _victims(seed)[: int(self.params["n_mandates"])]]
        rows, dt = [], 0.0
        for i in range(n):
            mandate = captured[i % len(captured)]
            near = min(self._jitter(rng, "amount_cents"), mandate.per_payment_cap_cents)
            p = self._product(rng, mandate.allowed_categories or ["electronics"], near)
            rows.append(dict(dt=dt, agent_id="ho-replay-agent", principal_id="ho-replay-p-1",
                             merchant_id=p.merchant_id, product_id=p.product_id, category=p.category,
                             amount_cents=p.price_cents, listed_price_cents=p.price_cents, mandate=mandate,
                             memo=self.payloads[0].format(ref=1000 + rng.randint(0, 8999))))
            dt += self._jitter(rng, "interval_s", 0.5)
        return rows


class FeeStackingMerchant(HandWrittenAttacker):
    attacker_id = "ho-fee-stacking-merchant"
    name = "post-checkout fee stacking"
    strategy = ("A storefront sells at normal prices, then within seconds charges the buyer's agent several "
                "small add-on fees (shipping protection, service fee) that nobody agreed to.")
    shelf = ShelfKey(style=AttackStyle.BURST, amount=AmountBand.UNDER_10, side=Side.BUYER)
    params = {"fee_cents": 499.0, "fees_per_purchase": 3.0, "interval_s": 4.0}
    bounds = {"fee_cents": (100.0, 999.0), "fees_per_purchase": (1.0, 5.0), "interval_s": (1.0, 60.0)}
    payloads = ["shipping protection", "service fee", "priority handling", "eco packaging"]
    merchant_id = "m-ho-addon-fees"

    def _emit(self, rng, n, seed, rnd):
        rows, dt = [], 0.0
        while len(rows) < n:
            victim = rng.choice(_victims(seed))
            for k in range(int(self.params["fees_per_purchase"])):
                fee = int(self._jitter(rng, "fee_cents", 0.4))
                rows.append(dict(dt=dt, agent_id=victim.agent_id, principal_id=victim.principal.principal_id,
                                 merchant_id=self.merchant_id, category=victim.mandate.allowed_categories[0],
                                 amount_cents=fee, listed_price_cents=None, mandate=victim.mandate,
                                 memo=self.payloads[k % len(self.payloads)]))
                dt += self._jitter(rng, "interval_s", 0.5)
            dt += rng.uniform(600, 3_600)  # next victim
        return rows


class UnsolicitedRenewal(HandWrittenAttacker):
    attacker_id = "ho-unsolicited-renewal"
    name = "unsolicited subscription renewals"
    strategy = ("A fake software store charges shopping agents 'renewals' for subscriptions they never bought, "
                "spread over the day, at prices that match its own listings.")
    shelf = ShelfKey(style=AttackStyle.SLOW_DRIP, amount=AmountBand.FROM_10_TO_100, side=Side.BUYER)
    params = {"amount_cents": 2_900.0, "interval_s": 5_400.0, "n_victims": 6.0}
    bounds = {"amount_cents": (1_000.0, 9_900.0), "interval_s": (600.0, 43_200.0), "n_victims": (1.0, 30.0)}
    merchant_id = "m-ho-renewals"

    def _emit(self, rng, n, seed, rnd):
        victims = _victims(seed)[-int(self.params["n_victims"]):]
        rows, dt = [], 0.0
        for i in range(n):
            victim = victims[i % len(victims)]
            amount = int(self._jitter(rng, "amount_cents", 0.2))
            rows.append(dict(dt=dt, agent_id=victim.agent_id, principal_id=victim.principal.principal_id,
                             merchant_id=self.merchant_id, category="software", amount_cents=amount,
                             listed_price_cents=amount, mandate=victim.mandate))
            dt += self._jitter(rng, "interval_s", 0.3)
        return rows


def round_ms(t: float) -> float:
    return round(t, 3)


HELD_OUT_CLASSES: list[type[HandWrittenAttacker]] = [
    GiftCardCashout, ChargebackDrip, RentedAccountRing, MandateReplay, FeeStackingMerchant, UnsolicitedRenewal,
]


def held_out_attackers() -> list[HandWrittenAttacker]:
    """Fresh instances of all six, for the frozen exam harness."""
    return [cls() for cls in HELD_OUT_CLASSES]
