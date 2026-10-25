"""The Fraud Desk's intent-to-action flow: "buy this for me, budget $30: <url>".

One chat message becomes four steps, each a real tool call:

1. **Scan the page** (`Engine.scan_page`: a live Apify scrape plus the injection detector).
   A page with hidden instructions for shopping agents, or one we could not read, stops
   here. That protects the buyer.
2. **Check the payment** (`Engine.check_payment`) at the page's listed price, under a
   mandate built from the user's budget, through the full pipeline: specialists,
   investigator, orchestrator. A block stops here. That protects the merchant.
3. **Ask the human.** An allowed or stepped-up payment comes back as an ASI:One review
   card with Approve and Cancel. Clients without cards get the same choice as text.
4. **Settle.** On Approve, one x402 payment settles on Base Sepolia and the reply links to
   it on basescan.

What it never does:
- It settles nothing the engine blocked.
- It settles nothing nobody approved.
- It settles nothing twice.
- It never settles more than a token amount: `TOKEN_CENTS` of test USDC, under
  `X402_MAX_SETTLE_CENTS`, stands in for the price. The decision is made at the real price.

Like every chat check, the payment is tagged `source=asi_one` and never trains the defense.
"""

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from contracts.entrypoints import Engine
from contracts.llm import ToolSpec
from contracts.schemas import Action, Decision, PageScan, Transaction
from integrations.asi_one.tools import EntryPointTools, ToolResult, amount_in_cents, format_decision

logger = logging.getLogger(__name__)

# What actually moves on Base Sepolia per approved purchase: one cent of test USDC.
TOKEN_CENTS = 1
# A public agent spends from one faucet-funded wallet, so cap how many purchases settle per run.
MAX_SETTLEMENTS = 25
PENDING_TTL_S = 15 * 60
INJECTION_STOP = 0.5

APPROVE, REJECT = "approve_purchase", "reject_purchase"
_PURCHASE_ID = re.compile(r"\b(buy-[0-9a-f]{12})\b")
_YES = re.compile(r"^\s*(yes|y|approve|approved|confirm|go ahead|ok|okay|do it)\b", re.IGNORECASE)
_NO = re.compile(r"^\s*(no|n|reject|cancel|stop|don't|do not)\b", re.IGNORECASE)

BUY_SPEC = ToolSpec(
    name="buy",
    description=(
        "Buy a product for the user from a product page URL: scan the page for hidden instructions, "
        "check the payment against their budget, ask them to approve, then pay through x402."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The product page URL."},
            "per_payment_cap_cents": {
                "type": "integer",
                "description": "The user's budget in cents, e.g. 'under $30' is 3000.",
            },
            "per_payment_cap_usd": {"type": "number", "description": "The user's budget in dollars."},
            "amount_cents": {
                "type": "integer",
                "description": "Only if the user states a price; otherwise the page's listed price is used.",
            },
            "request_text": {"type": "string", "description": "What the user asked for, in their words."},
        },
        "required": ["url"],
    },
)


@dataclass
class Pending:
    purchase_id: str
    sender: str
    tx: Transaction
    decision: Decision
    scan: PageScan
    created: float


class Purchases:
    """Pending purchases and their settlement, for one public agent."""

    def __init__(self, engine: Engine, settlement: Any | None, *, max_settlements: int = MAX_SETTLEMENTS):
        self.tools = EntryPointTools(engine, allowlist=set())  # only builds and checks payments; no team tools
        self.settlement = settlement  # an X402Settlement, or None when no wallet is configured
        self.max_settlements = max_settlements
        self.settled = 0
        self.pending: dict[str, Pending] = {}
        self._lock = threading.Lock()

    # -- step 1-3: from intent to a decision the human can approve -----------------------

    def start(self, arguments: dict[str, Any], sender: str) -> ToolResult:
        url = str(arguments.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return ToolResult("buy", "Send me the product page URL and your budget, e.g. "
                                     "\"buy https://shop.example/charger, budget $30\".", ok=False)

        scan = self.tools.engine.scan_page(url)
        if any(f.startswith("scrape failed") for f in scan.findings) and scan.injection_score < INJECTION_STOP:
            return ToolResult("buy", f"I couldn't read {url}, so I won't buy from it. Nothing was charged.",
                              ok=False, data=scan)
        if scan.injection_score >= INJECTION_STOP:
            found = "\n".join(f"- {f}" for f in scan.findings[:5])
            return ToolResult("buy", f"Not buying: that page hides instructions aimed at shopping agents "
                                     f"(injection score {scan.injection_score:.2f}).\n{found}\nNothing was charged.",
                              data=scan)

        cents = amount_in_cents(arguments) or scan.listed_price_cents
        if not cents:
            return ToolResult("buy", "I couldn't find a price on that page. Tell me the price, e.g. \"it's $24\".",
                              ok=False, data=scan)

        tx, mandate = self.tools.build_transaction({
            "merchant_id": scan.merchant_id or (urlparse(url).hostname or "unknown-merchant"),
            "listed_price_cents": scan.listed_price_cents,
            "per_payment_cap_cents": arguments.get("per_payment_cap_cents"),
            "per_payment_cap_usd": arguments.get("per_payment_cap_usd"),
            "request_text": str(arguments.get("request_text") or f"buy {url}"),
            "memo": url,
        }, cents)
        decision = self.tools.engine.check_payment(tx, mandate)
        checked = format_decision(decision, tx)

        if decision.action == Action.BLOCK:
            return ToolResult("buy", f"{checked}\n\nNot buying. Nothing was charged.", data=decision)

        purchase = Pending(f"buy-{uuid.uuid4().hex[:12]}", sender, tx, decision, scan, time.monotonic())
        with self._lock:
            self._expire()
            self.pending[purchase.purchase_id] = purchase
        ask = ("The defense wants a human to confirm this one." if decision.action == Action.STEP_UP
               else "The page is clean and the payment passed the fraud check.")
        text = (f"{checked}\n\n{ask} Approve to pay, or cancel. "
                f"(Without the card: reply \"approve {purchase.purchase_id}\" or \"cancel {purchase.purchase_id}\".)")
        return ToolResult("buy", text, data=decision, card=("review", self._review_card(purchase, mandate)))

    def _review_card(self, purchase: Pending, mandate) -> dict:
        tx, scan, decision = purchase.tx, purchase.scan, purchase.decision
        verdict = {"allow": "Allowed", "step_up": "Needs your confirmation"}.get(str(decision.action), str(decision.action))
        rows = [
            ("Item", scan.url),
            ("Price", f"${tx.amount_cents / 100:,.2f}"),
            ("Your budget", f"${mandate.per_payment_cap_cents / 100:,.2f}" if mandate else "none given"),
            ("Page scan", f"no hidden instructions (injection {scan.injection_score:.2f})"),
            ("Fraud check", f"{verdict}, suspicion {decision.weighted_score:.2f}"),
            ("Pays", f"${self._token_cents() / 100:.2f} test USDC on Base Sepolia via x402"),
        ]
        return {
            "title": "Confirm this purchase",
            "summary_rows": [{"label": label, "value": value} for label, value in rows],
            "approve_cta": {"label": "Approve and pay",
                            "selection": {"action": APPROVE, "purchase_id": purchase.purchase_id}, "primary": True},
            "reject_cta": {"label": "Cancel", "selection": {"action": REJECT, "purchase_id": purchase.purchase_id}},
        }

    # -- step 4: the human's answer --------------------------------------------------------

    def answer(self, sender: str, text: str) -> ToolResult | None:
        """The result of approving or cancelling, or None if `text` answers no purchase."""
        action, purchase_id = _parse_answer(text)
        with self._lock:
            self._expire()
            if purchase_id is None:
                mine = [p for p in self.pending.values() if p.sender == sender]
                if action is None or len(mine) != 1:
                    return None  # a bare "yes" is only an answer when exactly one purchase waits on it
                purchase_id = mine[0].purchase_id
            purchase = self.pending.pop(purchase_id, None)
        if purchase is None:
            if action is None:
                return None
            return ToolResult("buy", f"There's no open purchase {purchase_id}: it was already "
                                     "paid, cancelled or it expired. Nothing was charged.", ok=False)
        if action is None:  # the id alone, with no verb: ask again rather than guess
            with self._lock:
                self.pending[purchase_id] = purchase
            return ToolResult("buy", f"Approve or cancel {purchase_id}?", ok=False)
        if purchase.sender != sender:
            logger.info("Purchase %s opened by %s was answered by %s", purchase_id, purchase.sender, sender)
        if action == REJECT:
            return ToolResult("buy", f"Cancelled {purchase_id}. Nothing was charged.")
        return self._settle(purchase)

    def _settle(self, purchase: Pending) -> ToolResult:
        if self.settlement is None:
            return ToolResult("buy", "Approved, but no x402 wallet is configured here, so nothing was paid.", ok=False)
        with self._lock:
            if self.settled >= self.max_settlements:
                return ToolResult("buy", "Approved, but this agent has used its testnet payment budget for now. "
                                         "Nothing was charged.", ok=False)
            self.settled += 1
        tx = purchase.tx.model_copy(update={"amount_cents": self._token_cents()})
        # The human's approval is what a step-up asks for, so an approved step-up settles as allowed.
        decision = purchase.decision.model_copy(update={"action": Action.ALLOW})
        try:
            result = self.settlement.settle(tx, decision)
        except Exception:
            logger.exception("Settlement of %s raised", purchase.purchase_id)
            result = None
        if result is None or not result.settled:
            with self._lock:
                self.settled -= 1
                # Keep it open: the facilitator fails now and then, and a retry is safe because a
                # failed authorization moved nothing and the next attempt signs a fresh one.
                self.pending[purchase.purchase_id] = purchase
            reason = getattr(result, "reason", "") or "the facilitator didn't answer"
            return ToolResult("buy", f"The payment didn't go through ({reason}). Nothing was charged. "
                                     f"Approve again to retry, or cancel {purchase.purchase_id}.",
                              ok=False, data=result)
        price = purchase.tx.amount_cents / 100
        if result.tx_hash:
            from integrations.x402_settlement import explorer_url

            where = f"Paid on Base Sepolia through x402: {explorer_url(result.tx_hash)}"
        else:
            where = "Paid (simulated: this agent runs x402 in stub mode, so no chain call was made)."
        return ToolResult("buy", f"Done. {where}\n${tx.amount_cents / 100:.2f} of test USDC stood in for the "
                                 f"${price:,.2f} price. Purchase {purchase.purchase_id}, tx {purchase.tx.tx_id}.",
                          data=result)

    # -- helpers ---------------------------------------------------------------------------

    def _token_cents(self) -> int:
        cap = getattr(self.settlement, "max_cents", TOKEN_CENTS)
        return max(1, min(TOKEN_CENTS, cap))

    def _expire(self) -> None:
        now = time.monotonic()
        for purchase_id in [k for k, p in self.pending.items() if now - p.created > PENDING_TTL_S]:
            del self.pending[purchase_id]


def _parse_answer(text: str) -> tuple[str | None, str | None]:
    """(action, purchase_id) from a card click or a typed reply.

    A direct card click arrives as the CTA's selection serialized to JSON; a planner-routed
    one as prose that mentions the purchase id; a typed reply as "approve buy-...".
    """
    stripped = (text or "").strip()
    try:
        data = json.loads(stripped)
    except ValueError:
        data = None
    if isinstance(data, dict) and data.get("action") in (APPROVE, REJECT):
        found = _PURCHASE_ID.search(str(data.get("purchase_id", "")))
        return data["action"], found.group(1) if found else None

    found = _PURCHASE_ID.search(stripped)
    lowered = stripped.lower()
    if _NO.match(stripped) or any(w in lowered for w in ("cancel", "reject", "decline")):
        action = REJECT
    elif _YES.match(stripped) or any(w in lowered for w in ("approve", "confirm", "go ahead")):
        action = APPROVE
    else:
        action = None
    if found is None and len(stripped) > 40:
        action = None  # a long message without an id is a new request, not a yes or a no
    return action, found.group(1) if found else None
