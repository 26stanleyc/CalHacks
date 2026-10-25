"""Export a recorded run from the team database as the arena demo's replay (arena/src/data/types.ts).

    uv run python -m engine.export_replay h2h-real-42-202610241623 --out ../arena-viz/arena/public/replay.json

One query per table, as arena/README.md lays out. Defender actions are logged at the end of the
round that decided them, so they play at the start of the next round.
"""

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

LANES = ("timing", "graph", "identity", "velocity", "mandate")
QUESTIONS = {
    "timing": "Is this agent moving at a strange pace or rhythm?",
    "graph": "Is this account secretly linked to a ring of others?",
    "identity": "Is a real, accountable human behind this agent?",
    "velocity": "Are the amounts or frequency suspicious?",
    "mandate": "Is this payment what the human asked for?",
}
METRIC_KEYS = ("loss_merchant_side_cents", "loss_buyer_side_cents", "false_positive_rate", "exploitability_cents",
               "shelves_defended", "archive_coverage", "frozen")


def shelf_id(key: dict | None) -> str:
    return f"{key['style']}/{key['amount']}/{key['side']}" if key else ""


def plain_reason(reason: str) -> str:
    """The defender's own sentence, without the engine's budget bookkeeping tacked on the end, e.g.
    '... (its first choice was taken) (granted (2 points left))'."""
    for tail in ("(granted", "(over budget", "(denied", "(its first choice was taken)"):
        cut = reason.rfind(" " + tail)
        if cut > 0:
            reason = reason[:cut]
    reason = reason.strip()
    return reason[:1].upper() + reason[1:]


def db_tables(conn, run_id: str) -> dict:
    """The run's raw rows from the team database, one query per table."""
    def rows(sql: str, *args):
        return conn.execute(sql, args).fetchall()

    seed, defense = rows("select seed, defense from runs where run_id = %s", run_id)[0]
    return {
        "seed": seed, "defense": defense,
        "metrics": rows("select round, payload from round_metrics where run_id = %s order by round", run_id),
        "reports": rows("select report_id, payload, pattern, attacker_ids from threat_reports where report_id like %s",
                        f"{run_id}/%"),
        "attackers": rows("select round, payload from attackers where run_id = %s order by id", run_id),
        "actions": rows("select round, actor, action, granted, reason, detail from defender_actions "
                        "where run_id = %s order by id", run_id),
        "votes": rows("select v.tx_id, v.voter, v.payload from votes v join transactions t using (tx_id) "
                      "where t.run_id = %s", run_id),
        "payments": rows("select t.tx_id, t.round, t.wave, t.source, t.attacker_id, t.payload, d.action, d.escalated, "
                         "d.payload, g.is_fraud, s.settled from transactions t join decisions d using (tx_id) "
                         "left join ground_truth g using (tx_id) left join settlements s using (tx_id) "
                         "where t.run_id = %s order by (t.payload->>'ts')::float", run_id),
        "waves": rows("select attacker_id, round, wave, strategy_changed, trigger, playbook from playbook_waves "
                      "where run_id = %s order by id", run_id),
        "snapshots": rows("select round, shelf_id, payload from archive_snapshots where run_id = %s order by id", run_id),
        "versions": rows("select round, lane, rules, diff from rulebook_versions where run_id = %s and deployed "
                         "order by id", run_id),
    }


def memory_tables(store, run_id: str) -> dict:
    """The same rows from a MemoryStore (a run that never went to the database, e.g. a what-if run)."""
    run = store.runs[run_id]
    mine = lambda table: [r for r in store.rows[table] if r.get("run_id") == run_id]
    txs = {t.tx_id: t for t in store.transactions if t.run_id == run_id}
    truth = {g.tx_id: g.is_fraud for g in store.ground_truth}
    settled = {s.tx_id: s.settled for s in store.settlements}
    decisions = {d.tx_id: d for d in store.decisions if d.tx_id in txs}
    return {
        "seed": run["seed"], "defense": run["defense"],
        "metrics": [(m.round, m.model_dump(mode="json")) for m in store.metrics if m.run_id == run_id],
        "reports": [(r["report_id"], r["payload"], r.get("pattern"), r.get("attacker_ids"))
                    for r in store.rows["threat_reports"] if r["report_id"].startswith(f"{run_id}/")],
        "attackers": [(r["round"], r["payload"]) for r in mine("attackers")],
        "actions": [(r["round"], r["actor"], r["action"], r["granted"], r["reason"], r.get("detail") or {})
                    for r in mine("defender_actions")],
        "votes": [(v.tx_id, v.voter, v.model_dump(mode="json")) for v in store.votes if v.tx_id in txs],
        "payments": [(t.tx_id, t.round, t.wave, t.source.value, t.attacker_id, t.model_dump(mode="json"),
                      decisions[t.tx_id].action.value, decisions[t.tx_id].escalated,
                      decisions[t.tx_id].model_dump(mode="json"), truth.get(t.tx_id), settled.get(t.tx_id))
                     for t in sorted(txs.values(), key=lambda t: t.ts) if t.tx_id in decisions],
        "waves": [(r["attacker_id"], r["round"], r["wave"], r.get("strategy_changed", False), r.get("trigger"),
                   r["playbook"]) for r in mine("playbook_waves")],
        "snapshots": [(r["round"], r["shelf_id"], r["payload"]) for r in mine("archive_snapshots")],
        "versions": [(r["round"], r["lane"], r["rules"], r.get("diff")) for r in mine("rulebook_versions")
                     if r.get("deployed", True)],
    }


def export(conn, run_id: str) -> dict:
    return build(db_tables(conn, run_id), run_id)


def build(t: dict, run_id: str) -> dict:
    """The replay from a run's raw rows (db_tables or memory_tables)."""
    from contracts.config import model_for

    metrics = dict(t["metrics"])

    titles = {}  # threat-intel attacker id -> a human name from its report
    for _, payload, _, ids in t["reports"]:
        for k, aid in enumerate(ids or []):
            titles[aid] = payload.get("title", aid) + ("" if k == 0 else f" (variation {k})")

    attackers = defaultdict(dict)  # round -> attacker id -> its last row that round (an attacker can be logged twice)
    first_round: dict[str, int] = {}
    for rnd, p in t["attackers"]:
        first_round.setdefault(p["attacker_id"], rnd)
        pb = p["playbook"]
        attackers[rnd][p["attacker_id"]] = {
            "attacker_id": p["attacker_id"], "name": titles.get(p["attacker_id"], p["name"]), "source": p["source"],
            "model": p.get("model"), "parent": p.get("parent"), "lineage": p.get("lineage"),
            "generation": p.get("generation", 0), "shelf": shelf_id(pb.get("shelf")), "strategy": pb.get("strategy", ""),
            "params": pb.get("params", {}), "profit_cents": p.get("profit_cents", 0)}

    actions = defaultdict(list)
    for rnd, actor, action, granted, reason, detail in t["actions"]:
        if action not in ("spawn", "request_feature", "request_scan", "skip"):
            continue  # e.g. threat_report and pause rows
        entry = {"actor": actor, "action": action, "granted": granted, "reason": plain_reason(reason)}
        if detail.get("target_shelf"):
            entry["shelf"] = detail["target_shelf"]
        if detail.get("attacker_id"):
            entry["attacker_id"] = detail["attacker_id"]
        actions[rnd + 1].append(entry)  # decided at the end of a round, played at the start of the next

    news = defaultdict(list)
    for report_id, payload, pattern, ids in t["reports"]:
        if not ids or ids[0] not in first_round:
            continue
        news[first_round[ids[0]]].append({"report_id": report_id.split("/", 1)[1], "title": payload.get("title", ""),
                                          "shelf": shelf_id((pattern or {}).get("shelf")), "attacker_ids": ids})

    votes = defaultdict(list)
    for tx_id, voter, p in t["votes"]:
        if voter in LANES and p.get("vote") in ("step_up", "block"):
            votes[tx_id].append(voter)
    payments = defaultdict(list)
    for tx_id, rnd, wave, source, attacker_id, tx, action, escalated, decision, fraud, settled in t["payments"]:
        payments[(rnd, wave)].append({"tx_id": tx_id, "source": "legit" if source == "legit" else "attacker",
                                      "attacker_id": attacker_id, "amount_cents": tx["amount_cents"], "action": action,
                                      "settled": bool(settled), "is_fraud": bool(fraud),
                                      "objected_by": sorted(set(votes[tx_id])), "escalated": escalated,
                                      "explanation": decision.get("explanation", "")})

    retunes = defaultdict(list)
    for attacker_id, rnd, wave, changed, trigger, pb in t["waves"]:
        if changed or trigger:
            retunes[(rnd, wave)].append({"attacker_id": attacker_id, "strategy_changed": changed,
                                         "trigger": trigger or "", "params": pb.get("params", {})})

    shelves = defaultdict(list)
    for rnd, sid, p in t["snapshots"]:
        shelves[rnd].append({"shelf": sid, "attacker_ids": p.get("attacker_ids", []), "outsider_slot": p.get("outsider_slot"),
                             "attempts": p.get("window_attempts", p.get("attempts", 0)),
                             "caught": p.get("window_caught", p.get("caught", 0)), "unknown": bool(p.get("unknown"))})

    from engine.dsl import load_seed_rulebooks
    from engine.features import FeatureCatalog
    from engine.pipeline import ALL_LANES

    seeds = load_seed_rulebooks(FeatureCatalog(), ALL_LANES)
    live = {lane.value: [r.model_dump(mode="json") for r in book.rules] for lane, book in seeds.items()}  # deployed so far
    books = {}
    versions = defaultdict(list)
    for rnd, lane, rules, diff in t["versions"]:
        versions[rnd].append((lane, rules, diff or {}))
    for rnd in sorted(metrics):
        added, retired = defaultdict(int), defaultdict(int)
        for lane, rules, diff in versions.get(rnd, []):
            live[lane] = rules
            added[lane] += len(diff.get("added", [])) + len(diff.get("watch_added", []))
            retired[lane] += sum(len(diff.get(k, [])) for k in ("retired", "removed", "revet_removed", "fp_guard_removed",
                                                                 "watch_expired"))
        books[rnd] = [{"lane": lane, "rules": [r["id"] for r in live[lane] if not r.get("watch")],
                       "watch_rules": [r["id"] for r in live[lane] if r.get("watch")],
                       "added": added[lane], "retired": retired[lane]} for lane in LANES if lane in live]

    rounds = []
    for rnd in sorted(metrics):
        waves = sorted({w for (r, w) in payments if r == rnd})
        rounds.append({"round": rnd, "metrics": {k: metrics[rnd].get(k, 0) for k in METRIC_KEYS},
                       "attackers": list(attackers[rnd].values()), "actions": actions[rnd], "news": news[rnd],
                       "waves": [{"wave": w, "payments": payments[(rnd, w)], "retunes": retunes[(rnd, w)]} for w in waves],
                       "shelves": shelves[rnd], "rulebooks": books[rnd]})
    return {"run_id": run_id, "seed": t["seed"], "defense": t["defense"],
            "defenders": [{"lane": lane, "model": model_for(lane), "question": QUESTIONS[lane]} for lane in LANES],
            "rounds": rounds}


def main() -> None:
    import psycopg

    import integrations.settings  # noqa: F401  (loads .env)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_id")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        replay = export(conn, args.run_id)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(replay, separators=(",", ":")))
    n = sum(len(w["payments"]) for r in replay["rounds"] for w in r["waves"])
    print(f"{args.run_id}: {len(replay['rounds'])} rounds, {n} payments -> {args.out} ({args.out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
