"""Run the engine on live threat intel (integrations/threat_intel.py, Apify) and show what the
watchlist made of each report: the extracted pattern, the watch rule, the intel attackers and their
first-contact catches. Real Bitdeer models when configured (the mock brains recognize only a few
keywords), one report per round, in a sandbox (memory store, simulated marketplace, no real money).

    uv run python scripts/run_live_intel.py --rounds 3 --seed 42 --out evidence/threat-intel/bitdeer-live
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, required=True, help="output path without extension")
    args = parser.parse_args()

    from engine.engine import FraudEngine
    from engine.entrypoints import _brain, _voters
    from engine.sim.marketplace import MarketplaceWorld
    from engine.store import MemoryStore
    from integrations.threat_intel import ApifyIntelSource

    source = ApifyIntelSource()
    brain = _brain()
    engine = FraudEngine(store=MemoryStore(), world=MarketplaceWorld(seed=args.seed), allowlist={"live-intel"}, seed=args.seed,
                         run_id=f"live-intel-{args.seed}", voters=_voters(), brain=brain, threat_intel=True, threat_source=source)
    start = time.time()
    for _ in range(args.rounds):
        m = engine.run_round(requested_by="live-intel")
        print(f"round {m.round}: FP {m.false_positive_rate:.1%}, cost ${m.total_cost_cents / 100:,.0f}", flush=True)
    spend = brain.spend()
    reports = [r["payload"] for r in engine.store.rows["threat_reports"]]
    items = {i.report.report_id: i for i in engine.latest_threat_intel(limit=args.rounds)}
    verdicts = {i.report.report_id: (getattr(i, "critic_verdict", None), getattr(i, "legit_rate", None))
                for i in engine.watchlist.items}
    consumed = [a for a in engine.store.rows["defender_actions"] if a["action"] == "threat_report"]
    rows = []
    for a in consumed:
        rid = a["reason"]
        item = items.get(rid)
        report = a["detail"]["report"]
        catches = [r["payload"] for r in engine.store.rows["attackers"] if r["source"] == "threat_intel"
                   and r["attacker_id"] in (item.attacker_ids if item else [])]
        rows.append({"round": a["round"], "report_id": rid, "title": report["title"], "source_url": report["source_url"],
                     "live": source.live, "pattern": item.pattern.model_dump(mode="json") if item and item.pattern else None,
                     "watch_rule_id": item.watch_rule_id if item else None, "watch_verdict": verdicts.get(rid, (None, None))[0],
                     "honest_fire_rate": verdicts.get(rid, (None, None))[1], "attacker_ids": item.attacker_ids if item else [],
                     "attempts": sum(p.get("attempts", 0) for p in catches), "caught": sum(p.get("caught", 0) for p in catches)})
    out = {"seed": args.seed, "rounds": args.rounds, "source_live": source.live, "minutes": round((time.time() - start) / 60, 1),
           "llm_calls": spend.get("calls", 0), "llm_tokens": spend.get("input_tokens", 0) + spend.get("output_tokens", 0),
           "llm_failures": len(brain.failures), "reports": rows, "n_reports_logged": len(reports)}
    lines = [f"Seed {args.seed}; {args.rounds} rounds; {'live Apify reports' if source.live else 'CORPUS FALLBACK'}; "
             f"{'Bitdeer' if type(brain.llm).__name__ != 'MockBrains' else 'mock'} brains; {out['llm_calls']} LLM calls, "
             f"{out['llm_tokens']:,} tokens, {out['llm_failures']} failed; {out['minutes']} min.", "",
             "| round | report | pattern (lane) | signals | watch rule | why not | intel attackers | first-contact caught |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        p = r["pattern"]
        lines.append(f"| {r['round']} | [{r['title'][:60]}]({r['source_url']}) | {p['name'][:50] + ' (' + p['lane'] + ')' if p else 'none extracted'} | "
                     f"{'; '.join(p['signals']) if p else '-'} | {r['watch_rule_id'] or '-'} | {'-' if r['watch_rule_id'] else (r['watch_verdict'] or '-')} | {len(r['attacker_ids'])} | "
                     f"{r['caught']}/{r['attempts']} |")
    table = "\n".join(lines) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(out, indent=1))
    args.out.with_suffix(".md").write_text(table)
    print(table)


if __name__ == "__main__":
    main()
