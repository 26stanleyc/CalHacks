// The start of each round: a beam arcs from each angel that chose to build an attacker to
// the shelf it aimed at, and the new devil hatches there. News scams fall from the sky as a
// newspaper onto their shelf.

import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import * as THREE from "three";
import { C, M } from "../palette";
import { SPAWN_END, useArena } from "../store";
import { actorPos } from "./layout";
import type { NewsFall, SpawnBeam } from "./roundModel";

const SEGMENTS = 48;
const RADIAL = 6;

function Beam({ beam }: { beam: SpawnBeam }) {
  const geo = useMemo(() => {
    const from = actorPos(beam.actor);
    const a = new THREE.Vector3(from[0], from[1] + 0.95, from[2]);
    const b = new THREE.Vector3(...beam.to).add(new THREE.Vector3(0, 0.3, 0));
    const mid = a.clone().lerp(b, 0.5).add(new THREE.Vector3(0, 3.2, 0));
    return new THREE.TubeGeometry(new THREE.QuadraticBezierCurve3(a, mid, b), SEGMENTS, 0.075, RADIAL, false);
  }, [beam]);
  const mat = useMemo(() => new THREE.MeshBasicMaterial({ color: "#4F7BFF", transparent: true }), []);
  useFrame(() => {
    const t = useArena.getState().t;
    const grow = Math.min(1, Math.max(0, (t - 0.2) / 1.3));
    geo.setDrawRange(0, Math.floor(grow * SEGMENTS) * RADIAL * 6);
    mat.opacity = t > SPAWN_END ? 0 : 1 - Math.max(0, (t - 2.1) / 0.9);
  });
  return <mesh geometry={geo} material={mat} />;
}

function Newspaper({ news }: { news: NewsFall }) {
  const ref = useRef<THREE.Group>(null);
  const label = useRef<HTMLDivElement>(null);
  useFrame(() => {
    const t = useArena.getState().t;
    const k = Math.min(1, t / 1.4);
    if (ref.current) {
      ref.current.visible = t < 1.9;
      ref.current.position.set(news.to[0], news.to[1] + 0.2 + (1 - k * k) * 8, news.to[2]);
      ref.current.rotation.y = k * 6;
    }
    if (label.current) label.current.style.opacity = t < SPAWN_END + 1.5 ? "1" : "0";
  });
  return (
    <>
      <group ref={ref}>
        <mesh material={M.paper} castShadow>
          <boxGeometry args={[0.6, 0.04, 0.45]} />
        </mesh>
        <mesh position-y={0.025}>
          <planeGeometry args={[0.4, 0.06]} />
          <meshBasicMaterial color={C.ink} />
        </mesh>
      </group>
      <Html position={[news.to[0], news.to[1] + 1.4, news.to[2]]} center zIndexRange={[20, 0]}>
        <div ref={label} className="headline">
          <span>New scam in the news</span>
          {news.title}
        </div>
      </Html>
    </>
  );
}

export function Spawns({ beams, news }: { beams: SpawnBeam[]; news: NewsFall[] }) {
  return (
    <group>
      {beams.map((b) => (
        <Beam key={b.attackerId} beam={b} />
      ))}
      {news.map((n) => (
        <Newspaper key={n.title} news={n} />
      ))}
    </group>
  );
}
