// Live catch rate per shelf, computed from the payments themselves: the last few rounds plus the
// part of the current round that has played. The 3D tiles, the shelf map and the shelf card all
// read this, so they always agree and change as each coin is caught or gets through.

import type { Replay, ShelfId, ShelfState } from "../data/types";
import { getRoundModel } from "./roundModel";

export const RECENT_ROUNDS = 3; // the window a shelf's catch rate covers
const TRUST_AFTER = 20; // fewer attempts than this reads as "too early"

type Tally = { attempts: number; caught: number };

// Whole-round tallies, per round, per shelf. Cached per replay.
const roundCache = new WeakMap<Replay, Map<ShelfId, Tally>[]>();
function roundTallies(replay: Replay): Map<ShelfId, Tally>[] {
  let cached = roundCache.get(replay);
  if (cached) return cached;
  cached = replay.rounds.map((r) => {
    const shelfOf = new Map(r.attackers.map((a) => [a.attacker_id, a.shelf]));
    const out = new Map<ShelfId, Tally>();
    for (const w of r.waves)
      for (const p of w.payments) {
        const shelf = p.is_fraud && p.attacker_id ? shelfOf.get(p.attacker_id) : undefined;
        if (!shelf) continue;
        const t = out.get(shelf) ?? { attempts: 0, caught: 0 };
        t.attempts++;
        if (!p.settled) t.caught++;
        out.set(shelf, t);
      }
    return out;
  });
  roundCache.set(replay, cached);
  return cached;
}

// The current round's tallies so far: a coin counts once it's been zapped or has landed.
function partialTally(replay: Replay, round: number, t: number): Map<ShelfId, Tally> {
  const shelfOf = new Map(replay.rounds[round].attackers.map((a) => [a.attacker_id, a.shelf]));
  const out = new Map<ShelfId, Tally>();
  for (const c of getRoundModel(replay, round).coins) {
    if (!c.attack || !c.payment.attacker_id) continue;
    const caught = c.outcome === "blocked";
    if ((caught ? c.intercept : c.land) > t) continue;
    const shelf = shelfOf.get(c.payment.attacker_id);
    if (!shelf) continue;
    const tally = out.get(shelf) ?? { attempts: 0, caught: 0 };
    tally.attempts++;
    if (caught) tally.caught++;
    out.set(shelf, tally);
  }
  return out;
}

// Called every frame by the tiles, so the last result is reused until the clock moves on.
let last: { replay: Replay; round: number; t: number; result: Map<ShelfId, ShelfState> } | null = null;

export function liveShelves(replay: Replay, round: number, t: number): Map<ShelfId, ShelfState> {
  const tq = Math.floor(t * 10) / 10;
  if (last && last.replay === replay && last.round === round && last.t === tq) return last.result;
  const past = roundTallies(replay).slice(Math.max(0, round - (RECENT_ROUNDS - 1)), round);
  const now = partialTally(replay, round, tq);
  const result = new Map<ShelfId, ShelfState>();
  for (const s of replay.rounds[round].shelves) {
    let attempts = now.get(s.shelf)?.attempts ?? 0;
    let caught = now.get(s.shelf)?.caught ?? 0;
    for (const r of past) {
      attempts += r.get(s.shelf)?.attempts ?? 0;
      caught += r.get(s.shelf)?.caught ?? 0;
    }
    result.set(s.shelf, { ...s, attempts, caught, unknown: attempts < TRUST_AFTER });
  }
  last = { replay, round, t: tq, result };
  return result;
}
