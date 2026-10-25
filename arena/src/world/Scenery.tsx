// Beach scenery on the sand rings (5 and 6), where nothing plays. Picked per tile from a fixed hash, so
// it's the same on every load. Ring 5 sits right behind the shelves, so it only gets low things; the
// taller palms and umbrellas go on ring 6. Ring 6 tiles that already hold a pine (Board's i % 4 === 0)
// are skipped.

import { useMemo } from "react";
import * as THREE from "three";
import { TILE_TOP, ring, type Tile } from "./layout";

const mat = (color: string, roughness = 0.8) => new THREE.MeshStandardMaterial({ color, roughness });
const S = {
  trunk: mat("#A0703F"),
  frond: mat("#3E9B57"),
  frondLight: mat("#58B46A"),
  rock: mat("#B9B3A6", 0.95),
  rockDark: mat("#9C968A", 0.95),
  bush: mat("#5DAA5E"),
  petalPink: mat("#F28FB1"),
  petalWhite: mat("#FFF6E8"),
  petalYellow: mat("#F7CE4A"),
  pole: mat("#F4F1EA"),
  canopyRed: mat("#E8584F"),
  canopyBlue: mat("#4F8FE8"),
  towel: mat("#F2B84B"),
  starfish: mat("#F08A5D"),
  shell: mat("#F7E3D0"),
};

// A stable 0..1 per tile and salt.
const hash = (q: number, r: number, salt: number) => {
  const s = Math.sin(q * 127.1 + r * 311.7 + salt * 74.7) * 43758.5453;
  return s - Math.floor(s);
};

function Palm({ lean }: { lean: number }) {
  return (
    <group rotation-z={lean}>
      {[0, 1, 2, 3].map((i) => (
        <mesh key={i} material={S.trunk} position={[i * 0.03, 0.14 + i * 0.26, 0]} castShadow>
          <cylinderGeometry args={[0.06 - i * 0.008, 0.075 - i * 0.008, 0.28, 7]} />
        </mesh>
      ))}
      <group position={[0.1, 1.12, 0]}>
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <mesh
            key={i}
            material={i % 2 ? S.frond : S.frondLight}
            rotation={[0, (i * Math.PI) / 3, -0.55]}
            position={[Math.cos((i * Math.PI) / 3) * 0.28, -0.06, -Math.sin((i * Math.PI) / 3) * 0.28]}
            castShadow
          >
            <boxGeometry args={[0.62, 0.03, 0.16]} />
          </mesh>
        ))}
        <mesh material={S.trunk} position-y={-0.06}>
          <sphereGeometry args={[0.08, 8, 8]} />
        </mesh>
      </group>
    </group>
  );
}

function Rocks({ seed }: { seed: number }) {
  return (
    <group>
      <mesh material={S.rock} position={[0, 0.1, 0]} rotation={[seed, seed * 2, 0]} scale={[1.2, 0.8, 1]} castShadow>
        <dodecahedronGeometry args={[0.18, 0]} />
      </mesh>
      <mesh material={S.rockDark} position={[0.24, 0.06, 0.12]} rotation={[seed * 3, 0, seed]} castShadow>
        <dodecahedronGeometry args={[0.1, 0]} />
      </mesh>
      {seed > 0.5 && (
        <mesh material={S.rock} position={[-0.2, 0.05, 0.18]} castShadow>
          <dodecahedronGeometry args={[0.08, 0]} />
        </mesh>
      )}
    </group>
  );
}

const PETALS = [S.petalPink, S.petalWhite, S.petalYellow];
function Bush({ seed }: { seed: number }) {
  return (
    <group>
      {[[0, 0.16, 0, 0.2], [0.18, 0.11, 0.08, 0.14], [-0.15, 0.1, 0.1, 0.13]].map(([x, y, z, r], i) => (
        <mesh key={i} material={S.bush} position={[x, y, z]} castShadow>
          <icosahedronGeometry args={[r, 1]} />
        </mesh>
      ))}
      {[0, 1, 2, 3].map((i) => {
        const a = seed * 6 + i * 1.7;
        return (
          <mesh key={i} material={PETALS[(i + Math.floor(seed * 3)) % 3]} position={[Math.cos(a) * 0.17, 0.25 - (i % 2) * 0.07, Math.sin(a) * 0.17]}>
            <sphereGeometry args={[0.04, 6, 6]} />
          </mesh>
        );
      })}
    </group>
  );
}

function Umbrella({ seed }: { seed: number }) {
  return (
    <group>
      <mesh material={S.pole} position-y={0.4} rotation-z={0.12} castShadow>
        <cylinderGeometry args={[0.02, 0.02, 0.8, 6]} />
      </mesh>
      <mesh material={seed > 0.5 ? S.canopyRed : S.canopyBlue} position={[-0.05, 0.8, 0]} rotation-z={0.12} castShadow>
        <coneGeometry args={[0.42, 0.18, 8, 1, true]} />
      </mesh>
      <mesh material={S.towel} position={[0.28, 0.01, 0.1]} rotation-y={seed * 2}>
        <boxGeometry args={[0.24, 0.015, 0.46]} />
      </mesh>
    </group>
  );
}

function Starfish() {
  return (
    <group position-y={0.02}>
      {[0, 1, 2, 3, 4].map((i) => (
        <mesh key={i} material={S.starfish} rotation-y={(i * 2 * Math.PI) / 5} position={[Math.cos((i * 2 * Math.PI) / 5) * 0.06, 0, -Math.sin((i * 2 * Math.PI) / 5) * 0.06]}>
          <boxGeometry args={[0.13, 0.025, 0.04]} />
        </mesh>
      ))}
    </group>
  );
}

function Shells() {
  return (
    <group position-y={0.02}>
      {[[0, 0], [0.12, 0.07], [-0.06, 0.12]].map(([x, z], i) => (
        <mesh key={i} material={S.shell} position={[x, 0, z]} scale={[1, 0.5, 1]}>
          <sphereGeometry args={[0.04, 8, 6, 0, Math.PI * 2, 0, Math.PI / 2]} />
        </mesh>
      ))}
    </group>
  );
}

type Prop = { tile: Tile; kind: "palm" | "rocks" | "bush" | "umbrella" | "starfish" | "shells"; seed: number };

function pick(t: Tile, i: number): Prop | null {
  const h = hash(t.q, t.r, 1);
  const seed = hash(t.q, t.r, 2);
  if (t.ring === 5) {
    // Low things only, and not on every tile, so the sand still reads as sand.
    if (h < 0.22) return { tile: t, kind: "bush", seed };
    if (h < 0.4) return { tile: t, kind: "rocks", seed };
    if (h < 0.52) return { tile: t, kind: "starfish", seed };
    if (h < 0.62) return { tile: t, kind: "shells", seed };
    return null;
  }
  if (i % 4 === 0) return null; // a pine is already here
  if (h < 0.4) return { tile: t, kind: "palm", seed };
  if (h < 0.55) return { tile: t, kind: "umbrella", seed };
  if (h < 0.75) return { tile: t, kind: "rocks", seed };
  if (h < 0.88) return { tile: t, kind: "bush", seed };
  return null;
}

export function Scenery() {
  const props = useMemo(() => {
    const r5 = ring(5);
    // Indexed like Board's beach list (ring 5 then ring 6), so the pine check matches.
    return [...r5.map((t, i) => pick(t, i)), ...ring(6).map((t, i) => pick(t, i + r5.length))].filter(
      (p): p is Prop => p !== null,
    );
  }, []);
  return (
    <group>
      {props.map((p, i) => {
        // Off the tile centre a little, turned at random, so the rows don't line up.
        const dx = (p.seed - 0.5) * 0.4;
        const dz = (hash(p.tile.q, p.tile.r, 3) - 0.5) * 0.4;
        const top = p.tile.ring === 5 ? TILE_TOP[5] : -0.04;
        return (
          <group key={i} position={[p.tile.x + dx, top, p.tile.z + dz]} rotation-y={p.seed * Math.PI * 2}>
            {p.kind === "palm" && <Palm lean={(p.seed - 0.5) * 0.3} />}
            {p.kind === "rocks" && <Rocks seed={p.seed} />}
            {p.kind === "bush" && <Bush seed={p.seed} />}
            {p.kind === "umbrella" && <Umbrella seed={p.seed} />}
            {p.kind === "starfish" && <Starfish />}
            {p.kind === "shells" && <Shells />}
          </group>
        );
      })}
    </group>
  );
}
