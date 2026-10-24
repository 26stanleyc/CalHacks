"""Lane-restricted reflection (spec 4.6, Reflexion-style): each specialist looks at the fraud
assigned to it that got through and at its own false alarms, then proposes rules to add or remove.
It only ever sees its own lane's features, and every rule it writes is validated against them.

The mock answer is a small rule inducer: thresholds on one lane feature (or a pair) that sit
outside the honest range and would have caught the assigned misses. The critic and the replay
then decide what's deployed, exactly as for a model-written rule.
"""

import re

from pydantic import BaseModel, Field

from engine.brains import mock_answer

MAX_RULES_PER_REFLECTION = 2
LEARNED_RULE_SCORE = 0.75
LEARNED_PAIR_SCORE = 0.7


class RuleSpec(BaseModel):
    id: str
    when: dict
    score: float
    description: str = ""


class ReflectionProposal(BaseModel):
    add: list[RuleSpec] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)  # rule ids
    notes: str = ""


INSTRUCTIONS = """You are the {lane} specialist in a fraud defense. You see only your own lane's features.
Below are the fraud payments assigned to you that got through, your own false alarms on honest
payments, your current rules with how often each fired on fraud and on honest payments this round,
and the honest reference range of each of your features.

Propose at most {max_rules} new rules that would catch the missed fraud without firing on honest
payments, and list the ids of rules to remove (rules that mostly fire on honest payments). Each
feature's reference says which side of it looks like fraud ("suspicious_when"); only write rules in
that direction, never the other way. Rules:
- id: "{lane}.<short_name>" (lowercase letters, digits, underscores)
- when: {{"feature": <one of your features>, "op": ">=" | ">" | "<=" | "<" | "==" | "!=", "value": <number>}},
  or {{"all": [...]}} / {{"any": [...]}} / {{"not": ...}} of those, at most 8 comparisons
- score: 0 to 1, how sure the rule is (0.6 or more makes your vote a block)
- description: one plain sentence
You have {room} free rule slots of {cap}; to add more, remove rules first."""


def _short(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", name.split("_", 1)[-1].lower())[:24]


HONEST_SHARE_FOR_RULE = 0.001  # a learned single-feature rule may fire on at most 0.1% of honest payments
HONEST_SHARE_FOR_PAIR = 0.05  # each half of a two-feature rule


def _threshold(misses: list[dict], name: str, high: bool, max_share: float) -> tuple[float, frozenset] | None:
    """The least extreme miss value whose honest share beyond it is under max_share, and the misses
    at or beyond it."""
    share_key = "honest_at_or_above" if high else "honest_at_or_below"
    ok = [m[name]["value"] for m in misses if name in m and m[name][share_key] <= max_share]
    if not ok:
        return None
    t = min(ok) if high else max(ok)
    hits = frozenset(i for i, m in enumerate(misses) if name in m and (m[name]["value"] >= t if high else m[name]["value"] <= t))
    return t, hits


@mock_answer(ReflectionProposal)
def _mock_reflect(data: dict) -> ReflectionProposal:
    lane, misses, reference = data["lane"], data["misses"], data["reference"]
    existing = {repr(sorted(r["when"].items())) for r in data["rules"]}
    remove = [r["id"] for r in data["rules"] if r["false_hits"] >= 2 and r["false_hits"] > r["true_hits"]]
    room = data["room"] + len(remove)
    if not misses or room <= 0:
        return ReflectionProposal(remove=remove, notes="mock: nothing to learn")
    singles = []
    for name, stats in reference.items():
        side = stats.get("suspicious_when", "either")
        for high in (True, False):
            if side == ("low" if high else "high"):
                continue
            found = _threshold(misses, name, high, HONEST_SHARE_FOR_RULE)
            if found:
                t, hits = found
                singles.append((len(hits), {"feature": name, "op": ">=" if high else "<=", "value": t}, name,
                                "high" if high else "low"))
    singles.sort(key=lambda s: (-s[0], s[2], s[3]))
    add: list[RuleSpec] = []
    for hits, cond, name, side in singles:
        if len(add) >= min(room, MAX_RULES_PER_REFLECTION) or hits < 2:
            break
        if repr(sorted(cond.items())) in existing:
            continue
        add.append(RuleSpec(id=f"{lane}.r{data['round']}_{_short(name)}_{side}", when=cond, score=LEARNED_RULE_SCORE,
                            description=f"Learned in round {data['round']}: {name} {cond['op']} {cond['value']:g}, "
                                        f"rarer than {HONEST_SHARE_FOR_RULE:.1%} of honest payments."))
    if len(add) < min(room, MAX_RULES_PER_REFLECTION):
        pair = _best_pair(misses, reference)
        if pair is not None:
            hits, conds = pair
            names = "_".join(_short(c["feature"])[:10] for c in conds)
            add.append(RuleSpec(id=f"{lane}.r{data['round']}_{names}_pair", when={"all": conds}, score=LEARNED_PAIR_SCORE,
                                description=f"Learned in round {data['round']}: two features both unusual for honest payments."))
    return ReflectionProposal(add=add, remove=remove, notes=f"mock: {len(add)} rule(s) from {len(misses)} misses")


def _best_pair(misses: list[dict], reference: dict) -> tuple[int, list[dict]] | None:
    """Two features where the same misses each sit in the honest 5% tail, on the suspicious side."""
    tails = {}
    for name, stats in reference.items():
        side = stats.get("suspicious_when", "either")
        high = side != "low"
        found = _threshold(misses, name, high, HONEST_SHARE_FOR_PAIR)
        if found and len(found[1]) >= 2:
            tails[name] = (found[0], found[1], high)
    best = None
    names = sorted(tails)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            both = tails[a][1] & tails[b][1]
            if len(both) >= 3 and (best is None or len(both) > best[0]):
                conds = [{"feature": n, "op": ">=" if tails[n][2] else "<=", "value": tails[n][0]} for n in (a, b)]
                best = (len(both), conds)
    return best
