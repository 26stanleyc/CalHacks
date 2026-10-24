import { useMemo, useRef, type ReactNode } from "react";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { Billboard } from "@react-three/drei";
import * as THREE from "three";
import type { Actor, AttackerSource } from "../data/types";
import { C, M } from "../palette";
import { LEARN_START, useArena, type Selection } from "../store";
import { ACTOR_NAME } from "../ui/words";
import type { Vec3 } from "./layout";

// Invisible but still hit by the raycaster, so small creatures are easy to click.
const HIT = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false });
const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const easeOutBack = (x: number) => 1 + 2.7 * Math.pow(x - 1, 3) + 1.7 * Math.pow(x - 1, 2);
// Is any event in the sorted list within `window` seconds before t?
const recently = (times: number[], t: number, window: number) => times.some((x) => t >= x && t - x < window);

export function Pickable({ sel, label, children }: { sel: Selection; label: string; children: ReactNode }) {
  const select = useArena((s) => s.select);
  const setHover = useArena((s) => s.setHover);
  return (
    <group
      onClick={(e: ThreeEvent<MouseEvent>) => {
        e.stopPropagation();
        select(sel);
      }}
      onPointerOver={(e) => {
        e.stopPropagation();
        document.body.style.cursor = "pointer";
        setHover({ label, pos: e.object.getWorldPosition(new THREE.Vector3()).toArray() as Vec3 });
      }}
      onPointerOut={() => {
        document.body.style.cursor = "";
        setHover(null);
      }}
    >
      {children}
    </group>
  );
}

function SelectionRing({ active, radius }: { active: boolean; radius: number }) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    if (ref.current) ref.current.scale.setScalar(1 + Math.sin(clock.elapsedTime * 5) * 0.08);
  });
  if (!active) return null;
  return (
    <mesh ref={ref} rotation-x={-Math.PI / 2} position-y={0.02}>
      <ringGeometry args={[radius, radius + 0.07, 40]} />
      <meshBasicMaterial color="#FFFFFF" />
    </mesh>
  );
}

function Eyes({ y, z, spread = 0.1, size = 0.045 }: { y: number; z: number; spread?: number; size?: number }) {
  return (
    <>
      <mesh position={[-spread, y, z]} material={M.ink}>
        <sphereGeometry args={[size, 12, 12]} />
      </mesh>
      <mesh position={[spread, y, z]} material={M.ink}>
        <sphereGeometry args={[size, 12, 12]} />
      </mesh>
      <mesh position={[-spread - 0.06, y - 0.07, z - 0.02]} material={M.cheek}>
        <sphereGeometry args={[0.035, 10, 10]} />
      </mesh>
      <mesh position={[spread + 0.06, y - 0.07, z - 0.02]} material={M.cheek}>
        <sphereGeometry args={[0.035, 10, 10]} />
      </mesh>
    </>
  );
}

// Each specialist carries one prop that says what it watches.
function LaneProp({ actor }: { actor: Actor }) {
  switch (actor) {
    case "timing":
      return (
        <group>
          <mesh rotation-x={Math.PI / 2} material={M.gold}>
            <cylinderGeometry args={[0.13, 0.13, 0.04, 24]} />
          </mesh>
          <mesh position={[0, 0.03, 0.025]} material={M.ink}>
            <boxGeometry args={[0.015, 0.09, 0.01]} />
          </mesh>
          <mesh position={[0.03, 0, 0.025]} material={M.ink}>
            <boxGeometry args={[0.06, 0.015, 0.01]} />
          </mesh>
        </group>
      );
    case "graph":
      return (
        <group>
          {[[0, 0.09, 0], [-0.09, -0.06, 0], [0.09, -0.06, 0]].map((p, i) => (
            <mesh key={i} position={p as Vec3} material={M.seraph}>
              <sphereGeometry args={[0.045, 12, 12]} />
            </mesh>
          ))}
          <mesh rotation-z={Math.PI / 2} position-y={-0.06} material={M.gold}>
            <cylinderGeometry args={[0.012, 0.012, 0.18, 6]} />
          </mesh>
          <mesh rotation-z={0.5} position={[-0.045, 0.015, 0]} material={M.gold}>
            <cylinderGeometry args={[0.012, 0.012, 0.17, 6]} />
          </mesh>
          <mesh rotation-z={-0.5} position={[0.045, 0.015, 0]} material={M.gold}>
            <cylinderGeometry args={[0.012, 0.012, 0.17, 6]} />
          </mesh>
        </group>
      );
    case "identity":
      return (
        <group>
          <mesh material={M.white}>
            <boxGeometry args={[0.24, 0.16, 0.02]} />
          </mesh>
          <mesh position={[-0.06, 0, 0.012]} material={M.seraph}>
            <circleGeometry args={[0.04, 16]} />
          </mesh>
          <mesh position={[0.05, 0.02, 0.012]} material={M.ink}>
            <planeGeometry args={[0.08, 0.015]} />
          </mesh>
          <mesh position={[0.05, -0.02, 0.012]} material={M.ink}>
            <planeGeometry args={[0.08, 0.015]} />
          </mesh>
        </group>
      );
    case "velocity":
      return (
        <group rotation-z={-Math.PI / 2}>
          <mesh material={M.seraph}>
            <coneGeometry args={[0.08, 0.14, 16]} />
          </mesh>
          <mesh position-y={-0.12} material={M.gold}>
            <cylinderGeometry args={[0.03, 0.03, 0.14, 10]} />
          </mesh>
        </group>
      );
    case "mandate":
      return (
        <group rotation-z={Math.PI / 2}>
          <mesh material={M.paper}>
            <cylinderGeometry args={[0.05, 0.05, 0.26, 16]} />
          </mesh>
          <mesh material={M.imp}>
            <torusGeometry args={[0.055, 0.012, 8, 20]} />
          </mesh>
        </group>
      );
    case "orchestrator":
      return (
        <mesh material={M.gold} rotation-x={Math.PI / 2}>
          <torusGeometry args={[0.1, 0.025, 8, 5]} />
        </mesh>
      );
  }
}

export function Angel({ actor, pos, zaps, learns }: { actor: Actor; pos: Vec3; zaps: number[]; learns: boolean }) {
  const body = useRef<THREE.Group>(null);
  const halo = useRef<THREE.Mesh>(null);
  const wingL = useRef<THREE.Mesh>(null);
  const wingR = useRef<THREE.Mesh>(null);
  const pulse = useRef<THREE.Mesh>(null);
  const selected = useArena((s) => s.selected?.kind === "defender" && s.selected.id === actor);
  const phase = pos[0] * 1.7;
  const big = actor === "orchestrator";
  const haloMat = useMemo(() => M.halo.clone(), []);

  useFrame(({ clock }) => {
    const t = useArena.getState().t;
    const now = clock.elapsedTime;
    const zapping = recently(zaps, t, 0.35);
    if (body.current) {
      body.current.position.y = 0.42 + Math.sin(now * 2 + phase) * 0.05 + (zapping ? 0.08 : 0);
      body.current.rotation.y = Math.sin(now * 0.6 + phase) * 0.25;
    }
    if (halo.current) {
      halo.current.position.y = 0.48 + Math.sin(now * 3 + phase) * 0.02;
      haloMat.emissiveIntensity = zapping ? 2.2 : 0.6;
    }
    const flap = Math.sin(now * (zapping ? 20 : 6) + phase) * 0.35;
    if (wingL.current) wingL.current.rotation.y = 0.5 + flap;
    if (wingR.current) wingR.current.rotation.y = -0.5 - flap;
    if (pulse.current) {
      // A gold ring rises off the tile while the specialist rewrites its rulebook.
      const k = clamp01((t - LEARN_START - 0.3) / 2.2);
      pulse.current.visible = learns && k > 0 && k < 1;
      pulse.current.scale.setScalar(0.6 + k * 1.4);
      pulse.current.position.y = 0.1 + k * 1.2;
      (pulse.current.material as THREE.MeshBasicMaterial).opacity = 1 - k;
    }
  });

  return (
    <group position={pos}>
      <SelectionRing active={selected} radius={0.5} />
      <mesh rotation-x={-Math.PI / 2} position-y={0.015}>
        <ringGeometry args={[0.32, 0.4, 6]} />
        <meshBasicMaterial color={C.seraph} />
      </mesh>
      <mesh ref={pulse} rotation-x={-Math.PI / 2}>
        <ringGeometry args={[0.38, 0.44, 32]} />
        <meshBasicMaterial color={C.halo} transparent />
      </mesh>
      <Pickable sel={{ kind: "defender", id: actor }} label={ACTOR_NAME[actor]}>
        <group ref={body} scale={big ? 1.25 : 1}>
          <mesh castShadow material={M.angel} scale={[1, 1.12, 1]}>
            <sphereGeometry args={[0.3, 28, 28]} />
          </mesh>
          <mesh material={M.seraph} position-y={-0.12} rotation-x={Math.PI / 2}>
            <torusGeometry args={[0.27, 0.05, 10, 32]} />
          </mesh>
          <Eyes y={0.06} z={0.27} />
          <mesh ref={halo} rotation-x={Math.PI / 2} material={haloMat}>
            <torusGeometry args={[0.16, 0.03, 10, 32]} />
          </mesh>
          <mesh ref={wingL} position={[-0.26, 0.05, -0.12]} scale={[0.22, 0.3, 0.05]} material={M.wing} castShadow>
            <sphereGeometry args={[1, 16, 16]} />
          </mesh>
          <mesh ref={wingR} position={[0.26, 0.05, -0.12]} scale={[0.22, 0.3, 0.05]} material={M.wing} castShadow>
            <sphereGeometry args={[1, 16, 16]} />
          </mesh>
          <group position={[0.38, 0.12, 0.12]}>
            <LaneProp actor={actor} />
          </group>
        </group>
      </Pickable>
    </group>
  );
}

export interface DevilProps {
  id: string;
  name: string;
  source: AttackerSource;
  pos: Vec3;
  appear: number;
  leave: number | null;
  throws: number[];
  retunes: { at: number; changed: boolean }[];
  facing: number;
  idle: boolean;
}

const RING_COLOR: Record<AttackerSource, string> = {
  spawned: C.seraph, // built by our own angels
  outsider: C.ink,
  threat_intel: C.news,
  held_out: "#7A55C7",
};

export function Devil({ id, name, source, pos, appear, leave, throws, retunes, facing, idle }: DevilProps) {
  const root = useRef<THREE.Group>(null);
  const body = useRef<THREE.Group>(null);
  const tail = useRef<THREE.Group>(null);
  const bubble = useRef<THREE.Group>(null);
  const bang = useRef<THREE.Group>(null);
  const selected = useArena((s) => s.selected?.kind === "attacker" && s.selected.id === id);
  const phase = pos[0] * 2.3 + pos[2];

  useFrame(({ clock }) => {
    const t = useArena.getState().t;
    const now = clock.elapsedTime;
    let s = appear > 0 ? easeOutBack(clamp01((t - appear) / 0.5)) : 1;
    if (leave !== null) s *= 1 - clamp01((t - leave) / 0.6);
    if (root.current) {
      root.current.scale.setScalar(Math.max(0.0001, s));
      root.current.visible = s > 0.001;
    }
    const throwing = recently(throws, t, 0.3);
    if (body.current) {
      // Devils sitting this round out just breathe; active ones hop.
      const hop = idle ? Math.sin(now * 1.5 + phase) * 0.01 : Math.abs(Math.sin(now * 3.2 + phase)) * 0.06;
      body.current.position.y = 0.3 + hop;
      body.current.scale.set(throwing ? 1.15 : 1, throwing ? 0.85 : 1, throwing ? 1.15 : 1);
    }
    if (tail.current) tail.current.rotation.y = Math.sin(now * 4 + phase) * 0.6;
    const r = retunes.find((x) => t >= x.at && t - x.at < 1.6);
    if (bubble.current) bubble.current.visible = !!r;
    if (bang.current) bang.current.visible = !!r?.changed;
  });

  return (
    <group ref={root} position={pos} rotation-y={facing}>
      <SelectionRing active={selected} radius={0.32} />
      <mesh rotation-x={-Math.PI / 2} position-y={0.015}>
        <ringGeometry args={[0.2, 0.26, 24]} />
        <meshBasicMaterial color={RING_COLOR[source]} />
      </mesh>
      <Pickable sel={{ kind: "attacker", id }} label={name}>
        <mesh position-y={0.35} material={HIT}>
          <sphereGeometry args={[0.4, 8, 8]} />
        </mesh>
        <group ref={body}>
          <mesh castShadow material={M.imp}>
            <sphereGeometry args={[0.22, 24, 24]} />
          </mesh>
          <mesh position={[-0.11, 0.2, 0.02]} rotation-z={0.35} material={M.impDark}>
            <coneGeometry args={[0.05, 0.16, 12]} />
          </mesh>
          <mesh position={[0.11, 0.2, 0.02]} rotation-z={-0.35} material={M.impDark}>
            <coneGeometry args={[0.05, 0.16, 12]} />
          </mesh>
          {[-0.075, 0.075].map((x) => (
            <group key={x} position={[x, 0.04, 0.19]}>
              <mesh material={M.white}>
                <sphereGeometry args={[0.05, 12, 12]} />
              </mesh>
              <mesh position-z={0.035} material={M.ink}>
                <sphereGeometry args={[0.025, 10, 10]} />
              </mesh>
            </group>
          ))}
          <group ref={tail} position={[0, -0.08, -0.2]}>
            <mesh rotation-x={-1} position={[0, 0.04, -0.06]} material={M.impDark}>
              <cylinderGeometry args={[0.018, 0.018, 0.18, 8]} />
            </mesh>
            <mesh position={[0, 0.12, -0.13]} rotation-x={-0.4} material={M.impDark}>
              <coneGeometry args={[0.05, 0.08, 4]} />
            </mesh>
          </group>
          {source === "outsider" && (
            <group position-y={0.24}>
              <mesh material={M.outsiderHat}>
                <cylinderGeometry args={[0.18, 0.18, 0.02, 24]} />
              </mesh>
              <mesh position-y={0.08} material={M.outsiderHat}>
                <cylinderGeometry args={[0.11, 0.12, 0.16, 24]} />
              </mesh>
            </group>
          )}
          {source === "threat_intel" && (
            <mesh position-y={0.3} material={M.paper}>
              <coneGeometry args={[0.15, 0.2, 4]} />
            </mesh>
          )}
        </group>
      </Pickable>
      <group ref={bubble} position-y={0.95} visible={false}>
        <Billboard>
          <mesh>
            <circleGeometry args={[0.16, 24]} />
            <meshBasicMaterial color="#FFFFFF" />
          </mesh>
          {/* "!" = rethinking its strategy; dots = re-tuning its numbers */}
          <group ref={bang} position-z={0.01}>
            <mesh position-y={0.03}>
              <planeGeometry args={[0.045, 0.13]} />
              <meshBasicMaterial color={C.imp} />
            </mesh>
            <mesh position-y={-0.08}>
              <circleGeometry args={[0.026, 12]} />
              <meshBasicMaterial color={C.imp} />
            </mesh>
          </group>
          {[-0.06, 0, 0.06].map((x) => (
            <mesh key={x} position={[x, 0, 0.005]}>
              <circleGeometry args={[0.022, 10]} />
              <meshBasicMaterial color={C.ink} />
            </mesh>
          ))}
        </Billboard>
      </group>
    </group>
  );
}

export function Shopper({ pos, throws }: { pos: Vec3; throws: number[] }) {
  const body = useRef<THREE.Group>(null);
  const phase = pos[0] * 3.1 + pos[2] * 1.3;
  useFrame(({ clock }) => {
    if (!body.current) return;
    const t = useArena.getState().t;
    const now = clock.elapsedTime;
    // Ambles in a small loop around its spot.
    body.current.position.x = Math.sin(now * 0.4 + phase) * 0.25;
    body.current.position.z = Math.cos(now * 0.4 + phase) * 0.25;
    body.current.rotation.y = now * 0.4 + phase + Math.PI / 2;
    body.current.position.y = 0.2 + (recently(throws, t, 0.3) ? 0.08 : Math.abs(Math.sin(now * 5 + phase)) * 0.025);
  });
  return (
    <group position={pos}>
      <group ref={body}>
        <mesh castShadow material={M.pebble} scale={[1, 1.15, 1]}>
          <sphereGeometry args={[0.17, 20, 20]} />
        </mesh>
        <Eyes y={0.05} z={0.15} spread={0.06} size={0.028} />
        <mesh position={[0.17, -0.06, 0]} material={M.bag}>
          <boxGeometry args={[0.09, 0.1, 0.06]} />
        </mesh>
      </group>
    </group>
  );
}
