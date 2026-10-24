"""Rule DSL (spec 3.3): rulebooks are JSON predicates over a lane's features, so LLM-written rules
are data that gets validated and evaluated, never code that gets executed.

A condition is one of:
    {"feature": "v_small_count_agent_600s", "op": ">=", "value": 5}
    {"all": [condition, ...]}   {"any": [condition, ...]}   {"not": condition}

A rulebook's score is the noisy-OR of the scores of the rules that fire: 1 - prod(1 - score_i).
"""

import json
import math
import operator
from pathlib import Path

from pydantic import BaseModel, Field

from contracts.schemas import Lane
from engine.features import FeatureCatalog

MAX_RULES = 15
MAX_DEPTH = 4
MAX_LEAVES = 8
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq, "!=": operator.ne}


class RuleError(ValueError):
    """A rule or rulebook that must not be deployed."""


class Rule(BaseModel):
    id: str = Field(pattern=r"^[a-z]+\.[a-z0-9_]+$")  # "<lane>.<name>"
    when: dict
    score: float = Field(gt=0.0, le=1.0)
    description: str = ""
    watch: bool = False  # threat-intel watch rule: raises the score but never hard-blocks (spec 3.8)


class Rulebook(BaseModel):
    lane: Lane
    version: int = 0
    rules: list[Rule] = Field(default_factory=list)


def condition_features(cond: dict, depth: int = 0) -> list[str]:
    """Validate a condition's structure and return the features it reads."""
    if not isinstance(cond, dict) or len(cond) == 0:
        raise RuleError("a condition must be a non-empty object")
    if depth > MAX_DEPTH:
        raise RuleError(f"conditions nest at most {MAX_DEPTH} levels")
    if "feature" in cond:
        if set(cond) != {"feature", "op", "value"}:
            raise RuleError(f"a comparison needs exactly feature, op and value: {cond}")
        if not isinstance(cond["op"], str) or cond["op"] not in OPS:
            raise RuleError(f"unknown operator {cond['op']!r}")
        value = cond["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuleError(f"value must be a finite number: {cond}")
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite or abs(value) > 1e15:
            raise RuleError(f"value must be a finite number: {cond}")
        if not isinstance(cond["feature"], str):
            raise RuleError("feature must be a name")
        return [cond["feature"]]
    if len(cond) != 1:
        raise RuleError(f"a group has exactly one key (all, any or not): {cond}")
    (key, inner), = cond.items()
    if key == "not":
        return condition_features(inner, depth + 1)
    if key in ("all", "any"):
        if not isinstance(inner, list) or not inner:
            raise RuleError(f"{key} needs a non-empty list")
        return [f for c in inner for f in condition_features(c, depth + 1)]
    raise RuleError(f"unknown condition key {key!r}")


def validate_rule(rule: Rule, lane: Lane, catalog: FeatureCatalog) -> None:
    if not rule.id.startswith(f"{lane.value}."):
        raise RuleError(f"rule {rule.id!r} must be named {lane.value}.<name>")
    features = condition_features(rule.when)
    if len(features) > MAX_LEAVES:
        raise RuleError(f"rule {rule.id!r} has more than {MAX_LEAVES} comparisons")
    for name in features:
        owner = catalog.owner(name)
        if owner is None:
            raise RuleError(f"rule {rule.id!r} uses unknown feature {name!r}")
        if owner != lane:
            raise RuleError(f"rule {rule.id!r} uses {name!r}, which belongs to the {owner.value} lane, not {lane.value}")


MAX_WATCH_RULES = 3  # threat-intel watch rules, separate from the rule cap (spec 3.8)


def regular_rules(rulebook: Rulebook) -> int:
    return sum(not r.watch for r in rulebook.rules)


def validate_rulebook(rulebook: Rulebook, catalog: FeatureCatalog) -> None:
    if regular_rules(rulebook) > MAX_RULES:
        raise RuleError(f"{rulebook.lane.value} rulebook has {regular_rules(rulebook)} rules; the cap is {MAX_RULES}")
    if len(rulebook.rules) - regular_rules(rulebook) > MAX_WATCH_RULES:
        raise RuleError(f"{rulebook.lane.value} rulebook has more than {MAX_WATCH_RULES} watch rules")
    ids = [r.id for r in rulebook.rules]
    if len(ids) != len(set(ids)):
        raise RuleError("rule ids must be unique")
    for rule in rulebook.rules:
        validate_rule(rule, rulebook.lane, catalog)


def holds(cond: dict, values: dict[str, float]) -> bool:
    if "feature" in cond:
        value = values.get(cond["feature"])
        return value is not None and OPS[cond["op"]](value, cond["value"])
    (key, inner), = cond.items()
    if key == "not":  # a missing feature never makes a rule fire, negated or not
        return all(f in values for f in condition_features(inner)) and not holds(inner, values)
    if key == "all":
        return all(holds(c, values) for c in inner)
    return any(holds(c, values) for c in inner)


def evaluate(rulebook: Rulebook, values: dict[str, float]) -> tuple[float, list[str]]:
    """Returns (score, ids of the rules that fired)."""
    fired = [r for r in rulebook.rules if holds(r.when, values)]
    miss = 1.0
    for rule in fired:
        miss *= 1.0 - rule.score
    return 1.0 - miss, [r.id for r in fired]


SEED_DIR = Path(__file__).resolve().parent / "rulebooks"


def load_rulebook(path: Path, catalog: FeatureCatalog) -> Rulebook:
    rulebook = Rulebook.model_validate(json.loads(path.read_text()))
    validate_rulebook(rulebook, catalog)
    return rulebook


def load_seed_rulebooks(catalog: FeatureCatalog, lanes: tuple[Lane, ...]) -> dict[Lane, Rulebook]:
    return {lane: load_rulebook(SEED_DIR / f"{lane.value}.json", catalog) for lane in lanes}
