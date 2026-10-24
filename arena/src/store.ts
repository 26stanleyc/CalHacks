import { create } from "zustand";
import type { Replay } from "./data/types";

// One round on screen, in seconds at 1x: defenders spawn, three waves of payments, then learning.
export const SPAWN_END = 3;
export const WAVE_LEN = 7;
export const THROW_WINDOW = 5; // coins launch during the first 5 s of a wave
export const FLIGHT = 1.8;
export const LEARN_START = SPAWN_END + 3 * WAVE_LEN;
export const ROUND_LEN = LEARN_START + 3;

export type Phase = { kind: "spawn" } | { kind: "wave"; wave: number } | { kind: "learn" };

export function phaseAt(t: number): Phase {
  if (t < SPAWN_END) return { kind: "spawn" };
  if (t < LEARN_START) return { kind: "wave", wave: Math.floor((t - SPAWN_END) / WAVE_LEN) + 1 };
  return { kind: "learn" };
}
export const waveStart = (wave: number) => SPAWN_END + (wave - 1) * WAVE_LEN;

export type Selection =
  | { kind: "attacker"; id: string }
  | { kind: "defender"; id: string }
  | { kind: "shelf"; id: string }
  | { kind: "gate" };

export interface Hover {
  label: string;
  pos: [number, number, number];
}

interface ArenaState {
  replay: Replay | null;
  round: number; // index into replay.rounds
  t: number; // seconds into the round
  playing: boolean;
  speed: number;
  selected: Selection | null;
  hover: Hover | null;
  setReplay: (r: Replay) => void;
  jump: (round: number) => void;
  tick: (dt: number) => void;
  toggle: () => void;
  setSpeed: (s: number) => void;
  select: (s: Selection | null) => void;
  setHover: (h: Hover | null) => void;
}

export const useArena = create<ArenaState>((set, get) => ({
  replay: null,
  round: 0,
  t: 0,
  playing: true,
  speed: 1,
  selected: null,
  hover: null,
  setReplay: (replay) => set({ replay, round: 0, t: 0 }),
  jump: (round) => set({ round, t: 0 }),
  tick: (dt) => {
    const { playing, speed, t, round, replay } = get();
    if (!playing || !replay) return;
    const nt = t + dt * speed;
    if (nt < ROUND_LEN) return set({ t: nt });
    if (round + 1 < replay.rounds.length) return set({ round: round + 1, t: nt - ROUND_LEN });
    set({ t: ROUND_LEN - 0.001, playing: false });
  },
  toggle: () => {
    const { playing, round, t, replay } = get();
    // Pressing play at the very end starts the replay over.
    if (!playing && replay && round === replay.rounds.length - 1 && t >= ROUND_LEN - 0.01) set({ round: 0, t: 0 });
    set({ playing: !playing });
  },
  setSpeed: (speed) => set({ speed }),
  select: (selected) => set({ selected }),
  setHover: (hover) => set({ hover }),
}));

// Handy for poking at playback from the browser console while developing.
if (import.meta.env.DEV) (window as unknown as { arena: typeof useArena }).arena = useArena;
