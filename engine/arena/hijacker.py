"""The hijacker (spec 3.5, M9): an outsider lineage that has stolen legitimate agents' credentials
and spends with them. Each payment comes from a real shopper's agent, with that shopper's identity
and mandate, so nothing about the account looks wrong; the signal is the change from the agent's
usual behavior, plus mandate breaches.

It uses the same bounded numbers as every playbook (playbooks.REALISM), read for a hijacker:
- n_accounts: how many victims' agents it has taken over
- identity_quality: how carefully it stays inside each victim's mandate (allowed categories and the
  per-payment cap). It borrows real identities, so there's no identity to buy; the number is its caution.
- amount_cents, amount_spread, interval_s, n_merchants: as for any playbook
"""

import builtins
import random

from contracts.schemas import AttackStyle, GroundTruth, Mandate, Playbook, ShelfKey, TrafficSource, Transaction
from engine.arena import playbooks
from engine.arena.outsiders import OutsiderAttacker, Persona, shelf_for
from engine.arena.playbooks import REALISM, WAVES, Emitted

HIJACKER = Persona("hijacker", 0.10, "Spend with stolen agent credentials: big purchases the owners never make.",
                   {"amount_cents": 15_000, "amount_spread": 0.4, "interval_s": 900, "n_accounts": 3, "n_merchants": 6,
                    "identity_quality": 0.2, "memo_rate": 0.0})


def breaches(tx: Transaction) -> bool:
    m = tx.mandate
    return bool(m and (tx.amount_cents > m.per_payment_cap_cents
                       or (m.allowed_categories and tx.category not in m.allowed_categories)
                       or (m.allowed_merchants and tx.merchant_id not in m.allowed_merchants)))


def emit_hijacked(playbook: Playbook, *, run_id: str, round: int, wave: int, n: int, seed: int, products: list,
                  victims: list[tuple[str, str, Mandate]], round_seconds: float, tag: str = "",
                  start: int = 0) -> list[Emitted]:
    """Payments from hijacked agents inside this wave's window. Victims are chosen once per round."""
    p = playbooks.clamp(playbook.params, REALISM)
    if not victims or not products:
        return []
    rng = random.Random(f"{playbook.attacker_id}:{playbook.playbook_id}:{tag}:{seed}:{round}:{wave}")
    pick = random.Random(f"{playbook.attacker_id}{tag}:victims:{seed}:{round}")
    chosen = pick.sample(victims, min(len(victims), int(p["n_accounts"])))
    shops = random.Random(f"{playbook.attacker_id}{tag}:shops:{seed}:{round}").sample(products, min(len(products), int(p["n_merchants"])))
    window = round_seconds / WAVES
    opens = (round - 1) * round_seconds + wave * window
    end = opens + window
    t = opens + rng.uniform(0, min(window / 4, p["interval_s"] * 2))
    out = []
    for i in range(n):
        if t >= end:
            break
        agent, principal, mandate = chosen[(start + i) % len(chosen)]
        careful = rng.random() < p["identity_quality"]
        allowed = [x for x in products if not mandate.allowed_categories or x.category in mandate.allowed_categories]
        product = rng.choice(allowed) if careful and allowed else rng.choice(shops)
        amount = max(1, int(p["amount_cents"] * rng.uniform(1 - p["amount_spread"], 1 + p["amount_spread"])))
        if careful:
            amount = min(amount, mandate.per_payment_cap_cents)
        tx = Transaction(tx_id=f"{run_id}-r{round}w{wave}-{playbook.attacker_id}{tag}-{i + start}", run_id=run_id,
                         round=round, wave=wave, ts=builtins.round(t, 3), agent_id=agent, principal_id=principal,
                         merchant_id=product.merchant_id, product_id=product.product_id, category=product.category,
                         amount_cents=amount, mandate=mandate, source=TrafficSource.ATTACKER, attacker_id=playbook.attacker_id)
        out.append(Emitted(tx, GroundTruth(tx_id=tx.tx_id, is_fraud=True, attacker_id=playbook.attacker_id,
                                           breaches_mandate=breaches(tx))))
        t += p["interval_s"] * rng.uniform(0.7, 1.3)
    return out


class HijackerAttacker(OutsiderAttacker):
    """The hijacker lineage: everything an outsider does (outcomes only, live re-tuning, rewrites
    after a bad wave), but its payments come from hijacked agents. It buys no identities. Its
    playbook can't be replayed by the sandbox (that would invent fresh accounts), so it stays out of
    the archive; its outcomes count on the hijack shelf for its amount band."""

    archivable = False

    @property
    def shelf(self) -> ShelfKey:
        return shelf_for(self.playbook.params).model_copy(update={"style": AttackStyle.HIJACK_INJECTION})

    def __init__(self, brain, products, identities, round_seconds, victims: list[tuple[str, str, Mandate]],
                 role: str | None = None):
        super().__init__(HIJACKER, brain, products, identities, round_seconds, role=role)
        self.victims = victims

    def _emit(self, playbook: Playbook, *, run_id, round, wave, n, seed, tag, accounts, start) -> list[Emitted]:
        return emit_hijacked(playbook, run_id=run_id, round=round, wave=wave, n=n, seed=seed, products=self.products,
                             victims=self.victims, round_seconds=self.round_seconds, tag=tag, start=start)

    def truth(self, tx: Transaction) -> GroundTruth:
        return GroundTruth(tx_id=tx.tx_id, is_fraud=True, attacker_id=self.attacker_id, breaches_mandate=breaches(tx))

    def _identity_cost(self, principals) -> int:
        return 0  # stolen credentials: no identities bought
