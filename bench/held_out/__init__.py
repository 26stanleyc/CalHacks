"""Held-out attackers for the frozen exam only (spec 4). Never use them for training.

    from bench.held_out import held_out_attackers
    for attacker in held_out_attackers(): ...
"""

from bench.held_out.attackers import HELD_OUT_CLASSES, HandWrittenAttacker, held_out_attackers

HELD_OUT_IDS: frozenset[str] = frozenset(cls.attacker_id for cls in HELD_OUT_CLASSES)

__all__ = ["HELD_OUT_CLASSES", "HELD_OUT_IDS", "HandWrittenAttacker", "held_out_attackers"]
