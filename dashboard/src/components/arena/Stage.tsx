"use client";

import { OrbitControls } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { Bloom, BrightnessContrast, DepthOfField, EffectComposer, Noise, ToneMapping, Vignette } from "@react-three/postprocessing";
import { BlendFunction, ToneMappingMode } from "postprocessing";
import { type ReactNode, useMemo, useRef } from "react";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";

import type { Vec3 } from "./data";
import { fx } from "./fx";

type V3 = [number, number, number];

/** How a scene is filmed: the opening move, the resting view, and how far the viewer may orbit. */
export interface Shot {
  from: V3;
  to: V3;
  seconds: number;
  sway: number; // radians either side of the front view
  distance: [number, number];
  polar: [number, number];
  /** Depth of field (microscope look): world-space focus distance and range, or none. */
  dof?: { distance: number; range: number; bokeh: number };
  grade: { bloom: number; contrast: number; vignette: number; grain: number };
}

export const SHOTS = {
  // A dolly down the vessel, ending on a side view: defense on the left, pathogens on the right.
  immune: {
    from: [-15, 1.4, 5.5], to: [-0.4, 0.3, 8.8], seconds: 6, sway: 0.22, distance: [3.5, 11], polar: [1.05, 2.05],
    dof: { distance: 9.2, range: 5.5, bokeh: 2.6 },
    grade: { bloom: 0.8, contrast: 0.1, vignette: 0.62, grain: 0.07 },
  },
  // The crane through the armillary, down to a low side view.
  astrolabe: {
    from: [0.6, 23, 4], to: [0.9, 4.3, 15.6], seconds: 6.5, sway: 0.32, distance: [6, 24], polar: [0.3, 1.42],
    grade: { bloom: 0.9, contrast: 0.14, vignette: 0.72, grain: 0.1 },
  },
} satisfies Record<string, Shot>;

export type SceneId = keyof typeof SHOTS;

/** Canvas camera settings: set on <Canvas> so the composer renders through this camera from frame one. */
export function cameraFor(scene: SceneId) {
  return { fov: scene === "immune" ? 48 : 30, near: 0.1, far: 400, position: SHOTS[scene].from };
}

const IDLE_AFTER = 6; // seconds after the viewer lets go before the sway resumes

/** Opening move, then a slow sway around the front view that leans toward the fighting. */
export function CameraRig({ shot, focus, battling }: { shot: Shot; focus: Vec3; battling: boolean }) {
  const controls = useRef<OrbitControlsImpl>(null);
  const started = useRef<number | null>(null);
  const touched = useRef(-Infinity); // clock time the viewer last let go (Infinity while dragging)
  const released = useRef(false);
  const from = useMemo(() => new THREE.Vector3(...shot.from), [shot]);
  const to = useMemo(() => new THREE.Vector3(...shot.to), [shot]);
  const target = useMemo(() => new THREE.Vector3(...focus), [focus]);
  const offset = useMemo(() => new THREE.Vector3(), []);
  const spherical = useMemo(() => new THREE.Spherical(), []);

  useFrame(({ camera, clock }, dt) => {
    const now = clock.elapsedTime;
    started.current ??= now;
    const k = Math.min(1, (now - started.current) / shot.seconds);
    if (k < 1) {
      const eased = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2; // ease in-out
      camera.position.lerpVectors(from, to, eased);
    }
    if (released.current) {
      touched.current = now;
      released.current = false;
    }
    const c = controls.current;
    if (!c) return;
    c.target.lerp(target, 1 - Math.exp(-1.4 * dt));
    // Sway: drift the azimuth around the front view while nobody is dragging.
    if (k >= 1 && now - touched.current > IDLE_AFTER) {
      offset.copy(camera.position).sub(c.target);
      spherical.setFromVector3(offset);
      const desired = Math.sin((now - started.current - shot.seconds) * (battling ? 0.11 : 0.07)) * shot.sway;
      spherical.theta = THREE.MathUtils.lerp(spherical.theta, desired, 1 - Math.exp(-0.8 * dt));
      offset.setFromSpherical(spherical);
      camera.position.copy(c.target).add(offset);
    }
  });

  return (
    <OrbitControls ref={controls} makeDefault enableDamping dampingFactor={0.06} enablePan={false}
                   minDistance={shot.distance[0]} maxDistance={shot.distance[1]}
                   minPolarAngle={shot.polar[0]} maxPolarAngle={shot.polar[1]} target={focus}
                   onStart={() => { touched.current = Infinity; }}
                   onEnd={() => { released.current = true; }} />
  );
}

/** Shakes everything inside it on impacts, and decays the shared flash. */
export function ShakeRig({ children }: { children: ReactNode }) {
  const group = useRef<THREE.Group>(null);
  useFrame((_, dt) => {
    fx.shake *= Math.exp(-5.5 * dt);
    fx.flash = Math.max(0, fx.flash - dt * 1.6);
    const s = fx.shake * 0.15;
    group.current?.position.set((Math.random() - 0.5) * s, (Math.random() - 0.5) * s * 0.6, (Math.random() - 0.5) * s);
  });
  return <group ref={group}>{children}</group>;
}

/** Film look: optional depth of field, gentle HDR bloom, AgX tone mapping, a soft vignette and a whisper of grain. */
export function PostFX({ shot }: { shot: Shot }) {
  const { grade, dof } = shot;
  return (
    // No MSAA on the composer: with multisampling the output went black on this GPU.
    <EffectComposer multisampling={0}>
      {dof ? <DepthOfField worldFocusDistance={dof.distance} worldFocusRange={dof.range} bokehScale={dof.bokeh} /> : null}
      <Bloom mipmapBlur intensity={grade.bloom} luminanceThreshold={1} luminanceSmoothing={0.35} radius={0.75} />
      <ToneMapping mode={ToneMappingMode.AGX} />
      <BrightnessContrast brightness={-0.02} contrast={grade.contrast} />
      <Vignette offset={0.3} darkness={grade.vignette} />
      <Noise premultiply blendFunction={BlendFunction.ADD} opacity={grade.grain} />
    </EffectComposer>
  );
}
