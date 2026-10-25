// Turns one RoundReplay into timed things to draw: where each devil stands and when it
// appears or leaves, each payment's flight, spawn beams and news drops. Everything is a
// function of the round-local clock, so scrubbing and replay speed come for free.

import type { Actor, AttackerInfo, Payment, Replay, ShelfId } from "../data/types";
import { FLIGHT, LEARN_START, THROW_WINDOW, waveStart } from "../store";
import { GATE, SHOPPER_SPOTS, shelfSlot, type Vec3 } from "./layout";

export interface DevilPlacement {
  attacker: AttackerInfo;
  pos: Vec3;
  appear: number; // round-clock seconds when it pops in (0 = already there)
  leave: number | null; // evicted from its shelf during learning
  retunes: { at: number; changed: boolean }[];
  idle: boolean; // on its shelf but not drawn to attack this round
}

export type Outcome = "blocked" | "stolen" | "honest" | "false_alarm";
export interface CoinFlight {
  payment: Payment;
  from: Vec3;
  launch: number;
  intercept: number; // when a blocked coin is zapped
  land: number;
  outcome: Outcome;
  zappers: Actor[]; // who fires at a stopped coin
  attack: boolean;
  drawn: boolean; // only a readable sample of coins is drawn; all of them count
}

export interface SpawnBeam {
  actor: Actor;
  to: Vec3;
  attackerId: string;
}

export interface NewsFall {
  title: string;
  to: Vec3;
}

export interface RoundModel {
  devils: DevilPlacement[];
  slots: Map<string, number>; // each devil's spot on its shelf, carried into the next round
  coins: CoinFlight[];
  beams: SpawnBeam[];
  news: NewsFall[];
}

// The latest record of an attacker up to this round, even if it didn't run in it.
export function latestAttacker(replay: Replay, index: number, id: string): { info: AttackerInfo; ran: boolean } | null {
  for (let i = index; i >= 0; i--) {
    const a = replay.rounds[i].attackers.find((x) => x.attacker_id === id);
    if (a) return { info: a, ran: i === index };
  }
  return null;
}

export const SPAWN_POP = 1.6; // when spawned devils hatch
export const INTERCEPT_FRAC = 0.62;
const MAX_DRAWN_ATTACK_COINS = 40;
// Real runs have ~100 honest payments a wave; draw a sample, but always draw false alarms.
const MAX_DRAWN_HONEST_COINS = 24;

const hash = (s: string) => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return (h >>> 0) / 4294967296;
};

// Who stands on the board in a round: attackers that ran, plus those kept on a shelf last round
// but not drawn this round (idle). Real exports can list an attacker twice; keep one each.
const castCache = new WeakMap<Replay, AttackerInfo[][]>();
function castOf(replay: Replay, index: number): AttackerInfo[] {
  let cached = castCache.get(replay);
  if (!cached) castCache.set(replay, (cached = []));
  if (cached[index]) return cached[index];
  const round = replay.rounds[index];
  const ran = new Set(round.attackers.map((a) => a.attacker_id));
  const idle =
    index > 0
      ? replay.rounds[index - 1].shelves
          .flatMap((s) => [...s.attacker_ids, ...(s.outsider_slot ? [s.outsider_slot] : [])])
          .filter((id) => !ran.has(id))
          .flatMap((id) => latestAttacker(replay, index, id)?.info ?? [])
      : [];
  return (cached[index] = [...new Map([...round.attackers, ...idle].map((a) => [a.attacker_id, a])).values()]);
}

// Spots round each shelf's middle, fixed for the whole run: six, or more if the shelf ever holds more.
const ringCache = new WeakMap<Replay, Map<ShelfId, number>>();
function ringSizes(replay: Replay): Map<ShelfId, number> {
  let sizes = ringCache.get(replay);
  if (sizes) return sizes;
  sizes = new Map();
  for (let i = 0; i < replay.rounds.length; i++) {
    const count = new Map<ShelfId, number>();
    for (const a of castOf(replay, i)) count.set(a.shelf, (count.get(a.shelf) ?? 0) + 1);
    for (const [shelf, n] of count) sizes.set(shelf, Math.max(sizes.get(shelf) ?? 6, n - 1));
  }
  ringCache.set(replay, sizes);
  return sizes;
}

export function buildRoundModel(replay: Replay, index: number): RoundModel {
  const round = replay.rounds[index];

  const newIds = new Set<string>([
    ...round.actions.flatMap((a) => (a.attacker_id ? [a.attacker_id] : [])),
    ...round.news.flatMap((n) => n.attacker_ids),
  ]);
  const kept = new Set(round.shelves.flatMap((s) => [...s.attacker_ids, ...(s.outsider_slot ? [s.outsider_slot] : [])]));
  const outsiderShelf = new Map(round.shelves.filter((s) => s.outsider_slot).map((s) => [s.outsider_slot!, s.shelf]));
  const ran = new Set(round.attackers.map((a) => a.attacker_id));
  const unique = castOf(replay, index);

  // Spots: the outsider holding a shelf's outsider spot stands in the middle; everyone else keeps
  // last round's spot on the same shelf while it's free; newcomers (including devils that switched
  // shelves) fill the free spots in the order they arrived.
  const firstSeen = new Map<string, number>();
  replay.rounds.slice(0, index + 1).forEach((r, i) =>
    r.attackers.forEach((a) => {
      if (!firstSeen.has(a.attacker_id)) firstSeen.set(a.attacker_id, i);
    }),
  );
  const prev = index > 0 ? getRoundModel(replay, index - 1) : null;
  const prevShelf = new Map(prev?.devils.map((d) => [d.attacker.attacker_id, d.attacker.shelf]));
  const slots = new Map<string, number>();
  const byShelf = new Map<ShelfId, AttackerInfo[]>();
  for (const a of unique) byShelf.set(a.shelf, [...(byShelf.get(a.shelf) ?? []), a]);
  for (const [shelf, list] of byShelf) {
    const taken = new Set<number>();
    const claim = (id: string, slot: number) => {
      slots.set(id, slot);
      taken.add(slot);
    };
    const centre = list.find((a) => a.source === "outsider" && outsiderShelf.get(a.attacker_id) === shelf);
    if (centre) claim(centre.attacker_id, 0);
    for (const a of list) {
      const was = prevShelf.get(a.attacker_id) === shelf ? prev!.slots.get(a.attacker_id) : undefined;
      if (a !== centre && was !== undefined && !taken.has(was)) claim(a.attacker_id, was);
    }
    list
      .filter((a) => !slots.has(a.attacker_id))
      .sort((a, b) => firstSeen.get(a.attacker_id)! - firstSeen.get(b.attacker_id)! || a.attacker_id.localeCompare(b.attacker_id))
      .forEach((a) => {
        let s = 0;
        while (taken.has(s)) s++;
        claim(a.attacker_id, s);
      });
  }
  const ring = ringSizes(replay);

  const retunes = new Map<string, { at: number; changed: boolean }[]>();
  round.waves.forEach((w) =>
    w.retunes.forEach((r) => {
      const list = retunes.get(r.attacker_id) ?? [];
      list.push({ at: waveStart(w.wave) + THROW_WINDOW + FLIGHT, changed: r.strategy_changed });
      retunes.set(r.attacker_id, list);
    }),
  );

  const devils: DevilPlacement[] = unique.map((a) => ({
    attacker: a,
    pos: shelfSlot(a.shelf, slots.get(a.attacker_id)!, ring.get(a.shelf)!),
    appear: newIds.has(a.attacker_id) ? SPAWN_POP + hash(a.attacker_id) * 0.6 : 0,
    leave: kept.has(a.attacker_id) ? null : LEARN_START + 0.5 + hash(a.attacker_id) * 1.2,
    retunes: retunes.get(a.attacker_id) ?? [],
    idle: !ran.has(a.attacker_id),
  }));
  const devilPos = new Map(devils.map((d) => [d.attacker.attacker_id, d.pos]));

  const coins: CoinFlight[] = [];
  for (const w of round.waves) {
    const start = waveStart(w.wave);
    const n = w.payments.length;
    const attackCount = w.payments.filter((p) => p.source === "attacker").length;
    const drawEvery = Math.max(1, Math.ceil(attackCount / MAX_DRAWN_ATTACK_COINS));
    const honestEvery = Math.max(1, Math.ceil((n - attackCount) / MAX_DRAWN_HONEST_COINS));
    let attackSeen = 0;
    let honestSeen = 0;
    w.payments.forEach((p, i) => {
      const launch = start + (i / n) * THROW_WINDOW;
      const attack = p.source === "attacker";
      const outcome: Outcome = p.is_fraud ? (p.settled ? "stolen" : "blocked") : p.settled ? "honest" : "false_alarm";
      const from = attack
        ? devilPos.get(p.attacker_id!) ?? GATE
        : SHOPPER_SPOTS[Math.floor(hash(p.tx_id) * SHOPPER_SPOTS.length)];
      const drawn = attack
        ? attackSeen++ % drawEvery === 0
        : outcome === "false_alarm" || honestSeen++ % honestEvery === 0;
      coins.push({
        payment: p,
        from,
        launch,
        intercept: launch + FLIGHT * INTERCEPT_FRAC,
        land: launch + FLIGHT,
        outcome,
        // A stop no specialist objected to (model voters, a hard veto, the investigator) is the
        // orchestrator's final call, so it fires the shot.
        zappers: outcome === "blocked" || outcome === "false_alarm" ? (p.objected_by.length ? p.objected_by : ["orchestrator"]) : p.objected_by,
        attack,
        drawn,
      });
    });
  }

  const beams: SpawnBeam[] = round.actions
    .filter((a) => a.action === "spawn" && a.attacker_id && devilPos.has(a.attacker_id))
    .map((a) => ({ actor: a.actor, to: devilPos.get(a.attacker_id!)!, attackerId: a.attacker_id! }));
  const news: NewsFall[] = round.news.map((n) => ({ title: n.title, to: devilPos.get(n.attacker_ids[0]) ?? GATE }));

  return { devils, slots, coins, beams, news };
}

// Running totals for the scoreboard at round-clock time t.
export function tallyAt(model: RoundModel, t: number) {
  let blocked = 0;
  let stolen = 0;
  let falseAlarms = 0;
  let through = 0;
  let honest = 0; // honest money that settled
  for (const c of model.coins) {
    if (c.outcome === "honest" && c.land <= t) honest += c.payment.amount_cents;
    if (c.outcome === "blocked" && c.intercept <= t) blocked++;
    else if (c.outcome === "stolen" && c.land <= t) {
      stolen += c.payment.amount_cents;
      through++;
    }
    else if (c.outcome === "false_alarm" && c.intercept <= t) falseAlarms++;
  }
  return { blocked, stolen, falseAlarms, through, honest, net: honest - stolen };
}

const cache = new WeakMap<Replay, Map<number, RoundModel>>();
export function getRoundModel(replay: Replay, index: number): RoundModel {
  let byRound = cache.get(replay);
  if (!byRound) cache.set(replay, (byRound = new Map()));
  let model = byRound.get(index);
  if (!model) byRound.set(index, (model = buildRoundModel(replay, index)));
  return model;
}
