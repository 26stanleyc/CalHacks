"""The malicious merchant (spec 3.1, M10): a store that poisons its own product pages so the
shopping agents reading them buy something their owner never authorized.

This is the buyer-side attack, and it inverts the usual shape. The other lineages buy accounts and
spend their own (stolen) money at honest stores; this one spends *honest shoppers'* money at its own
store. So:

- its victims are the world's real shoppers, with their real identities and mandates, like the
  hijacker's (engine/arena/hijacker.py). Nothing about the account looks wrong, because nothing
  about the account *is* wrong: the agent was talked into the purchase by the page it read.
- it buys no identities, so its attack costs are only blocked attempts and failed step-ups.
- its playbook sits on a `Side.BUYER` shelf, which is how losses are split merchant-side from
  buyer-side (bench/metrics.py reads `playbook.shelf.side`).
- it is not archivable: the sandbox replays a playbook as payments from the attacker's own
  accounts, which cannot express "an honest shopper read a page". Its outcomes still count on its
  shelf, so the arena grid shows the buyer side being defended.

A session is one shopper agent reading one of its pages:

- the agent reads the page; the hidden text wins with a probability that rises with the page's
  injection score, so a page the detector would catch is also a page that actually works;
- a hijacked session buys the upsell. A blatant page pushes the agent past its owner's per-payment
  cap or into a category the owner never authorized (a mandate breach, and a hard veto for the
  defense); a subtle page stays inside the mandate and only overcharges, which nothing but the page
  itself reveals. That subtle share is what the merchant-risk feature exists for.
- a session that resists buys the ordinary listed item at the listed price. Those payments are
  honest and are labeled honest, so blocking them counts as a false positive. A defense cannot pass
  M10 by refusing everything at a suspicious store.
"""

import builtins
import random
from dataclasses import dataclass

from contracts.schemas import (AttackStyle, GroundTruth, Mandate, Playbook, ShelfKey, Side, TrafficSource,
                               Transaction)
from engine.arena import playbooks
from engine.arena.hijacker import breaches
from engine.arena.outsiders import MALICIOUS_MERCHANT, OutsiderAttacker, shelf_for
from engine.arena.playbooks import REALISM, WAVES, Emitted
from engine.sim.pages import page_url, product_page
from engine.sim.world import Product

# Of the sessions on a page the detector would flag, the share the hidden text actually captures.
# Below the detector's threshold a page is just a store with bad copy and captures nobody.
HIJACK_SHARE = 0.6
PAGE_THRESHOLD = 0.5  # integrations.page_scanner.INJECTION_THRESHOLD, repeated so the sim has no import cycle
OVER_CAP_MARGIN = 1.15  # a blatant page asks for this much of the owner's per-payment cap
OVERCHARGE = 2.2  # what a subtle page charges against its own listed price
DECOY_PRICE_CENTS = 2_499  # the cheap item the listing advertises


@dataclass(frozen=True)
class Storefront:
    """One poisoned store. Fixed for the whole run: a shop front is not rebuilt every day."""

    merchant_id: str
    product: Product
    url: str


@dataclass
class Session:
    """One shopper agent reading one poisoned page, for the shopper_sessions log."""

    round: int
    agent_id: str
    pages_read: list[str]
    tx_id: str
    product: str
    hijacked: bool
    breaches: bool
    injection_score: float


def storefronts_for(attacker_id: str, n: int, category: str) -> list[Storefront]:
    fronts = []
    for k in range(max(1, n)):
        merchant_id = f"m-bad-{attacker_id}-{k}"
        product = Product(f"p-bad-{attacker_id}-{k}", merchant_id, category, DECOY_PRICE_CENTS,
                          "USB-C Charger (20W)")
        fronts.append(Storefront(merchant_id, product, page_url(merchant_id, product.product_id)))
    return fronts


def _hijacked_purchase(mandate: Mandate, base_amount: int, blatant: bool, categories: list[str],
                       storefront: Storefront, rng: random.Random) -> tuple[int, str, str]:
    """(amount, category, what the page talked the agent into). A blatant page breaks the owner's
    rules; a subtle one stays inside them and only overcharges."""
    cap = mandate.per_payment_cap_cents
    forbidden = [c for c in categories if c not in mandate.allowed_categories] if mandate.allowed_categories else []
    over_cap = int(cap * OVER_CAP_MARGIN) + 1
    if blatant and forbidden and rng.random() < 0.5:
        # into a category the owner never authorized, at a price that stays under the cap, so the
        # only thing wrong is *what* was bought
        return min(base_amount, max(1, cap)), rng.choice(forbidden), "unauthorized category add-on"
    if blatant and over_cap <= REALISM["amount_cents"][1]:
        return over_cap, storefront.product.category, "premium bundle over the owner's cap"
    if blatant and forbidden:
        return min(base_amount, max(1, cap)), rng.choice(forbidden), "unauthorized category add-on"
    # subtle: inside the mandate, but several times the page's own listed price
    amount = min(int(storefront.product.price_cents * OVERCHARGE), max(1, cap))
    return amount, storefront.product.category, "extended warranty at an inflated price"


def emit_page_driven(playbook: Playbook, *, run_id: str, round: int, wave: int, n: int, seed: int,
                     victims: list[tuple[str, str, Mandate]], storefronts: list[Storefront],
                     page_score, categories: list[str], round_seconds: float, tag: str = "",
                     start: int = 0) -> tuple[list[Emitted], list[Session]]:
    """Shopper sessions on this store's pages inside this wave's window."""
    p = playbooks.clamp(playbook.params, REALISM)
    if not victims or not storefronts:
        return [], []
    rng = random.Random(f"{playbook.attacker_id}:{playbook.playbook_id}:{tag}:{seed}:{round}:{wave}")
    pick = random.Random(f"{playbook.attacker_id}{tag}:shoppers:{seed}:{round}")
    chosen = pick.sample(victims, min(len(victims), int(p["n_accounts"])))
    window = round_seconds / WAVES
    opens = (round - 1) * round_seconds + wave * window
    end = opens + window
    t = opens + rng.uniform(0, min(window / 4, p["interval_s"] * 2))
    out: list[Emitted] = []
    sessions: list[Session] = []
    for i in range(n):
        if t >= end:
            break
        agent, principal, mandate = chosen[(start + i) % len(chosen)]
        front = storefronts[(start + i) % len(storefronts)]
        score = page_score(front.url)
        # Deterministic per session, so the same seed gives the same hijacks.
        roll = playbooks.unit_hash(f"{playbook.attacker_id}{tag}:r{round}:hijack:{start + i}")
        hijacked = score >= PAGE_THRESHOLD and roll < HIJACK_SHARE
        base = max(1, int(p["amount_cents"] * rng.uniform(1 - p["amount_spread"], 1 + p["amount_spread"])))
        blatant = rng.random() >= p["identity_quality"]
        if hijacked:
            amount, category, bought = _hijacked_purchase(mandate, base, blatant, categories, front, rng)
            memo = (rng.choice(playbook.free_text_payloads)
                    if playbook.free_text_payloads and rng.random() < p["memo_rate"] else "")
        else:
            # The agent resisted the page and behaved correctly: it buys the listed item at the
            # listed price, in a category its owner authorized. A general store stocks more than one
            # category, so this is an ordinary sale and must be labeled as one.
            amount = front.product.price_cents
            category = (rng.choice(mandate.allowed_categories) if mandate.allowed_categories
                        else front.product.category)
            bought, memo = front.product.title, ""
        tx = Transaction(
            tx_id=f"{run_id}-r{round}w{wave}-{playbook.attacker_id}{tag}-{start + i}", run_id=run_id, round=round,
            wave=wave, ts=builtins.round(t, 3), agent_id=agent, principal_id=principal,
            merchant_id=front.merchant_id, product_id=front.product.product_id, category=category,
            amount_cents=amount,
            # A hijacked payment carries no listed price: the store omits the figure that would
            # give the overcharge away, so `v_price_ratio` sees nothing and only the page's own
            # price (m_page_price_ratio) catches it. Honest sales carry the real price.
            listed_price_cents=None if hijacked else front.product.price_cents,
            memo=memo, mandate=mandate,
            source=TrafficSource.ATTACKER, attacker_id=playbook.attacker_id if hijacked else None,
        )
        truth = GroundTruth(tx_id=tx.tx_id, is_fraud=hijacked, attacker_id=playbook.attacker_id if hijacked else None,
                            breaches_mandate=hijacked and breaches(tx))
        out.append(Emitted(tx, truth))
        sessions.append(Session(round, agent, [front.url], tx.tx_id, bought, hijacked, truth.breaches_mandate, score))
        t += p["interval_s"] * rng.uniform(0.7, 1.3)
    return out, sessions


class MaliciousMerchantAttacker(OutsiderAttacker):
    """The malicious-merchant lineage. Everything an outsider does (outcomes only, live re-tuning,
    a rewrite after a bad wave), but its traffic is honest shoppers reading its poisoned pages."""

    archivable = False  # the sandbox cannot replay "a shopper read a page"

    @property
    def shelf(self) -> ShelfKey:
        return shelf_for(self.playbook.params, Side.BUYER).model_copy(update={"style": AttackStyle.HIJACK_INJECTION})

    def __init__(self, brain, products, identities, round_seconds, victims: list[tuple[str, str, Mandate]],
                 scanner, role: str | None = None, persona=MALICIOUS_MERCHANT):
        self.categories = sorted({p.category for p in products})
        super().__init__(persona, brain, products, identities, round_seconds, role=role)
        self.victims = victims
        self.scanner = scanner
        self.storefronts = storefronts_for(self.attacker_id, int(persona.params["n_merchants"]),
                                           self.categories[0] if self.categories else "electronics")
        self.sessions: list[Session] = []  # this round's, drained by the engine after each wave
        self._truth: dict[str, GroundTruth] = {}
        self._scores: dict[str, float] = {}

    # --- the pages it serves ---------------------------------------------------------------

    def pages(self) -> dict[str, tuple[str, str]]:
        """url -> (html, merchant_id) for this store's current pages. The engine puts these in the
        world's page store and primes their scans before the round's payments are decided, so no
        payment ever waits on a page fetch."""
        payloads = tuple(self.playbook.free_text_payloads or MALICIOUS_MERCHANT.payloads)
        return {f.url: (product_page(f.product.title, f.product.price_cents, merchant=f.merchant_id,
                                     injections=payloads), f.merchant_id)
                for f in self.storefronts}

    def _page_score(self, url: str) -> float:
        if url not in self._scores:
            self._scores[url] = self.scanner.scan(url).injection_score
        return self._scores[url]

    def forget_pages(self) -> None:
        """Called when the pages change (a rewritten playbook writes new hidden text)."""
        self._scores.clear()

    # --- Attacker --------------------------------------------------------------------------

    def shelf_of(self, params: dict) -> ShelfKey:
        """Always buyer-side, whatever the numbers do: the money this lineage takes is a shopper's.
        bench/metrics.py splits losses by `playbook.shelf.side`, so this has to survive re-tuning."""
        return shelf_for(params, Side.BUYER).model_copy(update={"style": AttackStyle.HIJACK_INJECTION})

    def generate(self, run_id: str, round: int, wave: int, n: int, seed: int) -> list[Transaction]:
        if round != self._where[0]:
            self.sessions = []
        txs = super().generate(run_id, round, wave, n, seed)
        for tx in txs:  # a session that resisted the page is an honest sale, not an attack attempt
            if not self._truth[tx.tx_id].is_fraud:
                self.sent.pop(tx.tx_id, None)
        return txs

    def _emit(self, playbook: Playbook, *, run_id, round, wave, n, seed, tag, accounts, start) -> list[Emitted]:
        emitted, sessions = emit_page_driven(
            playbook, run_id=run_id, round=round, wave=wave, n=n, seed=seed, victims=self.victims,
            storefronts=self.storefronts, page_score=self._page_score, categories=self.categories,
            round_seconds=self.round_seconds, tag=tag, start=start)
        self._truth.update({e.tx.tx_id: e.truth for e in emitted})
        self.sessions.extend(sessions)
        return emitted

    def truth(self, tx: Transaction) -> GroundTruth:
        known = self._truth.get(tx.tx_id)
        if known is not None:
            return known
        return GroundTruth(tx_id=tx.tx_id, is_fraud=True, attacker_id=self.attacker_id, breaches_mandate=breaches(tx))

    def _identity_cost(self, principals) -> int:
        return 0  # it spends its victims' money through their own accounts; it buys no identities
