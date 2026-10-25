"""Threat intel over Apify (spec 3.8): public scam reports scraped from consumer-protection and
law-enforcement alert pages, served through `contracts.interfaces.ThreatIntelSource` so the
watchlist (engine/intel/watchlist.py) reads them exactly as it reads the pre-scraped corpus.

- The crawl starts at a few public alert index pages (APIFY_INTEL_URLS overrides them) and follows
  their article links one level down with `apify/website-content-crawler`. Every article page
  that reads like a scam report becomes one ThreatReport; the index pages themselves don't.
- Page text is untrusted: it's capped, whitespace-normalized and only ever passed to models as
  quoted data (the extractor prompt says so, and a test checks that an injection in a report
  doesn't change the answer format).
- Scraped reports are cached outside the repo (`~/calhacks-sandbox/threat-intel/`, like PaySim) for
  a day, so repeated runs don't pay for the same crawl, and nothing scraped is ever committed.
- No token, a failed crawl, or a crawl that finds nothing usable falls back to the corpus, with a
  warning, so an offline run behaves as before.

Caveat for the held-out exam: two corpus reports are withheld to build exam attackers. A live crawl
can surface the same real-world patterns (gift-card cash-out, replayed mandates). Compare exam
numbers across runs that use the same source.

    uv run python -m integrations.threat_intel          # live crawl: prints what it fetched
    uv run python -m integrations.threat_intel --refresh
"""

import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contracts.schemas import ThreatReport
from engine.intel.corpus import CorpusSource

logger = logging.getLogger(__name__)

DEFAULT_ACTOR = os.environ.get("APIFY_INTEL_ACTOR", "apify/website-content-crawler")
DEFAULT_URLS = (
    "https://consumer.ftc.gov/consumer-alerts",  # FTC consumer alerts
    "https://www.ic3.gov/PSA",  # FBI IC3 public service announcements
)
DEFAULT_CACHE = Path("~/calhacks-sandbox/threat-intel/reports.json").expanduser()
MAX_PAGES = int(os.environ.get("APIFY_INTEL_MAX_PAGES", "30"))
CACHE_MAX_AGE = timedelta(hours=24)
MAX_TEXT = 2000  # characters of page text kept per report (the extractor prompt quotes it whole)
MIN_TEXT = 300  # shorter pages are navigation, cookie banners or stubs
RELEVANT = re.compile(r"\b(scam|fraud|phish|stolen|victim|impersonat|chargeback|mule)", re.IGNORECASE)


def _urls() -> list[str]:
    raw = os.environ.get("APIFY_INTEL_URLS", "")
    return [u.strip() for u in raw.split(",") if u.strip()] or list(DEFAULT_URLS)


def _report_from(item: dict, fetched: datetime) -> ThreatReport | None:
    url = str(item.get("url") or "")
    text = ""
    for key in ("text", "markdown"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            text = value
            break
    text = re.sub(r"\s+", " ", text).strip()
    if not url or len(text) < MIN_TEXT or not RELEVANT.search(text):
        return None
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    title = str(meta.get("title") or item.get("title") or url).strip()[:200]
    return ThreatReport(report_id="apify-" + hashlib.blake2b(url.encode(), digest_size=6).hexdigest(),
                        source_url=url, title=title, text=text[:MAX_TEXT], fetched=fetched)


class ApifyIntelSource:
    """contracts.interfaces.ThreatIntelSource over a live Apify crawl: one unseen report per call,
    like CorpusSource, so the watchlist's one-report-a-round pacing and restart logic are unchanged."""

    def __init__(self, urls: list[str] | None = None, *, actor: str = DEFAULT_ACTOR, max_pages: int = MAX_PAGES,
                 cache: Path | None = DEFAULT_CACHE, refresh: bool = False, fallback: CorpusSource | None = None):
        self.urls = list(urls or _urls())
        self.actor = actor
        self.max_pages = max_pages
        self.cache = cache
        self.refresh = refresh
        self.fallback = fallback if fallback is not None else CorpusSource()
        self.reports: list[ThreatReport] | None = None  # None until the first fetch
        self.seen: set[str] = set()
        self.live = False  # True once the reports served come from a crawl (cached or fresh)

    def fetch_new(self, since: datetime | None = None) -> list[ThreatReport]:
        if self.reports is None:
            self.reports = self._load()
        for report in self.reports:
            if report.report_id not in self.seen and (since is None or report.fetched >= since):
                self.seen.add(report.report_id)
                return [report]
        return []

    def _load(self) -> list[ThreatReport]:
        try:
            reports = self._cached() if not self.refresh else None
            if reports is None:
                reports = self._crawl()
                self._store(reports)
            if not reports:
                raise RuntimeError("the crawl found no usable scam reports")
            self.live = True
            return reports
        except Exception as exc:
            logger.warning("Apify threat intel unavailable (%s); using the pre-scraped corpus", exc)
            self.fallback.seen = self.seen
            return list(self.fallback.reports)

    def _crawl(self) -> list[ThreatReport]:
        """Imported lazily so tests need no token."""
        from apify_runner import run_actor

        fetched = datetime.now(timezone.utc)
        starts = {u.rstrip("/") for u in self.urls}
        reports, crawled = [], 0
        for url in self.urls:  # one crawl per start page, each with its own page budget, so a busy site can't crowd out another
            items = run_actor(self.actor, {"startUrls": [{"url": url}], "maxCrawlPages": self.max_pages,
                                           "maxCrawlDepth": 1, "crawlerType": "cheerio"}, max_items=self.max_pages)
            crawled += len(items)
            reports += [r for item in items if str(item.get("url") or "").rstrip("/") not in starts
                        for r in [_report_from(item, fetched)] if r is not None]
        logger.info("Apify threat intel: %d pages crawled, %d scam reports kept", crawled, len(reports))
        return reports

    def _cached(self) -> list[ThreatReport] | None:
        if self.cache is None or not self.cache.exists():
            return None
        raw = json.loads(self.cache.read_text())
        if raw.get("urls") != self.urls:
            return None
        reports = [ThreatReport.model_validate(r) for r in raw.get("reports", [])]
        if not reports or datetime.now(timezone.utc) - reports[0].fetched > CACHE_MAX_AGE:
            return None
        return reports

    def _store(self, reports: list[ThreatReport]) -> None:
        if self.cache is None or not reports:
            return
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text(json.dumps({"urls": self.urls, "reports": [r.model_dump(mode="json") for r in reports]}, indent=1))


def make_intel_source():
    """An Apify source when a token is set, otherwise the pre-scraped corpus."""
    if os.environ.get("APIFY_TOKEN"):
        return ApifyIntelSource()
    logger.info("APIFY_TOKEN is not set; threat intel comes from the pre-scraped corpus")
    return CorpusSource()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    source = ApifyIntelSource(refresh="--refresh" in sys.argv)
    source.fetch_new()
    print(f"{'live crawl' if source.live else 'CORPUS FALLBACK'}: {len(source.reports)} reports from {source.urls}")
    for r in source.reports:
        print(f"- [{r.report_id}] {r.title[:70]!r} ({len(r.text)} chars) {r.source_url}")
