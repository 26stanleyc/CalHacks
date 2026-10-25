"""M10: a payment never waits on a live page scan."""

import threading
import time

from contracts.stubs import StubPageScanner
from engine.page_cache import NOT_SCANNED, PrescannedPages


class SlowScanner:
    """Stands in for Apify: each scan blocks until released."""

    def __init__(self):
        self.release = threading.Event()
        self.calls = 0

    def scan(self, url):
        self.calls += 1
        self.release.wait(5)
        return StubPageScanner().scan(url)


def _wait_for(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.01)
    return False


def test_an_unscanned_page_answers_at_once_and_is_scanned_in_the_background():
    scanner = SlowScanner()
    pages = PrescannedPages(scanner)
    url = "https://shop.example/poison-widget"
    started = time.perf_counter()
    first = pages.scan(url)
    assert time.perf_counter() - started < 0.5
    assert first.findings == [NOT_SCANNED]
    pages.scan(url)  # asking again doesn't queue a second scan
    scanner.release.set()
    assert _wait_for(lambda: pages.scan(url).injection_score > 0.5)
    assert scanner.calls == 1


def test_prescanned_pages_are_read_without_rescanning():
    scanner = SlowScanner()
    scanner.release.set()
    pages = PrescannedPages(scanner)
    pages.prescan(["https://a.example/poison", "https://b.example/clean"])
    assert pages.scan("https://a.example/poison").injection_score > 0.5
    assert pages.scan("https://b.example/clean").findings != [NOT_SCANNED]
    assert scanner.calls == 2


def test_scan_now_rescans_and_keeps_the_result():
    scanner = SlowScanner()
    scanner.release.set()
    pages = PrescannedPages(scanner)
    pages.scan_now("https://a.example/poison")
    pages.scan_now("https://a.example/poison")
    assert scanner.calls == 2
    assert pages.scan("https://a.example/poison").injection_score > 0.5
