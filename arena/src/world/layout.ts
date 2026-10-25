// Where everything sits on the hex board. Ring 0 is the x402 gate, ring 1 the five
// specialists plus the orchestrator, rings 2–3 the battlefield, ring 4 the 24 arena shelves
// (attacks on stores on one half, attacks on shoppers on the other) and ring 5 the beach.

import { ALL_SHELVES, LANES } from "../data/mock";
import type { Actor, ShelfId } from "../data/types";

export const HEX = 1; // centre-to-corner
const SQ3 = Math.sqrt(3);

export type Vec3 = [number, number, number];
export interface Tile {
  q: number;
  r: number;
  x: number;
  z: number;
  ring: number;
}

const toWorld = (q: number, r: number): [number, number] => [HEX * SQ3 * (q + r / 2), HEX * 1.5 * r];
const DIRS: [number, number][] = [[1, 0], [1, -1], [0, -1], [-1, 0], [-1, 1], [0, 1]];

export function ring(k: number): Tile[] {
  if (k === 0) return [{ q: 0, r: 0, x: 0, z: 0, ring: 0 }];
  const out: Tile[] = [];
  let q = DIRS[4][0] * k;
  let r = DIRS[4][1] * k;
  for (let side = 0; side < 6; side++) {
    for (let step = 0; step < k; step++) {
      const [x, z] = toWorld(q, r);
      out.push({ q, r, x, z, ring: k });
      q += DIRS[side][0];
      r += DIRS[side][1];
    }
  }
  return out;
}

// Tile heights: the gate and shelves stand on raised plinths so they read from above.
export const TILE_TOP: Record<number, number> = { 0: 0.5, 1: 0.32, 2: 0.18, 3: 0.18, 4: 0.36, 5: 0.06 };

export const GATE: Vec3 = [0, TILE_TOP[0], 0];

const ring1 = ring(1);
export const ACTORS: Actor[] = [...LANES, "orchestrator"];
export const ACTOR_TILE: Record<Actor, Tile> = Object.fromEntries(
  ACTORS.map((a, i) => [a, ring1[i]]),
) as Record<Actor, Tile>;
export const actorPos = (a: Actor): Vec3 => [ACTOR_TILE[a].x, TILE_TOP[1], ACTOR_TILE[a].z];

const ring4 = ring(4);
export const SHELF_TILE: Record<ShelfId, Tile> = Object.fromEntries(
  ALL_SHELVES.map((id, i) => [id, ring4[i]]),
);

// Up to seven creatures share a shelf tile: the outsider in the centre, the rest in a ring.
const SLOT_OFFSETS: [number, number][] = [
  [0, 0],
  ...[0, 1, 2, 3, 4, 5].map((k): [number, number] => [0.52 * Math.cos(Math.PI / 6 + (k * Math.PI) / 3), 0.52 * Math.sin(Math.PI / 6 + (k * Math.PI) / 3)]),
];
// Slot 0 is the middle of the tile; slots 1.. stand round it. Up to six use the fixed spots; a
// crowded shelf spreads its ring evenly instead.
export function shelfSlot(id: ShelfId, slot: number, ringCount: number): Vec3 {
  const t = SHELF_TILE[id];
  if (ringCount < SLOT_OFFSETS.length || slot === 0) {
    const [dx, dz] = SLOT_OFFSETS[slot % SLOT_OFFSETS.length];
    return [t.x + dx, TILE_TOP[4], t.z + dz];
  }
  const a = Math.PI / 6 + ((slot - 1) * 2 * Math.PI) / ringCount;
  return [t.x + 0.58 * Math.cos(a), TILE_TOP[4], t.z + 0.58 * Math.sin(a)];
}

// Honest shoppers stroll on fixed battlefield tiles.
const field = [...ring(2), ...ring(3)];
export const SHOPPER_SPOTS: Vec3[] = field
  .filter((_, i) => i % 3 === 1)
  .map((t) => [t.x, TILE_TOP[3], t.z]);
