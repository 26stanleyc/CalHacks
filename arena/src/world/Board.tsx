import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import * as THREE from "three";
import { ALL_SHELVES } from "../data/mock";
import { C, M, shelfColor } from "../palette";
import { LEARN_START, ROUND_LEN, useArena } from "../store";
import { shelfName } from "../ui/words";
import { Pickable } from "./creatures";
import { HEX, SHELF_TILE, TILE_TOP, ring } from "./layout";

const TILE_R = HEX * 0.95;
const hexGeo = new THREE.CylinderGeometry(TILE_R, TILE_R, 1, 6);
const soil = new THREE.MeshStandardMaterial({ color: C.soil, roughness: 0.9 });

function HexTile({ x, z, top, color, material }: { x: number; z: number; top: number; color?: string; material?: THREE.Material }) {
  const depth = top + 0.8;
  return (
    <group position={[x, 0, z]}>
      <mesh geometry={hexGeo} material={soil} position-y={top - 0.06 - (depth - 0.06) / 2} scale={[1, depth - 0.06, 1]} receiveShadow />
      <mesh geometry={hexGeo} position-y={top - 0.03} scale={[1, 0.06, 1]} receiveShadow material={material}>
        {!material && <meshStandardMaterial color={color} roughness={0.85} />}
      </mesh>
    </group>
  );
}

function Shelf({ id }: { id: string }) {
  const tile = SHELF_TILE[id];
  const mat = useMemo(() => new THREE.MeshStandardMaterial({ roughness: 0.75 }), []);
  const from = useRef(new THREE.Color());
  const to = useRef(new THREE.Color());
  const selected = useArena((s) => s.selected?.kind === "shelf" && s.selected.id === id);
  useFrame(() => {
    const { replay, round, t } = useArena.getState();
    if (!replay) return;
    // The tile shows last round's verdict, then recolours while the defense learns.
    const before = round > 0 ? replay.rounds[round - 1].shelves.find((s) => s.shelf === id) : undefined;
    const after = replay.rounds[round].shelves.find((s) => s.shelf === id);
    from.current.set(shelfColor(before));
    to.current.set(shelfColor(after));
    const k = Math.min(1, Math.max(0, (t - LEARN_START - 0.5) / (ROUND_LEN - LEARN_START - 1)));
    mat.color.copy(from.current).lerp(to.current, k);
    mat.emissive.copy(mat.color).multiplyScalar(selected ? 0.35 : 0.08);
  });
  return (
    <Pickable sel={{ kind: "shelf", id }} label={shelfName(id)}>
      <HexTile x={tile.x} z={tile.z} top={TILE_TOP[4]} material={mat} />
    </Pickable>
  );
}

function Gate() {
  return (
    <Pickable sel={{ kind: "gate" }} label="x402 checkpoint">
      <HexTile x={0} z={0} top={TILE_TOP[0]} color="#E9E2F3" />
      <group position-y={TILE_TOP[0]}>
        {[-0.45, 0.45].map((x) => (
          <mesh key={x} position={[x, 0.45, 0]} material={M.gold} castShadow>
            <boxGeometry args={[0.16, 0.9, 0.16]} />
          </mesh>
        ))}
        <mesh position-y={0.9} material={M.gold} castShadow>
          <torusGeometry args={[0.45, 0.08, 10, 24, Math.PI]} />
        </mesh>
        {[0, 1, 2, 3].map((i) => (
          <mesh key={i} position={[(i % 2) * 0.12 - 0.06, 0.04 + i * 0.05, (i % 3) * 0.05 - 0.05]} material={M.gold}>
            <cylinderGeometry args={[0.14, 0.14, 0.04, 20]} />
          </mesh>
        ))}
      </group>
    </Pickable>
  );
}

function Tree({ x, z, s }: { x: number; z: number; s: number }) {
  return (
    <group position={[x, TILE_TOP[5], z]} scale={s}>
      <mesh position-y={0.2} castShadow>
        <cylinderGeometry args={[0.06, 0.08, 0.4, 8]} />
        <meshStandardMaterial color="#8A5A3B" />
      </mesh>
      <mesh position-y={0.62} castShadow>
        <coneGeometry args={[0.32, 0.6, 8]} />
        <meshStandardMaterial color="#4E9D5B" roughness={0.8} />
      </mesh>
      <mesh position-y={0.9} castShadow>
        <coneGeometry args={[0.24, 0.45, 8]} />
        <meshStandardMaterial color="#5DB06A" roughness={0.8} />
      </mesh>
    </group>
  );
}

function Cloud({ x, z, y, s, speed }: { x: number; z: number; y: number; s: number; speed: number }) {
  const ref = useRef<THREE.Group>(null);
  useFrame(({ clock }) => {
    if (ref.current) ref.current.position.x = x + Math.sin(clock.elapsedTime * speed) * 1.5;
  });
  return (
    <group ref={ref} position={[x, y, z]} scale={s}>
      {[[0, 0, 0, 1], [0.9, -0.15, 0.1, 0.75], [-0.85, -0.2, 0, 0.7], [0.3, 0.35, -0.1, 0.7]].map(([dx, dy, dz, r], i) => (
        <mesh key={i} position={[dx, dy, dz]}>
          <sphereGeometry args={[r, 18, 18]} />
          <meshStandardMaterial color="#FFFFFF" roughness={1} />
        </mesh>
      ))}
    </group>
  );
}

function HalfLabel({ angle, text }: { angle: number; text: string }) {
  const r = 9.4;
  return (
    <Html position={[Math.cos(angle) * r, 0.4, Math.sin(angle) * r]} center zIndexRange={[10, 0]}>
      <div className="plaque">{text}</div>
    </Html>
  );
}

export function Board() {
  const field = useMemo(() => [...ring(1), ...ring(2), ...ring(3)], []);
  const beach = useMemo(() => [...ring(5), ...ring(6)], []);
  // Centroid direction of each half of the shelf ring, for the two labels.
  const halfAngle = (side: "merchant" | "buyer") => {
    const ids = ALL_SHELVES.filter((id) => id.endsWith(side));
    const x = ids.reduce((s, id) => s + SHELF_TILE[id].x, 0);
    const z = ids.reduce((s, id) => s + SHELF_TILE[id].z, 0);
    return Math.atan2(z, x);
  };
  return (
    <group>
      <Gate />
      {field.map((t, i) => (
        <HexTile key={i} x={t.x} z={t.z} top={TILE_TOP[t.ring]} color={(t.q + t.r * 2) % 3 === 0 ? C.meadowDeep : C.meadow} />
      ))}
      {ALL_SHELVES.map((id) => (
        <Shelf key={id} id={id} />
      ))}
      {beach.map((t, i) => (
        <HexTile key={i} x={t.x} z={t.z} top={t.ring === 5 ? TILE_TOP[5] : -0.04} color={C.sand} />
      ))}
      {beach
        .filter((t, i) => t.ring === 6 && i % 4 === 0)
        .map((t, i) => (
          <Tree key={i} x={t.x} z={t.z} s={0.8 + (i % 3) * 0.2} />
        ))}
      <mesh rotation-x={-Math.PI / 2} position-y={-0.2} receiveShadow>
        <circleGeometry args={[60, 64]} />
        <meshStandardMaterial color={C.water} roughness={0.35} metalness={0.1} />
      </mesh>
      {/* Low and far out, so they sit on the horizon instead of drifting behind the HUD. */}
      <Cloud x={-17} z={-15} y={1.2} s={1.3} speed={0.05} />
      <Cloud x={14} z={-19} y={1.6} s={1.6} speed={0.04} />
      <Cloud x={21} z={2} y={1} s={1.1} speed={0.06} />
      <HalfLabel angle={halfAngle("merchant")} text="Attacks on stores" />
      <HalfLabel angle={halfAngle("buyer")} text="Attacks on shoppers" />
    </group>
  );
}
