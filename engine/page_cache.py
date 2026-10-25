"""Page scans off the payment path (M10).

A live Apify scan takes about 20 s, far over the investigator's case budget, so a payment only
ever reads a scan taken earlier. `prescan` scans merchant pages up front. A page nobody has
scanned yet reads as "not scanned yet" and is scanned in the background, so later payments see
the real score. The Merchant Scanner chat agent asks for a fresh scan with `scan_now`.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from contracts.interfaces import PageScanner
from contracts.schemas import PageScan

NOT_SCANNED = "not scanned yet; a scan is queued"


class PrescannedPages:
    def __init__(self, scanner: PageScanner, workers: int = 4):
        self.scanner = scanner
        self._scans: dict[str, PageScan] = {}
        self._queued: set[str] = set()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="page-scan")

    def scan(self, url: str) -> PageScan:
        """The kept scan, without waiting. This is what payments use."""
        with self._lock:
            if url in self._scans:
                return self._scans[url]
            queue = url not in self._queued
            self._queued.add(url)
        if queue:
            self._pool.submit(self._scan_and_unqueue, url)
        return PageScan(url=url, injection_score=0.0, findings=[NOT_SCANNED], scanned_at=datetime.now(timezone.utc))

    def scan_now(self, url: str) -> PageScan:
        """A fresh scan, waited for and kept. For prescans and the chat agent, never a payment."""
        scan = self.scanner.scan(url)
        with self._lock:
            self._scans[url] = scan
        return scan

    def keep(self, scans) -> None:
        """Keep scans taken elsewhere (the round's page priming), replacing older ones."""
        with self._lock:
            self._scans.update((scan.url, scan) for scan in scans)

    def prescan(self, urls) -> None:
        """Scans every page in parallel and waits for all of them."""
        list(self._pool.map(self.scan_now, set(urls)))

    def _scan_and_unqueue(self, url: str) -> None:
        try:
            self.scan_now(url)
        finally:
            with self._lock:
                self._queued.discard(url)
