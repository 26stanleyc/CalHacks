"""The Apify threat-intel source: offline (the crawl is mocked), the contract, the fallback."""

from datetime import datetime, timezone

from contracts.conformance import check_threat_intel
from contracts.interfaces import ThreatIntelSource
from engine.intel.corpus import REPORTS, CorpusSource
from integrations.threat_intel import ApifyIntelSource, make_intel_source

NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)
ARTICLE = ("Scammers are draining shopping agents overnight. Victims report purchases of about $120 every 15 "
           "minutes at stores they have never used, until the account is emptied in the morning. ") * 4
START = "https://consumer.ftc.gov/consumer-alerts"


def crawl_items():
    return [
        {"url": START, "text": "Consumer alerts: scam news index. " * 40, "metadata": {"title": "Alerts"}},  # index: skipped
        {"url": "https://consumer.ftc.gov/alerts/agents-drained", "text": ARTICLE, "metadata": {"title": "Agents drained overnight"}},
        {"url": "https://consumer.ftc.gov/privacy", "text": "This privacy policy explains cookies. " * 20},  # not a scam report
        {"url": "https://consumer.ftc.gov/alerts/short", "text": "A scam. Fraud."},  # too short
        {"url": "https://www.ic3.gov/PSA/2026/mule", "text": "Money mule rings recruit victims to move stolen funds. " * 10,
         "metadata": {"title": "Money mules"}},
    ]


def source(items, **kw):
    s = ApifyIntelSource([START, "https://www.ic3.gov/PSA"], cache=None, **kw)
    s._crawl = lambda: [r for item in items if (r := __import__("integrations.threat_intel", fromlist=["_report_from"])
                        ._report_from(item, NOW)) is not None and item["url"] != START]
    return s


def test_satisfies_the_contract_and_serves_one_unseen_report_per_call():
    s = source(crawl_items())
    assert isinstance(s, ThreatIntelSource)
    check_threat_intel(source(crawl_items()))
    first, second, third = s.fetch_new(), s.fetch_new(), s.fetch_new()
    assert s.live and [r.title for r in first + second] == ["Agents drained overnight", "Money mules"] and third == []
    assert first[0].report_id.startswith("apify-") and first[0].source_url.startswith("https://")


def test_keeps_only_article_pages_that_read_like_scam_reports():
    s = source(crawl_items())
    s.fetch_new()
    urls = {r.source_url for r in s.reports}
    assert START not in urls and "https://consumer.ftc.gov/privacy" not in urls and "https://consumer.ftc.gov/alerts/short" not in urls
    assert all(len(r.text) <= 2000 and "\n" not in r.text for r in s.reports)


def test_falls_back_to_the_corpus_when_the_crawl_fails_or_finds_nothing():
    broken = ApifyIntelSource(cache=None)
    broken._crawl = lambda: (_ for _ in ()).throw(RuntimeError("actor timed out"))
    assert broken.fetch_new() == [REPORTS[0]] and not broken.live
    empty = ApifyIntelSource(cache=None)
    empty._crawl = lambda: []
    assert empty.fetch_new() == [REPORTS[0]] and not empty.live
    assert REPORTS[0].report_id in empty.seen  # the restart logic sees the same `seen` set either way


def test_make_intel_source_picks_by_token(monkeypatch):
    assert isinstance(make_intel_source(), CorpusSource)  # conftest clears APIFY_TOKEN
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    assert isinstance(make_intel_source(), ApifyIntelSource)
