"""`contracts.interfaces.PageScanner`: the seam M10 plugs into.

This is the E6/M10 groundwork, not M10 itself. What is here now:

- `InjectionDetector` - the scoring, over page text. Real and already useful: it is what
  gives `scan_page` its number, and it needs no network, so it is cheap to test and runs
  inside the feature service on every cached page (spec 3.2 merchant-risk features).
- `FixturePageScanner` - scans local fixture pages, for tests and the offline demo.
- `ApifyPageScanner` - fetches the page through Apify, then scores it with the same
  detector. This is the one `scan_page` uses at M10.

Scoring the text separately from fetching it is the point: at M10 only the fetch changes,
and the Merchant Scanner chat agent, the investigator's `scan_page` tool and the
merchant-risk feature all keep the same shape.
"""

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from contracts.schemas import PageScan

logger = logging.getLogger(__name__)

# Default Apify actor for turning a URL into page text. Overridable because M10 may want
# a different one once we see what real merchant pages need.
DEFAULT_ACTOR = os.environ.get("APIFY_PAGE_ACTOR", "apify/website-content-crawler")

INJECTION_THRESHOLD = 0.5

# Each pattern is a phrase a poisoned product page uses to redirect a shopping agent,
# with the weight it contributes. Scores are summed and capped at 1.0, so one strong
# phrase or several weak ones both clear the threshold.
PATTERNS: tuple[tuple[str, float, str], ...] = (
    (r"ignore (?:all |any )?(?:your |the )?(?:previous |prior |earlier )?(?:instructions|rules|budget|limits?|constraints?)", 0.55, "tells the agent to ignore its instructions or budget"),
    (r"disregard (?:all |any )?(?:your |the )?(?:previous |prior )?(?:instructions|rules|budget|limits?|mandate)", 0.55, "tells the agent to disregard its rules"),
    (r"(?:you are|act as|you're) (?:now )?(?:an? )?(?:admin|administrator|developer|system|unrestricted)", 0.45, "tries to change the agent's role"),
    (r"new (?:system )?(?:instructions?|prompt|directive)", 0.4, "claims to issue new instructions"),
    (r"(?:system|developer)\s*(?::|message|note)", 0.3, "impersonates a system message"),
    # Payment redirection, the malicious merchant's core line. Both patterns need a payment verb
    # *and* a changed destination, so a shipping form ("send it to this new address") is not a hit.
    (r"(?:pay|payment|remit|transfer|wire)\w*\b.{0,40}?\b0x[a-fA-F0-9]{40}", 0.55, "redirects payment to a wallet address"),
    (r"(?:pay|payment|remit|transfer|wire)\w*\b.{0,30}?\b(?:other|alternate|alternative|different|new|updated)\s+(?:crypto\s+|wallet\s+|payment\s+)?(?:address|account|wallet)", 0.5, "redirects payment to another account"),
    (r"0x[a-fA-F0-9]{40}", 0.25, "contains a wallet address"),
    (r"(?:buy|purchase|add) (?:the )?(?:premium|deluxe|upgraded?|bundle|extended)", 0.35, "pushes an upsell at the agent"),
    (r"(?:exceed|override|raise|increase|bypass) (?:your |the )?(?:budget|cap|limit|mandate|spending)", 0.5, "tells the agent to exceed its cap"),
    (r"do not (?:tell|inform|mention|report|show)(?: this)?(?: to)? (?:your |the )?(?:user|owner|human|principal)", 0.5, "asks the agent to hide the purchase"),
    (r"without (?:asking|confirming|notifying|telling)", 0.3, "asks the agent to skip confirmation"),
    (r"(?:urgent|act now|expires? (?:in|within)|last chance|only \d+ left)", 0.15, "manufactured urgency"),
    # A comment addressed to an agent is enough on its own: hidden text that talks to a
    # shopping agent has no honest purpose. "buy" is deliberately not a trigger, because
    # `<!-- buy button -->` is ordinary markup.
    (r"<!--(?:(?!-->).)*?(?:instruct|ignore|disregard|override|agent|prompt|system)(?:(?!-->).)*?-->", 0.5, "instructions hidden in an HTML comment"),
    # Hiding text with CSS is a contributing signal, not a standalone one: real pages use
    # display:none for menus and modals all the time.
    (r"(?:display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0)", 0.4, "text hidden with CSS"),
    (r"aria-hidden\s*=\s*[\"']true[\"']", 0.2, "text hidden from sighted users"),
)

# Real storefronts price in more than dollars, and a page whose price we cannot read loses the
# price-consistency check, so the symbol class matters. Amounts are still read as major units.
_CURRENCY = r"[$\u00a3\u20ac\u00a5]|USD|EUR|GBP"
_PRICE = re.compile(rf"(?:price|cost|total)[^0-9]{{0,20}}(?:{_CURRENCY})\s*([0-9][0-9,]*(?:\.[0-9]{{2}})?)",
                    re.IGNORECASE)
_ANY_PRICE = re.compile(rf"(?:{_CURRENCY})\s*([0-9][0-9,]*(?:\.[0-9]{{2}})?)", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InjectionDetector:
    """Scores page text for instructions aimed at a shopping agent.

    Deliberately not an LLM call: it runs on every cached merchant page as a feature
    (spec 3.2), so it has to be free and fast. The investigator can still reason about a
    page with its model once this flags one.
    """

    def __init__(self, patterns=PATTERNS, threshold: float = INJECTION_THRESHOLD):
        self.threshold = threshold
        self._compiled = [(re.compile(p, re.IGNORECASE | re.DOTALL), w, label) for p, w, label in patterns]

    def score(self, text: str) -> tuple[float, list[str]]:
        """(score in 0..1, what was found). Quoted snippets stay short and are data."""
        if not text:
            return 0.0, []
        total = 0.0
        findings: list[str] = []
        for pattern, weight, label in self._compiled:
            match = pattern.search(text)
            if match is None:
                continue
            total += weight
            findings.append(f"{label}: {_snippet(match.group(0))}")
        return min(1.0, round(total, 4)), findings

    def scan_text(self, url: str, text: str, *, merchant_id: str | None = None) -> PageScan:
        score, findings = self.score(text)
        return PageScan(
            url=url,
            merchant_id=merchant_id,
            injection_score=score,
            findings=findings,
            listed_price_cents=listed_price_cents(text),
            scanned_at=_now(),
        )


def _snippet(matched: str, limit: int = 90) -> str:
    flat = " ".join(matched.split())
    return f"\"{flat[:limit]}...\"" if len(flat) > limit else f"\"{flat}\""


def listed_price_cents(text: str) -> int | None:
    """The page's asking price, for the price-consistency check (spec 3.2)."""
    match = _PRICE.search(text) or _ANY_PRICE.search(text)
    if match is None:
        return None
    raw = match.group(1)
    # A comma before exactly two final digits, with no dot anywhere, is a decimal comma
    # ("19,99" is 19.99); otherwise it separates thousands ("1,299" is 1299).
    if "." not in raw and re.fullmatch(r"\d+,\d{2}", raw):
        raw = raw.replace(",", ".")
    try:
        return round(float(raw.replace(",", "")) * 100)
    except ValueError:
        return None


class FixturePageScanner:
    """Scans pages from a local directory. Used by the tests and the offline demo."""

    def __init__(self, directory: str | Path, detector: InjectionDetector | None = None):
        self.directory = Path(directory)
        self.detector = detector or InjectionDetector()

    def scan(self, url: str) -> PageScan:
        path = self._path_for(url)
        if path is None:
            return PageScan(url=url, injection_score=0.0, findings=["no fixture for this url"], scanned_at=_now())
        return self.detector.scan_text(url, path.read_text(), merchant_id=path.stem)

    def _path_for(self, url: str) -> Path | None:
        name = url.rstrip("/").rsplit("/", 1)[-1] or "index"
        for candidate in (name, f"{name}.html", f"{name}.txt", f"{name}.md"):
            path = self.directory / candidate
            if path.is_file():
                return path
        return None


class ApifyPageScanner:
    """Fetches a page through Apify, then scores it with `InjectionDetector`.

    This is what `scan_page` runs at M10. The fetch is the only part that differs from
    `FixturePageScanner`, and a failed fetch returns a zero-score scan with the reason in
    `findings` rather than raising, because it sits on the hot path of a payment decision.
    """

    def __init__(
        self,
        *,
        actor: str = DEFAULT_ACTOR,
        detector: InjectionDetector | None = None,
        max_pages: int = 1,
        fallback=None,
    ):
        self.actor = actor
        self.detector = detector or InjectionDetector()
        self.max_pages = max_pages
        self.fallback = fallback
        self._cache: dict[str, PageScan] = {}

    def scan(self, url: str) -> PageScan:
        if url in self._cache:
            return self._cache[url]
        try:
            text = self._fetch(url)
        except Exception as exc:
            logger.warning("Apify scrape of %s failed: %s", url, exc)
            if self.fallback is not None:
                return self.fallback.scan(url)
            return PageScan(url=url, injection_score=0.0, findings=[f"scrape failed: {exc}"], scanned_at=_now())
        scan = self.detector.scan_text(url, text)
        self._cache[url] = scan
        return scan

    def _fetch(self, url: str) -> str:
        """Page text via the Apify actor. Imported lazily so tests need no token."""
        from apify_runner import run_actor

        items = run_actor(self.actor, {"startUrls": [{"url": url}], "maxCrawlPages": self.max_pages}, max_items=self.max_pages)
        parts = []
        for item in items:
            for key in ("text", "markdown", "html", "body"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    parts.append(value)
                    break
        if not parts:
            raise RuntimeError("the actor returned no page text")
        return "\n\n".join(parts)


def make_page_scanner(fixtures: str | Path | None = None):
    """An Apify scanner when a token is set, otherwise the fixture scanner."""
    fixture_scanner = FixturePageScanner(fixtures) if fixtures else None
    if os.environ.get("APIFY_TOKEN"):
        return ApifyPageScanner(fallback=fixture_scanner)
    logger.info("APIFY_TOKEN is not set; page scans come from fixtures")
    return fixture_scanner or FixturePageScanner(Path(__file__).resolve().parent / "fixtures" / "pages")
