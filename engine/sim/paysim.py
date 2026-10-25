"""PaySim (a public mobile-money fraud dataset) as the engine's world, for the demo.

Real PaySim payments are the honest shoppers, PaySim's own recorded fraud runs as one fixed attacker,
and the engine's attack agents (outsiders, threat intel, spawned, archive) attack on top as usual.
Like engine/public_bench.py: only TRANSFER and CASH_OUT rows (the only types that carry fraud), the
balance columns left out (they leak the label), read from a local file outside the repo as plain data.

How a PaySim row becomes a payment, and what had to be approximated:
- step (an hour) -> the hour within the round; PaySim day d is round d; waves split the day by time.
- nameOrig -> the agent and principal (most PaySim accounts appear once, so honest payers are new to
  the per-account features too); nameDest -> the merchant; type -> the category.
- amount -> amount_cents at 1/10,000 of PaySim's units (amount / 100 cents), so PaySim's typical
  transfer lands in the same range as the agents' payments. One scale for every PaySim row.
- Identity isn't in the data: 90% of honest principals verify, 10% are thin files, as in the
  marketplace. Recorded fraud is account takeover, so it pays from a victim's verified account.
- Mandates aren't in the data: every honest payment gets the smallest cap above its amount (the
  marketplace's caps, extended upward), and fraud accounts write theirs the same way.
- Products for the attack agents are the day-1 destinations, with PaySim's categories and typical
  amounts, so agent payments look like PaySim transfers rather than shop purchases.
- 300 honest payments a round (a deterministic, per-seed sample of the day; PaySim day 3 has only 119)
  and 25 of the day's recorded frauds, like one outsider's volume, sampled per seed so each
  takeover's transfer and cash-out stay together (PaySim links the two only by hour and amount).
"""

import csv
import hashlib
import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from contracts.schemas import (AttackerRecord, AttackerSource, AttackFeedback, GroundTruth, IdentityStatus, Mandate, Playbook,
                               ShelfKey, TrafficSource, Transaction)
from engine.sim.marketplace import HONEST_CAPS_CENTS, MarketplaceWorld
from engine.sim.world import Product, round_ts

DATA = Path("~/calhacks-sandbox/paysim/paysim.csv").expanduser()
TYPES = ("TRANSFER", "CASH_OUT")
DAY_STEPS = 24
KEEP_HONEST = 2_000  # per day in the cache, the most a round could ask for
CAPS_CENTS = HONEST_CAPS_CENTS + (200_000, 300_000, 500_000, 1_000_000, 2_000_000, 5_000_000)
RECORDED_ID = "paysim-account-takeover"


def _hash(*parts) -> int:
    return int.from_bytes(hashlib.blake2b(":".join(map(str, parts)).encode(), digest_size=8).digest(), "big")


def round_amount(amount: float) -> float:
    return round(amount, 2)


def cents(amount: float) -> int:
    return max(1, round(amount / 100))


def load(path: Path = DATA) -> dict[int, dict[str, list]]:
    """Per PaySim day: a hash sample of honest rows and every fraud row, as (step, type, amount, orig,
    dest). Cached next to the data, since the CSV takes a few seconds to read."""
    cache = path.with_name("arena-days-v3.json")
    if cache.exists():
        return {int(d): v for d, v in json.loads(cache.read_text()).items()}
    days: dict[int, dict[str, list]] = defaultdict(lambda: {"honest": [], "fraud": []})
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            if row["type"] not in TYPES:
                continue
            step = int(row["step"])
            rec = (step, row["type"], float(row["amount"]), row["nameOrig"], row["nameDest"])
            days[(step - 1) // DAY_STEPS + 1]["fraud" if row["isFraud"] == "1" else "honest"].append(rec)
    out = {}
    for d, v in days.items():
        # A takeover is a transfer and a cash-out of the same amount in the same hour (PaySim gives the two
        # different, anonymised accounts), so fraud is sampled by (hour, amount) to keep each pair together.
        honest = sorted(v["honest"], key=lambda r: (_hash(r[3]), r[0]))[:KEEP_HONEST]
        out[d] = {"honest": honest, "fraud": sorted(v["fraud"], key=lambda r: (_hash(r[0], round(r[2], 2)), r[1]))}
    cache.write_text(json.dumps(out))
    return out


def _mandate(principal: str, amount_cents: int, category: str) -> Mandate:
    cap = next((c for c in CAPS_CENTS if c >= amount_cents * 1.2), CAPS_CENTS[-1])
    return Mandate(mandate_id=f"md-{principal}", principal_id=principal, per_payment_cap_cents=cap,
                   daily_cap_cents=cap * 4, allowed_categories=[category], request_text=f"send money ({category})")


@dataclass
class PaySimWorld(MarketplaceWorld):
    data: Path = DATA
    days: dict = field(default_factory=dict)

    def __post_init__(self):
        super().__post_init__()  # keeps the marketplace's population, for the hijacker's victims
        self.days = load(self.data)
        seen, products = set(), []
        for step, kind, amount, _, dest in self.days[1]["honest"]:
            if dest not in seen and len(products) < 60:
                seen.add(dest)
                # no title: an agent payment's product text is its category, like a PaySim row's (a title here
                # made the mandate request-match feature tell every agent payment apart from honest traffic)
                products.append(Product(f"ps-{dest}", dest, kind.lower(), cents(amount)))
        self.products = products

    def fraud_mandate(self, principal_id, amount_cents, categories, daily_need_cents, rng):
        mandate = _mandate(principal_id, amount_cents, categories[0] if categories else "transfer")
        return mandate.model_copy(update={"daily_cap_cents": max(mandate.daily_cap_cents, daily_need_cents),
                                          "allowed_categories": list(categories) or mandate.allowed_categories})

    def payment(self, run_id: str, round: int, start_ts: float, row, tag: str, attacker_id: str | None = None,
                wave: int = 0) -> Transaction:
        step, kind, amount, orig, dest = row
        principal = f"pr-ps-{orig}"
        if principal not in self.identities:
            self.identities[principal] = IdentityStatus.THIN_FILE if _hash("id", orig) % 10 == 0 else IdentityStatus.VERIFIED
        if attacker_id:  # a takeover pays from the victim's own (verified) account
            self.identities[principal] = IdentityStatus.VERIFIED
        amount_cents = cents(amount)
        ts = start_ts + ((step - 1) % DAY_STEPS) * 3600 + _hash("ts", tag) % 3600
        return Transaction(tx_id=f"{run_id}-{tag}", run_id=run_id, round=round, wave=wave, ts=round_ts(ts), agent_id=f"ag-ps-{orig}",
                           principal_id=principal, merchant_id=dest, category=kind.lower(), amount_cents=amount_cents,
                           mandate=_mandate(principal, amount_cents, kind.lower()),
                           source=TrafficSource.ATTACKER if attacker_id else TrafficSource.LEGIT, attacker_id=attacker_id)

    def legit_traffic(self, run_id, round, n, start_ts, span_s):
        # each seed draws its own sample of the day (so different seeds are different data)
        rows = sorted(self.days.get(round, {"honest": []})["honest"], key=lambda r: _hash(self.seed, *r))[:n]
        out = [self.payment(run_id, round, start_ts, row, f"ps{round}-h{i}") for i, row in enumerate(rows)]
        return [(tx, GroundTruth(tx_id=tx.tx_id, is_fraud=False)) for tx in sorted(out, key=lambda t: (t.ts, t.tx_id))]


class PaySimRecordedFraud:
    """PaySim's own recorded fraud for the round's day: fixed, it never adapts (implements
    contracts.interfaces.Attacker, like engine/arena/outsiders.py)."""

    attacker_id = RECORDED_ID
    step_up_pass = 0.20  # the cost matrix's default; the data has no step-up outcomes

    def __init__(self, world: PaySimWorld):
        self.world = world
        self.shelf = ShelfKey(style="slow_drip", amount="over_100", side="buyer")
        self.playbook = Playbook(playbook_id=f"{RECORDED_ID}-pb", attacker_id=RECORDED_ID,
                                 strategy="Recorded PaySim account takeovers: empty the victim's account with a "
                                          "transfer to a mule, then cash out.", shelf=self.shelf,
                                 # only for the arena's sandbox scoring of archive offers; the payments
                                 # themselves are the recorded rows
                                 params={"amount_cents": 40_000, "amount_spread": 0.9, "interval_s": 3_000, "n_accounts": 25,
                                         "n_merchants": 20, "identity_quality": 1.0, "memo_rate": 0.0})
        self.round_stats = {"extraction": 0, "attempts": 0, "caught": 0, "profit": 0}
        self._round = 0
        self._amounts: dict[str, int] = {}

    def record(self) -> AttackerRecord:
        return AttackerRecord(attacker_id=RECORDED_ID, name="PaySim account takeover", source=AttackerSource.OUTSIDER,
                              lineage="paysim", playbook=self.playbook, profit_cents=self.round_stats["profit"],
                              scored_round=self._round or None)

    def generate(self, run_id: str, round: int, wave: int, n: int, seed: int) -> list[Transaction]:
        if round != self._round:
            self._round, self.round_stats = round, {"extraction": 0, "attempts": 0, "caught": 0, "profit": 0}
        start = (round - 1) * self.world.round_seconds
        # each seed draws its own 25 of the day's takeovers, sampled by (hour, amount) so pairs stay together
        day = sorted(self.world.days.get(round, {"fraud": []})["fraud"],
                     key=lambda r: (_hash(self.world.seed, r[0], round_amount(r[2])), r[1]))
        rows = [r for r in day[:25] if min(2, ((r[0] - 1) % DAY_STEPS) // 8) == wave][:n]
        txs = [self.world.payment(run_id, round, start, row, f"ps{round}-f{wave}-{i}", attacker_id=RECORDED_ID, wave=wave)
               for i, row in enumerate(rows)]
        self._amounts.update({t.tx_id: t.amount_cents for t in txs})
        return txs

    def truth(self, tx: Transaction) -> GroundTruth:
        return GroundTruth(tx_id=tx.tx_id, is_fraud=True, attacker_id=RECORDED_ID)

    def observe(self, feedback: list[AttackFeedback]) -> None:
        for f in feedback:
            amount = self._amounts.pop(f.tx_id, 0)
            self.round_stats["attempts"] += 1
            self.round_stats["caught"] += f.outcome.value != "allow"
            self.round_stats["extraction"] += amount if f.settled else 0
            self.round_stats["profit"] += amount if f.settled else 0
