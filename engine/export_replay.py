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


def export(conn, run_id: str) -> dict:
    from contracts.config import model_for

    def rows(sql: str, *args):
        return conn.execute(sql, args).fetchall()

    seed, defense = rows("select seed, defense from runs where run_id = %s", run_id)[0]
    metrics = {r: p for r, p in rows("select round, payload from round_metrics where run_id = %s order by round", run_id)}

    titles = {}  # threat-intel attacker id -> a human name from its report
    for payload, ids in rows("select payload, attacker_ids from threat_reports where report_id like %s", f"{run_id}/%"):
        for k, aid in enumerate(ids or []):
            titles[aid] = payload.get("title", aid) + ("" if k == 0 else f" (variation {k})")

    attackers = defaultdict(dict)  # round -> attacker id -> its last row that round (an attacker can be logged twice)
    first_round: dict[str, int] = {}
    for rnd, p in rows("select round, payload from attackers where run_id = %s order by id", run_id):
        first_round.setdefault(p["attacker_id"], rnd)
        pb = p["playbook"]
        attackers[rnd][p["attacker_id"]] = {
            "attacker_id": p["attacker_id"], "name": titles.get(p["attacker_id"], p["name"]), "source": p["source"],
            "model": p.get("model"), "parent": p.get("parent"), "lineage": p.get("lineage"),
            "generation": p.get("generation", 0), "shelf": shelf_id(pb.get("shelf")), "strategy": pb.get("strategy", ""),
            "params": pb.get("params", {}), "profit_cents": p.get("profit_cents", 0)}

    actions = defaultdict(list)
    for rnd, actor, action, granted, reason, detail in rows(
            "select round, actor, action, granted, reason, detail from defender_actions "
            "where run_id = %s and action in ('spawn', 'request_feature', 'request_scan', 'skip') order by id", run_id):
        entry = {"actor": actor, "action": action, "granted": granted, "reason": plain_reason(reason)}
        if detail.get("target_shelf"):
            entry["shelf"] = detail["target_shelf"]
        if detail.get("attacker_id"):
            entry["attacker_id"] = detail["attacker_id"]
        actions[rnd + 1].append(entry)  # decided at the end of a round, played at the start of the next

    news = defaultdict(list)
    for report_id, payload, pattern, ids in rows(
            "select report_id, payload, pattern, attacker_ids from threat_reports where report_id like %s", f"{run_id}/%"):
        if not ids or ids[0] not in first_round:
            continue
        news[first_round[ids[0]]].append({"report_id": report_id.split("/", 1)[1], "title": payload.get("title", ""),
                                          "shelf": shelf_id((pattern or {}).get("shelf")), "attacker_ids": ids})

    votes = defaultdict(list)
    for tx_id, voter, p in rows("select v.tx_id, v.voter, v.payload from votes v join transactions t using (tx_id) "
                                "where t.run_id = %s", run_id):
        if voter in LANES and p.get("vote") in ("step_up", "block"):
            votes[tx_id].append(voter)
    payments = defaultdict(list)
    for tx_id, rnd, wave, source, attacker_id, tx, action, escalated, decision, fraud, settled in rows(
            "select t.tx_id, t.round, t.wave, t.source, t.attacker_id, t.payload, d.action, d.escalated, d.payload, "
            "g.is_fraud, s.settled from transactions t join decisions d using (tx_id) "
            "left join ground_truth g using (tx_id) left join settlements s using (tx_id) "
            "where t.run_id = %s order by t.payload->>'ts'", run_id):
        payments[(rnd, wave)].append({"tx_id": tx_id, "source": "legit" if source == "legit" else "attacker",
                                      "attacker_id": attacker_id, "amount_cents": tx["amount_cents"], "action": action,
                                      "settled": bool(settled), "is_fraud": bool(fraud),
                                      "objected_by": sorted(set(votes[tx_id])), "escalated": escalated,
                                      "explanation": decision.get("explanation", "")})

    retunes = defaultdict(list)
    for attacker_id, rnd, wave, changed, trigger, pb in rows(
            "select attacker_id, round, wave, strategy_changed, trigger, playbook from playbook_waves "
            "where run_id = %s order by id", run_id):
        if changed or trigger:
            retunes[(rnd, wave)].append({"attacker_id": attacker_id, "strategy_changed": changed,
                                         "trigger": trigger or "", "params": pb.get("params", {})})

    shelves = defaultdict(list)
    for rnd, sid, p in rows("select round, shelf_id, payload from archive_snapshots where run_id = %s order by id", run_id):
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
    for rnd, lane, rules, diff in rows("select round, lane, rules, diff from rulebook_versions "
                                       "where run_id = %s and deployed order by id", run_id):
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
    return {"run_id": run_id, "seed": seed, "defense": defense,
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
