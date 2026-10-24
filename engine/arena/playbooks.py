"""Playbooks to payments (spec 3.5, 3.9 point 5): the LLM picks the strategy, the numbers live in
bounded params, and this deterministic generator turns a playbook into transactions.

One generator covers every merchant-side style; the style is in the numbers:
- burst: a short interval_s from one or a few accounts
- slow drip: a long interval_s
- many accounts: a high n_accounts, so each account pays once or twice
- injection: memo_rate > 0, with the playbook's free-text payloads as memos

Every number has a realism limit (REALISM) that a real attacker could actually reach, and a
playbook's own bounds must sit inside it. `validate` checks both before a playbook is used, so an
optimizer or a model can't win by exploiting a simulator quirk.

Attacker profit (spec 3.5) = extraction (settled fraud dollars) minus attack costs: identities
used, attempts that were blocked (a burned card or credential), and failed step-ups.
"""

import builtins
import hashlib
import math
import random
from collections.abc import Callable
from dataclasses import dataclass

from contracts.schemas import GroundTruth, IdentityStatus, Mandate, Playbook, TrafficSource, Transaction

WAVES = 3

REALISM: dict[str, tuple[float, float]] = {
    "amount_cents": (50.0, 50_000.0),  # $0.50 to $500 per payment
    "amount_spread": (0.0, 0.9),  # each payment is amount_cents x (1 +- spread)
    "interval_s": (1.0, 3_000.0),  # seconds between an attacker's payments
    "n_accounts": (1.0, 40.0),  # distinct agent + principal pairs: the identity budget
    "n_merchants": (1.0, 20.0),
    "identity_quality": (0.0, 1.0),  # share of accounts on verified (rented or stolen) identities
    "memo_rate": (0.0, 1.0),  # share of payments that carry a free-text payload
}
INTEGER_PARAMS = ("n_accounts", "n_merchants")

IDENTITY_COST_CENTS = {IdentityStatus.VERIFIED: 2_000, IdentityStatus.THIN_FILE: 300, IdentityStatus.MISMATCH: 100}
BLOCKED_ATTEMPT_COST_CENTS = 100
FAILED_STEP_UP_COST_CENTS = 200
SELF_MANDATE_CAP_CENTS = 300_000  # fraud accounts carry mandates their owners wrote themselves


def unit_hash(key: str) -> float:
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") / 2**64


def validate(playbook: Playbook) -> list[str]:
    """Problems that make a playbook unusable; empty means it's fine."""
    errors = []
    for name in REALISM:
        if name not in playbook.params:
            errors.append(f"missing param {name}")
    for name in playbook.param_bounds:
        if name not in REALISM:
            errors.append(f"bounds for unknown param {name}")
    for name, value in playbook.params.items():
        if name not in REALISM:
            errors.append(f"unknown param {name}")
            continue
        bounds = playbook.param_bounds.get(name, REALISM[name])
        if len(bounds) != 2 or not all(isinstance(b, (int, float)) and math.isfinite(b) for b in (value, *bounds)):
            errors.append(f"{name} and its bounds must be finite numbers")
            continue
        lo, hi = bounds
        real_lo, real_hi = REALISM[name]
        if not real_lo <= lo <= hi <= real_hi:
            errors.append(f"bounds for {name} ({lo}, {hi}) are outside the realism limits ({real_lo}, {real_hi})")
        elif not lo <= value <= hi:
            errors.append(f"{name}={value} is outside its bounds ({lo}, {hi})")
        elif name in INTEGER_PARAMS and (math.ceil(lo) > hi or value != round(value)):
            errors.append(f"{name} must be a whole number inside its bounds")
    if playbook.params.get("memo_rate", 0) > 0 and not playbook.free_text_payloads:
        errors.append("memo_rate > 0 needs at least one free-text payload")
    return errors


def bounds_of(playbook: Playbook) -> dict[str, tuple[float, float]]:
    return {name: tuple(playbook.param_bounds.get(name, REALISM[name])) for name in REALISM}


def clamp(params: dict[str, float], bounds: dict[str, tuple[float, float]]) -> dict[str, float]:
    out = {}
    for name, (lo, hi) in bounds.items():
        value = min(hi, max(lo, float(params.get(name, lo))))
        if name in INTEGER_PARAMS:  # a whole number that still sits inside the bounds
            value = float(min(math.floor(hi), max(math.ceil(lo), round(value))))
        out[name] = value
    return out


def account_identity(attacker_id: str, round: int, k: int, quality: float) -> IdentityStatus:
    """Fixed per account, so re-running a playbook gives the same identities."""
    if unit_hash(f"{attacker_id}:r{round}:id:{k}") < quality:
        return IdentityStatus.VERIFIED
    return IdentityStatus.THIN_FILE if unit_hash(f"{attacker_id}:r{round}:thin:{k}") < 0.7 else IdentityStatus.MISMATCH


@dataclass
class Emitted:
    tx: Transaction
    truth: GroundTruth


MandateFor = Callable[[str, int, list[str], int, random.Random], Mandate]


def self_mandate(principal_id: str, amount_cents: int, categories: list[str], daily_need_cents: int,
                 rng: random.Random) -> Mandate:
    return Mandate(mandate_id=f"md-{principal_id}", principal_id=principal_id, per_payment_cap_cents=SELF_MANDATE_CAP_CENTS,
                   daily_cap_cents=SELF_MANDATE_CAP_CENTS * 10, request_text="buy whatever is needed")


def emit(playbook: Playbook, *, run_id: str, round: int, wave: int, n: int, seed: int, products: list,
         identities: dict[str, IdentityStatus], round_seconds: float, tag: str = "",
         mandate_for: MandateFor = self_mandate, accounts: dict[str, Mandate] | None = None,
         start: int = 0) -> list[Emitted]:
    """Up to `n` payments for one wave, inside that wave's time window (a slow drip that runs out of
    time emits fewer).

    Accounts are fresh each round. An account is created on its first payment: its identity goes
    into `identities` (the lookup an identity check answers from) and it writes itself a mandate
    with `mandate_for` (the world's style, so mandates don't give it away). Both stay fixed for the
    rest of the round, even if the playbook changes. Pass the same `accounts` dict for every wave
    of a round, and `start` = payments this probe already sent this round, so payment i always
    goes to account (start + i) mod n_accounts and a many-accounts playbook really spreads out."""
    accounts = {} if accounts is None else accounts
    p = clamp(playbook.params, REALISM)
    rng = random.Random(f"{playbook.attacker_id}:{playbook.playbook_id}:{tag}:{seed}:{round}:{wave}")
    window = round_seconds / WAVES
    opens = (round - 1) * round_seconds + wave * window
    end = opens + window
    shop_rng = random.Random(f"{playbook.attacker_id}{tag}:shops:{seed}:{round}")  # the same stores all round
    shops = shop_rng.sample(products, min(len(products), int(p["n_merchants"])))
    top_amount = int(p["amount_cents"] * (1 + p["amount_spread"]))
    categories = sorted({s.category for s in shops})
    n_accounts = int(p["n_accounts"])
    daily_need = int(top_amount * max(1, n) * WAVES / n_accounts * 1.2)
    t = opens + rng.uniform(0, min(window / 4, p["interval_s"] * 2))
    out = []
    for i in range(n):
        if t >= end:
            break
        k = (start + i) % n_accounts
        agent, principal = f"{playbook.attacker_id}{tag}-r{round}-ag{k}", f"{playbook.attacker_id}{tag}-r{round}-pr{k}"
        if principal not in accounts:
            identities.setdefault(principal, account_identity(f"{playbook.attacker_id}{tag}", round, k, p["identity_quality"]))
            accounts[principal] = mandate_for(principal, top_amount, categories, daily_need,
                                              random.Random(f"{principal}:mandate:{seed}"))
        mandate = accounts[principal]
        product = shops[i % len(shops)] if len(shops) <= 3 else rng.choice(shops)
        amount = max(1, int(p["amount_cents"] * rng.uniform(1 - p["amount_spread"], 1 + p["amount_spread"])))
        memo = rng.choice(playbook.free_text_payloads) if playbook.free_text_payloads and rng.random() < p["memo_rate"] else ""
        tx = Transaction(
            tx_id=f"{run_id}-r{round}w{wave}-{playbook.attacker_id}{tag}-{i}", run_id=run_id, round=round, wave=wave,
            ts=builtins.round(t, 3), agent_id=agent, principal_id=principal, merchant_id=product.merchant_id,
            product_id=product.product_id, category=product.category, amount_cents=amount, memo=memo,
            mandate=mandate, source=TrafficSource.ATTACKER, attacker_id=playbook.attacker_id,
        )
        out.append(Emitted(tx, GroundTruth(tx_id=tx.tx_id, is_fraud=True, attacker_id=playbook.attacker_id)))
        t += p["interval_s"] * rng.uniform(0.7, 1.3)
    return out


def profit_cents(amounts: list[int], settled: list[bool], outcomes: list[str], n_accounts_cost: int) -> int:
    """Extraction minus attack costs for a set of an attacker's payments."""
    extraction = sum(a for a, s in zip(amounts, settled) if s)
    blocked = sum(o == "block" for o in outcomes)
    failed_step_ups = sum(o == "step_up" and not s for o, s in zip(outcomes, settled))
    return extraction - blocked * BLOCKED_ATTEMPT_COST_CENTS - failed_step_ups * FAILED_STEP_UP_COST_CENTS - n_accounts_cost


