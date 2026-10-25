import type * as THREE from "three";

// Shared, mutable, per-frame state. React state would re-render the scene 60 times a second, so
// units, effects, camera and HUD talk through this module instead.

export const fx = {
  shake: 0, // camera/world shake amplitude, decays every frame
  flash: 0, // full-screen flash, 0..1
  lightning: 0, // read by the HUD flash overlay; the Astrolabe stage has no lightning, so it stays 0
};

/** Live scene objects of each unit (they move), so effects can aim at them. */
export const unitObjects = new Map<string, THREE.Object3D>();

/** Latest hit on each unit's shield: where (world space), how hard (decays to 0) and when. */
export const shieldHits = new Map<string, { strength: number; point: THREE.Vector3; at: number }>();

export function hitShield(id: string, point: THREE.Vector3, at: number) {
  shieldHits.set(id, { strength: 1, point: point.clone(), at });
}

type Vec3 = [number, number, number];

const noop = () => {};

/** Filled in by <BattleFX/> on mount; no-ops until then. */
export const fxApi: {
  impact: (pos: Vec3, color: string, size?: number) => void;
  explosion: (pos: Vec3) => void;
  shockwave: (pos: Vec3, color: string, size?: number) => void;
} = { impact: noop, explosion: noop, shockwave: noop };

export function addShake(amount: number) {
  fx.shake = Math.min(0.4, fx.shake + amount);
}
