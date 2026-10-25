"use client";

import { useFrame } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";

import type { Agent, RoundScript } from "../data";
import { unitObjects } from "../fx";
import { glowTexture } from "../textures";
import { setTrackHover } from "../Tracking";

// Immune response: the defense as an immune system that vaccinates itself.
// Inside a blood vessel, red cells (legitimate payments) flow past, each carrying a "self" marker
// (its mandate). Pathogens (the attacker personas) slip in and spray toxins at the defense. White
// cells (the specialists) chase them, coat them in antibodies (rules) and engulf them. In round 3
// the dendritic cell (Graph) presents a weakened copy of a threat to train on: a vaccine. Every
// threat beaten leaves a memory cell along the vessel wall: the attack archive.

export const IMMUNE_COLORS = { defense: "#e4d9ff", attack: "#d6e35a" };
const R = 5.6; // vessel radius
const PLASMA = new THREE.Color("#2a070d");
const PATHOGEN = IMMUNE_COLORS.attack;

export const BIO: Record<string, { kind: string; note: string }> = {
  orchestrator: { kind: "helper T cell", note: "Coordinates the response: decides which cells act and how hard." },
  velocity: { kind: "neutrophil", note: "First responder. Fast, numerous, quick to swarm a burst." },
  timing: { kind: "natural killer cell", note: "Spots cells whose behavior has drifted from their own normal rhythm." },
  graph: { kind: "dendritic cell", note: "Maps connections and presents threats to the rest of the system; builds the vaccine." },
  identity: { kind: "T cell", note: "Reads each cell's self-marker and rejects ones that don't belong." },
  mandate: { kind: "B cell", note: "Releases antibodies that tag a threat for removal." },
  "card-tester": { kind: "bacteria", note: "A burst of small rods probing for a way in." },
  structurer: { kind: "spirochete", note: "A slow corkscrew that stays under the immune threshold." },
  "account-farmer": { kind: "cocci colony", note: "Many small cells, none of which looks dangerous alone." },
  hijacker: { kind: "infected red cell", note: "A healthy cell taken over from inside. Its self-marker is wrong." },
  "prompt-injector": { kind: "bacteriophage", note: "Injects its own instructions into a host's machinery." },
  "malicious-merchant": { kind: "virion", note: "A spiked virus that lures cells toward it." },
  "spawn-graph": { kind: "vaccine strain", note: "A weakened copy built by the dendritic cell, so the system learns without the risk." },
};

const hdr = (hex: string, k: number) => new THREE.Color(hex).multiplyScalar(k);
const damp = (a: number, b: number, rate: number, dt: number) => THREE.MathUtils.lerp(a, b, 1 - Math.exp(-rate * dt));
function rng(seed: number) {
  return () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
}
function hash01(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return ((h >>> 0) % 10000) / 10000;
}

/** Battle-field coordinates (x across, z deep) to the vessel (x along the flow, y up). */
const toVessel = (p: [number, number, number], id: string) =>
  new THREE.Vector3(p[0] * 1.05 - 0.4, p[2] * 0.72, -0.4 - hash01(id) * 1.2);

/** Where a unit sits in the vessel, for the camera's focus. */
export const vesselPoint = (a: Agent): [number, number, number] => toVessel(a.position, a.id).toArray() as [number, number, number];

// --- the vessel ------------------------------------------------------------------------------------

function Vessel() {
  const material = useMemo(() => new THREE.ShaderMaterial({
    side: THREE.BackSide,
    uniforms: { uTime: { value: 0 }, uPlasma: { value: PLASMA } },
    vertexShader: /* glsl */ `
      varying vec3 vW; varying vec3 vN;
      void main() { vec4 w = modelMatrix * vec4(position, 1.0); vW = w.xyz; vN = normalize(mat3(modelMatrix) * normal);
        gl_Position = projectionMatrix * viewMatrix * w; }`,
    fragmentShader: /* glsl */ `
      varying vec3 vW; varying vec3 vN; uniform float uTime; uniform vec3 uPlasma;
      float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
      float cells(vec2 p) {
        vec2 i = floor(p), f = fract(p); float d = 1.0;
        for (int y = -1; y <= 1; y++) for (int x = -1; x <= 1; x++) {
          vec2 g = vec2(float(x), float(y)); vec2 o = vec2(hash(i + g), hash(i + g + 7.0));
          o = 0.5 + 0.4 * sin(uTime * 0.15 + 6.2831 * o);
          d = min(d, length(g + o - f));
        }
        return d;
      }
      void main() {
        float a = atan(vW.y, vW.z);
        vec2 uv = vec2(vW.x * 0.5, a * 2.0);
        float c = cells(uv);
        float membrane = smoothstep(0.03, 0.0, abs(c - 0.46)) * 0.2;
        float pulse = 0.85 + 0.15 * sin(uTime * 1.2 - vW.x * 0.25); // the heartbeat, travelling down the vessel
        vec3 view = normalize(cameraPosition - vW);
        float rim = pow(1.0 - abs(dot(vN, view)), 2.0);
        vec3 tissue = mix(vec3(0.2, 0.025, 0.05), vec3(0.72, 0.22, 0.26), c * 0.5 + membrane);
        vec3 col = (tissue * (0.3 + rim * 0.8) + vec3(1.0, 0.6, 0.55) * membrane * 0.16) * pulse;
        float d = distance(vW, cameraPosition);
        col = mix(col, uPlasma, 1.0 - exp(-pow(d * 0.055, 2.0)));
        gl_FragColor = vec4(col, 1.0);
      }`,
  }), []);
  useFrame(({ clock }) => { material.uniforms.uTime.value = clock.elapsedTime; });
  return (
    <mesh rotation-z={Math.PI / 2} material={material}>
      <cylinderGeometry args={[R, R, 100, 96, 1, true]} />
    </mesh>
  );
}

/** Red blood cell: the biconcave disc (Evans & Fung profile). */
function rbcGeometry(radius: number) {
  const pts: THREE.Vector2[] = [];
  const z = (x: number) => 0.5 * Math.sqrt(Math.max(0, 1 - x * x)) * (0.207 + 2.003 * x * x - 1.123 * x ** 4);
  for (let i = 0; i <= 24; i++) { const x = i / 24; pts.push(new THREE.Vector2(x * radius, z(x) * radius)); }
  for (let i = 24; i >= 0; i--) { const x = i / 24; pts.push(new THREE.Vector2(x * radius, -z(x) * radius)); }
  const g = new THREE.LatheGeometry(pts, 40);
  g.computeVertexNormals();
  return g;
}

/** Legitimate payments: red cells flowing past, each carrying a pearl self-marker (its mandate). */
function Bloodstream({ count = 150 }: { count?: number }) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const markers = useRef<THREE.Points>(null);
  const glow = useMemo(() => glowTexture(), []);
  const geometry = useMemo(() => rbcGeometry(0.34), []);
  const cells = useMemo(() => {
    const rand = rng(41);
    return Array.from({ length: count }, () => {
      const a = rand() * Math.PI * 2, r = Math.sqrt(rand()) * (R - 0.9);
      return { x: (rand() - 0.5) * 36, y: Math.sin(a) * r, z: Math.cos(a) * r - 0.8, v: 0.4 + rand() * 0.5,
        rot: new THREE.Euler(rand() * 6, rand() * 6, rand() * 6), spin: new THREE.Vector3(rand() - 0.5, rand() - 0.5, rand() - 0.5) };
    });
  }, [count]);
  const markerPos = useMemo(() => new Float32Array(count * 3), [count]);
  const m4 = useMemo(() => new THREE.Matrix4(), []);
  const q = useMemo(() => new THREE.Quaternion(), []);
  const v = useMemo(() => new THREE.Vector3(), []);
  const n = useMemo(() => new THREE.Vector3(), []);
  const one = useMemo(() => new THREE.Vector3(1, 1, 1), []);
  useFrame(({ clock }, dt) => {
    const m = mesh.current;
    if (!m) return;
    const beat = 1 + Math.max(0, Math.sin(clock.elapsedTime * 1.2)) * 0.5; // flow surges with the heartbeat
    cells.forEach((c, i) => {
      c.x += c.v * dt * beat;
      if (c.x > 18) c.x -= 36;
      c.rot.x += c.spin.x * dt * 0.6;
      c.rot.y += c.spin.y * dt * 0.6;
      q.setFromEuler(c.rot);
      v.set(c.x, c.y + Math.sin(clock.elapsedTime * 0.7 + i) * 0.05, c.z);
      m4.compose(v, q, one);
      m.setMatrixAt(i, m4);
      n.set(0.2, 0.08, 0).applyQuaternion(q).add(v); // the marker rides on the cell's rim
      markerPos.set([n.x, n.y, n.z], i * 3);
    });
    m.instanceMatrix.needsUpdate = true;
    if (markers.current) (markers.current.geometry.attributes.position as THREE.BufferAttribute).needsUpdate = true;
  });
  return (
    <>
      <instancedMesh ref={mesh} args={[geometry, undefined, count]}>
        <meshPhysicalMaterial color="#a8101c" roughness={0.38} clearcoat={0.6} clearcoatRoughness={0.3} sheen={1}
                              sheenColor="#ff9a9a" sheenRoughness={0.4} />
      </instancedMesh>
      <points ref={markers}>
        <bufferGeometry><bufferAttribute attach="attributes-position" args={[markerPos, 3]} /></bufferGeometry>
        <pointsMaterial size={0.09} map={glow} color={hdr(IMMUNE_COLORS.defense, 1.6)} transparent opacity={0.85} depthWrite={false}
                        blending={THREE.AdditiveBlending} toneMapped={false} />
      </points>
    </>
  );
}

function Platelets({ count = 260 }: { count?: number }) {
  const pts = useRef<THREE.Points>(null);
  const glow = useMemo(() => glowTexture(), []);
  const positions = useMemo(() => {
    const rand = rng(77);
    const p = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const a = rand() * Math.PI * 2, r = Math.sqrt(rand()) * (R - 0.5);
      p.set([(rand() - 0.5) * 36, Math.sin(a) * r, Math.cos(a) * r], i * 3);
    }
    return p;
  }, [count]);
  useFrame((_, dt) => {
    const p = pts.current;
    if (!p) return;
    const attr = p.geometry.attributes.position as THREE.BufferAttribute;
    for (let i = 0; i < count; i++) {
      let x = attr.getX(i) + dt * (0.55 + (i % 7) * 0.05);
      if (x > 18) x -= 36;
      attr.setX(i, x);
    }
    attr.needsUpdate = true;
  });
  return (
    <points ref={pts}>
      <bufferGeometry><bufferAttribute attach="attributes-position" args={[positions, 3]} /></bufferGeometry>
      <pointsMaterial size={0.05} map={glow} color={hdr("#ffc7a8", 1.1)} transparent opacity={0.55} depthWrite={false}
                      blending={THREE.AdditiveBlending} toneMapped={false} />
    </points>
  );
}

// --- white cells (the specialists) -------------------------------------------------------------------

function useMembrane(color: string) {
  return useMemo(() => new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: { uTime: { value: 0 }, uColor: { value: new THREE.Color(color) }, uActive: { value: 0 }, uHit: { value: 0 }, uSel: { value: 0 } },
    vertexShader: /* glsl */ `
      uniform float uTime, uActive; varying vec3 vN; varying vec3 vV;
      void main() {
        vec3 p = position;
        float n = sin(p.x * 5.0 + uTime * 1.3) * sin(p.y * 4.0 - uTime * 1.1) * sin(p.z * 6.0 + uTime * 0.9);
        float reach = max(0.0, dot(normalize(p), vec3(1.0, 0.0, 0.0))); // pseudopods reach toward the threat
        p += normal * (n * (0.07 + uActive * 0.05) + reach * reach * uActive * 0.18);
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        vN = normalize(normalMatrix * normal); vV = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor; uniform float uActive, uHit, uSel; varying vec3 vN; varying vec3 vV;
      void main() {
        float fres = pow(1.0 - abs(dot(vN, vV)), 1.8);
        float a = 0.16 + fres * 0.72;
        vec3 col = uColor * (0.55 + fres * 1.35 + uActive * 0.5 + uSel * 0.5) + vec3(0.84, 0.89, 0.35) * uHit * fres * 1.6;
        gl_FragColor = vec4(col, a);
      }`,
  }), [color]);
}

const CELL_SIZE: Record<string, number> = { orchestrator: 0.85, velocity: 0.55, timing: 0.5, graph: 0.48, identity: 0.46, mandate: 0.5 };

interface CellProps {
  agent: Agent;
  home: THREE.Vector3;
  chase: string | null; // pathogen it is tagging
  engulf: string | null; // pathogen it is swallowing
  hit: boolean; // under toxin fire
  presenting: boolean; // dendritic cell showing the vaccine
  selected: boolean;
  onSelect: (a: Agent) => void;
}

function WhiteCell({ agent, home, chase, engulf, hit, presenting, selected, onSelect }: CellProps) {
  const g = useRef<THREE.Group>(null);
  const size = CELL_SIZE[agent.id] ?? 0.5;
  const membrane = useMembrane(IMMUNE_COLORS.defense);
  const glow = useMemo(() => glowTexture(), []);
  const goal = useMemo(() => new THREE.Vector3(), []);
  const tmp = useMemo(() => new THREE.Vector3(), []);
  const branches = useMemo(() => {
    if (agent.id !== "graph") return [];
    const rand = rng(5);
    return Array.from({ length: 9 }, () => new THREE.Euler(rand() * Math.PI * 2, rand() * Math.PI * 2, rand() * Math.PI * 2));
  }, [agent.id]);
  const branchMat = useMemo(() => new THREE.MeshStandardMaterial({ color: "#fff1e0", roughness: 0.6, transparent: true, opacity: 0.55,
    emissive: "#ffe6c0", emissiveIntensity: 0 }), []);

  useLayoutEffect(() => {
    const grp = g.current;
    if (!grp) return;
    grp.position.copy(home);
    unitObjects.set(agent.id, grp);
    return () => { unitObjects.delete(agent.id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id]);

  useFrame(({ clock }, dt) => {
    const grp = g.current;
    if (!grp) return;
    const t = clock.elapsedTime + home.x;
    const target = (engulf && unitObjects.get(engulf)) || (chase && unitObjects.get(chase));
    if (target) {
      // Chasing: settle just upstream of the threat. Engulfing: close over it completely.
      goal.copy(target.position);
      if (!engulf) goal.add(tmp.set(-size * 1.05, 0.05, 0.2));
    } else {
      goal.copy(home).add(tmp.set(Math.sin(t * 0.3) * 0.15, Math.sin(t * 0.4) * 0.12, 0));
    }
    grp.position.lerp(goal, 1 - Math.exp(-(engulf ? 2.2 : target ? 1.2 : 0.5) * dt));
    grp.rotation.y = damp(grp.rotation.y, target ? 0 : grp.rotation.y + dt * 0.15, 2, dt);
    membrane.uniforms.uTime.value = t;
    membrane.uniforms.uActive.value = damp(membrane.uniforms.uActive.value, target || agent.status === "defending" ? 1 : 0, 3, dt);
    membrane.uniforms.uHit.value = damp(membrane.uniforms.uHit.value, hit ? 0.6 + Math.sin(t * 14) * 0.4 : 0, 6, dt);
    membrane.uniforms.uSel.value = damp(membrane.uniforms.uSel.value, selected ? 1 : 0, 6, dt);
    branchMat.emissiveIntensity = damp(branchMat.emissiveIntensity, presenting ? 1.4 + Math.sin(t * 5) * 0.4 : 0, 4, dt);
  });

  return (
    <group ref={g} scale={size}
           onClick={(e) => { e.stopPropagation(); onSelect(agent); }}
           onPointerOver={(e) => { e.stopPropagation(); setTrackHover(agent.id, true); document.body.style.cursor = "pointer"; }}
           onPointerOut={() => { setTrackHover(agent.id, false); document.body.style.cursor = ""; }}>
      <mesh material={membrane}>
        <icosahedronGeometry args={[1, 5]} />
      </mesh>
      {[[-0.25, 0.05, 0], [0.22, 0.12, 0.05], [0.02, -0.22, -0.05]].map((p, i) => (
        <mesh key={i} position={p as [number, number, number]} scale={agent.id === "velocity" ? 0.3 : 0.4 - i * 0.05}>
          <sphereGeometry args={[1, 24, 24]} />
          <meshStandardMaterial color="#5b4a9a" roughness={0.5} transparent opacity={0.55} />
        </mesh>
      ))}
      {branches.map((e, i) => (
        <group key={i} rotation={e}>
          <mesh position={[0, 1.25, 0]} material={branchMat}>
            <coneGeometry args={[0.09, 1.6, 8]} />
          </mesh>
        </group>
      ))}
      <sprite scale={2.6}>
        <spriteMaterial map={glow} color={hdr(IMMUNE_COLORS.defense, 0.45)} toneMapped={false} blending={THREE.AdditiveBlending} depthWrite={false} transparent />
      </sprite>
    </group>
  );
}

// --- pathogens (the attackers) ---------------------------------------------------------------------

function PathogenBody({ id, vaccine }: { id: string; vaccine: boolean }) {
  const color = vaccine ? "#e9f1ff" : PATHOGEN;
  const mat = useMemo(() => new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: vaccine ? 0.2 : 0.35, roughness: 0.45,
    transparent: vaccine, opacity: vaccine ? 0.6 : 1 }), [color, vaccine]);
  const rbc = useMemo(() => rbcGeometry(0.36), []);
  const spikes = useMemo(() => {
    const g = new THREE.IcosahedronGeometry(1, 1).attributes.position as THREE.BufferAttribute;
    const seen = new Set<string>();
    const out: THREE.Quaternion[] = [];
    for (let i = 0; i < g.count; i++) {
      const d = new THREE.Vector3().fromBufferAttribute(g, i).normalize();
      const k = d.toArray().map((x) => x.toFixed(2)).join();
      if (!seen.has(k)) { seen.add(k); out.push(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), d)); }
    }
    return out;
  }, []);
  const spiral = useMemo(() => new THREE.CatmullRomCurve3(Array.from({ length: 30 }, (_, i) =>
    new THREE.Vector3(i * 0.035 - 0.5, Math.sin(i * 0.9) * 0.08, Math.cos(i * 0.9) * 0.08))), []);
  const cocci = useMemo(() => { const r = rng(9); return Array.from({ length: 9 }, () => [(r() - 0.5) * 0.4, (r() - 0.5) * 0.4, (r() - 0.5) * 0.4] as [number, number, number]); }, []);

  if (id === "card-tester") {
    return <group>{[0, 1, 2].map((i) => <mesh key={i} material={mat} position={[i * 0.22 - 0.22, Math.sin(i * 2) * 0.12, 0]} rotation-z={i * 0.6}>
      <capsuleGeometry args={[0.07, 0.26, 4, 10]} /></mesh>)}</group>;
  }
  if (id === "structurer") return <mesh material={mat}><tubeGeometry args={[spiral, 120, 0.03, 8]} /></mesh>;
  if (id === "account-farmer") return <group>{cocci.map((p, i) => <mesh key={i} material={mat} position={p}><sphereGeometry args={[0.09, 16, 16]} /></mesh>)}</group>;
  if (id === "hijacker") {
    return (
      <group>
        <mesh geometry={rbc}>
          <meshPhysicalMaterial color="#6d0b14" roughness={0.4} clearcoat={0.5} emissive={PATHOGEN} emissiveIntensity={0.1} />
        </mesh>
        <mesh material={mat} scale={0.6}><sphereGeometry args={[0.1, 16, 16]} /></mesh>
        <mesh position={[0.2, 0.08, 0]}><sphereGeometry args={[0.035, 12, 12]} /><meshBasicMaterial color={hdr(PATHOGEN, 3)} toneMapped={false} /></mesh>
      </group>
    );
  }
  if (id === "prompt-injector") {
    return (
      <group rotation-z={-0.4}>
        <mesh material={mat} position={[0, 0.22, 0]}><icosahedronGeometry args={[0.15, 0]} /></mesh>
        <mesh material={mat}><cylinderGeometry args={[0.025, 0.025, 0.3, 8]} /></mesh>
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <mesh key={i} material={mat} position={[Math.cos(i) * 0.1, -0.2, Math.sin(i) * 0.1]} rotation={[Math.sin(i) * 0.7, 0, Math.cos(i) * 0.7]}>
            <cylinderGeometry args={[0.008, 0.008, 0.2, 4]} />
          </mesh>
        ))}
      </group>
    );
  }
  return (
    <group>
      <mesh material={mat}><sphereGeometry args={[0.2, 24, 24]} /></mesh>
      {spikes.map((q, i) => (
        <group key={i} quaternion={q}>
          <mesh material={mat} position={[0, 0.26, 0]}><cylinderGeometry args={[0.012, 0.012, 0.12, 4]} /></mesh>
          <mesh material={mat} position={[0, 0.33, 0]}><sphereGeometry args={[0.03, 8, 8]} /></mesh>
        </group>
      ))}
    </group>
  );
}

/** Y-shaped antibody, pointing along +y. */
function Antibody({ k = 2.6 }: { k?: number }) {
  const mat = useMemo(() => new THREE.MeshBasicMaterial({ color: hdr("#f4ecff", k), toneMapped: false }), [k]);
  return (
    <group>
      <mesh material={mat} position={[0, -0.08, 0]}><cylinderGeometry args={[0.02, 0.02, 0.16, 6]} /></mesh>
      <mesh material={mat} position={[-0.05, 0.05, 0]} rotation-z={0.6}><cylinderGeometry args={[0.02, 0.02, 0.14, 6]} /></mesh>
      <mesh material={mat} position={[0.05, 0.05, 0]} rotation-z={-0.6}><cylinderGeometry args={[0.02, 0.02, 0.14, 6]} /></mesh>
    </group>
  );
}

const COAT = 14;

interface PathogenProps {
  agent: Agent;
  tagged: boolean;
  engulfer: string | null;
  spawnFrom: THREE.Vector3 | null;
  selected: boolean;
  onSelect: (a: Agent) => void;
}

function Pathogen({ agent, tagged, engulfer, spawnFrom, selected, onSelect }: PathogenProps) {
  const g = useRef<THREE.Group>(null);
  const body = useRef<THREE.Group>(null);
  const coat = useRef<THREE.Group>(null);
  const glow = useMemo(() => glowTexture(), []);
  const hold = useMemo(() => toVessel(agent.position, agent.id), [agent.position, agent.id]);
  const start = useMemo(() => new THREE.Vector3(10 + hash01(agent.id) * 4, hold.y * 0.8, hold.z - 0.5), [hold, agent.id]);
  const vaccine = !!agent.spawnFrom;
  const coatDirs = useMemo(() => Array.from({ length: COAT }, (_, i) => {
    const y = 1 - (i / (COAT - 1)) * 2, r = Math.sqrt(1 - y * y), a = i * 2.39996;
    const d = new THREE.Vector3(Math.cos(a) * r, y, Math.sin(a) * r);
    return { pos: d.clone().multiplyScalar(0.34), quat: new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, -1, 0), d.negate()) };
  }), []);
  const coatK = useRef(0);
  const tmp = useMemo(() => new THREE.Vector3(), []);

  useLayoutEffect(() => {
    const grp = g.current;
    if (!grp) return;
    grp.position.copy(spawnFrom ?? start);
    unitObjects.set(agent.id, grp);
    return () => { unitObjects.delete(agent.id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id]);

  useFrame(({ clock }, dt) => {
    const grp = g.current;
    if (!grp) return;
    const t = clock.elapsedTime + hash01(agent.id) * 10;
    const down = agent.status === "down";
    const eater = engulfer ? unitObjects.get(engulfer) : undefined;
    if (down && eater) tmp.copy(eater.position);
    else tmp.copy(agent.status === "charging" || down ? hold : start).add(new THREE.Vector3(Math.sin(t * 0.8) * 0.08, Math.cos(t * 0.6) * 0.08, 0));
    grp.position.lerp(tmp, 1 - Math.exp(-(down ? 1.6 : 0.6) * dt));
    if (body.current) {
      body.current.rotation.x += dt * 0.4;
      body.current.rotation.y += dt * 0.6;
      body.current.scale.setScalar(damp(body.current.scale.x, down ? 0.001 : (selected ? 1.15 : 1) + Math.sin(t * 3) * 0.04, down ? 1.2 : 5, dt));
    }
    coatK.current = damp(coatK.current, (tagged || down) && !(down && body.current && body.current.scale.x < 0.2) ? 1 : 0, 1.6, dt);
    coat.current?.children.forEach((c, i) => {
      const on = coatK.current * COAT > i ? 1 : 0;
      c.scale.setScalar(damp(c.scale.x, on, 6, dt) + 1e-4);
    });
  });

  return (
    <group ref={g}
           onClick={(e) => { e.stopPropagation(); onSelect(agent); }}
           onPointerOver={(e) => { e.stopPropagation(); setTrackHover(agent.id, true); document.body.style.cursor = "pointer"; }}
           onPointerOut={() => { setTrackHover(agent.id, false); document.body.style.cursor = ""; }}>
      <group ref={body} scale={1.3}>
        <PathogenBody id={vaccine ? "vaccine" : agent.id} vaccine={vaccine} />
        <sprite scale={1.1}>
          <spriteMaterial map={glow} color={hdr(vaccine ? "#dfeaff" : PATHOGEN, 0.55)} toneMapped={false} blending={THREE.AdditiveBlending}
                          depthWrite={false} transparent />
        </sprite>
        <group ref={coat}>
          {coatDirs.map((d, i) => (
            <group key={i} position={d.pos} quaternion={d.quat} scale={0.0001}><group scale={0.55}><Antibody k={2} /></group></group>
          ))}
        </group>
      </group>
    </group>
  );
}

// --- streams: antibodies to a threat, toxins at a cell ----------------------------------------------

function Stream({ from, to, kind }: { from: string; to: string; kind: "antibody" | "toxin" }) {
  const group = useRef<THREE.Group>(null);
  const N = kind === "antibody" ? 9 : 14;
  const phases = useMemo(() => Array.from({ length: N }, (_, i) => i / N), [N]);
  const a = useMemo(() => new THREE.Vector3(), []);
  const b = useMemo(() => new THREE.Vector3(), []);
  const glow = useMemo(() => glowTexture(), []);
  useFrame(({ clock }) => {
    const oa = unitObjects.get(from), ob = unitObjects.get(to);
    if (!oa || !ob || !group.current) return;
    a.copy(oa.position);
    b.copy(ob.position);
    const lift = a.distanceTo(b) * 0.18;
    group.current.children.forEach((c, i) => {
      const k = (clock.elapsedTime * (kind === "antibody" ? 0.55 : 0.8) + phases[i]) % 1;
      c.position.lerpVectors(a, b, k);
      c.position.y += Math.sin(k * Math.PI) * lift + (kind === "toxin" ? Math.sin(clock.elapsedTime * 7 + i) * 0.05 : 0);
      c.position.z += kind === "toxin" ? Math.cos(clock.elapsedTime * 5 + i) * 0.05 : 0;
      c.rotation.z = clock.elapsedTime * 2.5 + i;
      c.scale.setScalar(Math.sin(k * Math.PI) * 0.9 + 0.1);
    });
  });
  return (
    <group ref={group}>
      {phases.map((_, i) => kind === "antibody" ? (
        <group key={i}><group scale={0.8}><Antibody /></group>
          <sprite scale={0.35}><spriteMaterial map={glow} color={hdr("#efe6ff", 1.2)} toneMapped={false} blending={THREE.AdditiveBlending} depthWrite={false} transparent /></sprite>
        </group>
      ) : (
        <sprite key={i} scale={0.16}>
          <spriteMaterial map={glow} color={hdr(PATHOGEN, 2.4)} toneMapped={false} blending={THREE.AdditiveBlending} depthWrite={false} transparent />
        </sprite>
      ))}
    </group>
  );
}

// --- immune memory ------------------------------------------------------------------------------------

function MemoryCell({ id, slot }: { id: string; slot: number }) {
  const g = useRef<THREE.Group>(null);
  const glow = useMemo(() => glowTexture(), []);
  // A row in the upper right of the frame, over where the threats were: the archive filling up.
  const to = useMemo(() => new THREE.Vector3(1.2 + slot * 0.7, 2.75 + Math.sin(slot * 1.3) * 0.12, -1.8), [slot]);
  useLayoutEffect(() => {
    const src = unitObjects.get(id);
    if (src && g.current) g.current.position.copy(src.position);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useFrame(({ clock }, dt) => {
    const grp = g.current;
    if (!grp) return;
    grp.position.lerp(to, 1 - Math.exp(-0.4 * dt));
    grp.scale.setScalar(1 + Math.sin(clock.elapsedTime * 2 + slot) * 0.08);
  });
  return (
    <group ref={g}>
      <mesh><sphereGeometry args={[0.11, 20, 20]} /><meshBasicMaterial color={hdr("#f3e9ff", 3)} toneMapped={false} /></mesh>
      <sprite scale={0.85}><spriteMaterial map={glow} color={hdr("#c9b6ff", 1.3)} toneMapped={false} blending={THREE.AdditiveBlending} depthWrite={false} transparent /></sprite>
    </group>
  );
}

// --- the world ----------------------------------------------------------------------------------------

export interface ImmuneWorldProps {
  agents: Agent[];
  battling: boolean;
  script: RoundScript | undefined;
  selectedId: string | null;
  onSelect: (a: Agent) => void;
}

export default function ImmuneWorld({ agents, battling, script, selectedId, onSelect }: ImmuneWorldProps) {
  const defenders = agents.filter((a) => a.type === "defender");
  const attackers = agents.filter((a) => a.type === "attacker");
  const alive = (id: string) => attackers.some((a) => a.id === id && a.status !== "down");
  const homes = useMemo(() => Object.fromEntries(
    agents.filter((a) => a.type === "defender").map((a) => [a.id, toVessel(a.position, a.id)])), [agents]);

  const engagements = battling && script ? script.engagements : [];
  const catches = engagements.filter((e) => e.weapon === "beam");
  const attacks = engagements.filter((e) => e.weapon === "fireball" && alive(e.from));
  // Who swallows each fallen pathogen: the white cell assigned to catch it this round (else the closest catcher).
  const engulferOf = (id: string) => catches.find((e) => e.to === id)?.from ?? null;

  // Memory cells appear in the order threats are beaten, and stay.
  const [beaten, setBeaten] = useState<string[]>([]);
  const downIds = attackers.filter((a) => a.status === "down").map((a) => a.id).join(",");
  useEffect(() => {
    const ids = downIds ? downIds.split(",") : [];
    const timer = window.setTimeout(() => setBeaten((prev) => [...prev, ...ids.filter((id) => !prev.includes(id))]), 1600);
    return () => window.clearTimeout(timer);
  }, [downIds]);

  const graphHome = homes.graph;
  const presenting = attackers.some((a) => a.spawnFrom && a.status === "charging");

  return (
    <>
      <color attach="background" args={["#2a070d"]} />
      <fog attach="fog" args={["#2a070d", 5, 24]} />
      <ambientLight intensity={0.5} color="#ffb0a0" />
      <directionalLight position={[2, 4, 6]} intensity={1.6} color="#ffe2d4" />
      <directionalLight position={[-4, -2, -3]} intensity={0.6} color="#ff6a6a" />
      <pointLight position={[0, 0, 2.5]} intensity={3} distance={12} decay={2} color="#ffd6c8" />

      <Vessel />
      <Bloodstream />
      <Platelets />

      {defenders.map((d) => {
        const chase = catches.find((e) => e.from === d.id && alive(e.to))?.to ?? null;
        const engulf = attackers.find((a) => a.status === "down" && engulferOf(a.id) === d.id && !beaten.includes(a.id))?.id ?? null;
        return (
          <WhiteCell key={d.id} agent={d} home={homes[d.id]} chase={chase} engulf={engulf}
                     hit={attacks.some((e) => e.to === d.id)} presenting={d.id === "graph" && presenting}
                     selected={d.id === selectedId} onSelect={onSelect} />
        );
      })}
      {attackers.map((a) => (
        <Pathogen key={a.id} agent={a} tagged={catches.some((e) => e.to === a.id)} engulfer={engulferOf(a.id)}
                  spawnFrom={a.spawnFrom && graphHome ? graphHome.clone() : null} selected={a.id === selectedId} onSelect={onSelect} />
      ))}

      {catches.filter((e) => alive(e.to)).map((e) => <Stream key={`ab-${e.from}-${e.to}`} from={e.from} to={e.to} kind="antibody" />)}
      {attacks.map((e) => <Stream key={`tx-${e.from}-${e.to}`} from={e.from} to={e.to} kind="toxin" />)}

      {beaten.map((id, i) => <MemoryCell key={id} id={id} slot={i} />)}
    </>
  );
}
