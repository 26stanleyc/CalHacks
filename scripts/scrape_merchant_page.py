"""Scan a real merchant page with the live Apify scraper (M10's Apify use).

    uv run python scripts/scrape_merchant_page.py <url> [more urls...]

Runs the real `ApifyPageScanner`: an Apify actor fetches the page, and the same
`InjectionDetector` that scores the simulation's pages scores the text that comes back. Writes
`evidence/m10/apify-scrape.json` and the scraped text, so the run is checkable afterwards.

The default URL is a public practice bookstore (books.toscrape.com), which exists to be scraped.
Point it at any store page you want to check instead. Clean pages are expected to score 0: this
proves the fetch path works end to end, not that the page is malicious.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere

from integrations.page_scanner import INJECTION_THRESHOLD, ApifyPageScanner, InjectionDetector  # noqa: E402

EVIDENCE = Path(__file__).resolve().parent.parent / "evidence" / "m10"
DEFAULT_URLS = ("https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan real merchant pages through Apify")
    parser.add_argument("urls", nargs="*", default=list(DEFAULT_URLS))
    parser.add_argument("--save-text", action="store_true", default=True,
                        help="also save the scraped page text next to the JSON")
    args = parser.parse_args()
    urls = args.urls or list(DEFAULT_URLS)

    detector = InjectionDetector()
    scanner = ApifyPageScanner(detector=detector)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    results, failures = [], 0
    for url in urls:
        print(f"scraping {url} ...", flush=True)
        try:
            text = scanner._fetch(url)
        except Exception as exc:
            print(f"  FAILED: {exc}")
            results.append({"url": url, "ok": False, "error": str(exc)})
            failures += 1
            continue
        scan = detector.scan_text(url, text)
        verdict = "FLAGGED" if scan.injection_score >= INJECTION_THRESHOLD else "clean"
        print(f"  ok: {len(text):,} chars, injection_score={scan.injection_score:.2f} ({verdict}), "
              f"listed_price={scan.listed_price_cents}")
        for finding in scan.findings:
            print(f"    - {finding}")
        results.append({"url": url, "ok": True, "chars": len(text), "verdict": verdict,
                        "scan": scan.model_dump(mode="json")})
        if args.save_text:
            name = "".join(c if c.isalnum() else "-" for c in url.split("//", 1)[-1])[:80]
            path = EVIDENCE / f"scraped-{name}.txt"
            path.write_text(text)
            print(f"    saved page text to {path}")

    payload = {"ran_at": datetime.now(timezone.utc).isoformat(), "actor": scanner.actor,
               "threshold": INJECTION_THRESHOLD, "results": results}
    out = EVIDENCE / "apify-scrape.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {out}")
    return 1 if failures or not results else 0


if __name__ == "__main__":
    sys.exit(main())
