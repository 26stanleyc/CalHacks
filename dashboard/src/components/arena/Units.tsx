"use client";

import { Sparkles } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";

import { useBrass } from "./Battlefield";
import type { Agent } from "./data";
import { fxApi, shieldHits, unitObjects } from "./fx";
import { glowTexture } from "./textures";
import { setTrackHover } from "./Tracking";

const hdr = (hex: string, k: number) => new THREE.Color(hex).multiplyScalar(k);
const damp = (current: number, target: number, rate: number, dt: number) =>
  THREE.MathUtils.lerp(current, target, 1 - Math.exp(-rate * dt));

export const PEARL = "#a9c8ff";
export const EMBER = "#ff8a4c";

function rng(seed: number) {
  return () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
}

/** A soft pool of light on the floor under a unit. */
function LightPool({ color, size, poolRef }: { color: string; size: number; poolRef?: React.RefObject<THREE.Mesh | null> }) {
  const glow = useMemo(() => glowTexture(), []);
  return (
    <mesh ref={poolRef} rotation-x={-Math.PI / 2} position={[0, -0.215, 0]} scale={size}>
      <planeGeometry args={[1, 1]} />
      <meshBasicMaterial map={glow} color={hdr(color, 0.9)} toneMapped={false} transparent opacity={0.32}
                         blending={THREE.AdditiveBlending} depthWrite={false} />
    </mesh>
  );
}

function Glow({ color, size, k = 1.4 }: { color: string; size: number; k?: number }) {
  const glow = useMemo(() => glowTexture(), []);
  return (
    <sprite scale={size}>
      <spriteMaterial map={glow} color={hdr(color, k)} toneMapped={false} blending={THREE.AdditiveBlending} depthWrite={false} transparent />
    </sprite>
  );
}

// --- Shield: a glass bubble with a fresnel rim; every hit sends a ripple out from where it landed ----

function useShield(color: string) {
  return useMemo(
    () =>
      new THREE.ShaderMaterial({
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        uniforms: {
          uTime: { value: 0 }, uHit: { value: 0 }, uAge: { value: 10 }, uDir: { value: new THREE.Vector3(1, 0, 0) },
          uOpacity: { value: 0 }, uColor: { value: new THREE.Color(color) },
        },
        vertexShader: /* glsl */ `
          varying vec3 vN; varying vec3 vV; varying vec3 vP;
          void main() {
            vec4 mv = modelViewMatrix * vec4(position, 1.0);
            vN = normalize(normalMatrix * normal); vV = normalize(-mv.xyz); vP = position;
            gl_Position = projectionMatrix * mv;
          }`,
        fragmentShader: /* glsl */ `
          varying vec3 vN; varying vec3 vV; varying vec3 vP;
          uniform float uTime, uHit, uAge, uOpacity; uniform vec3 uDir, uColor;
          void main() {
            vec3 p = normalize(vP);
            float fres = pow(1.0 - abs(dot(vN, vV)), 3.0);
            float d = acos(clamp(dot(p, uDir), -1.0, 1.0));
            float ring = exp(-pow((d - uAge * 2.6) * 7.0, 2.0)) * uHit;
            float core = exp(-d * 4.0) * uHit * 0.5;
            float shimmer = 0.5 + 0.5 * sin(p.y * 14.0 - uTime * 0.8 + p.x * 6.0);
            float a = (fres * (0.45 + shimmer * 0.15) + ring * 1.3 + core) * uOpacity;
            gl_FragColor = vec4(uColor * (1.1 + ring * 2.4) * a, a);
          }`,
      }),
    [color],
  );
}

// --- Sentinel (defender): iridescent glass, a pearl core, a brass gyroscope --------------------

function Sentinel({ agent }: { agent: Agent }) {
  const body = useRef<THREE.Group>(null);
  const gyroA = useRef<THREE.Mesh>(null);
  const gyroB = useRef<THREE.Mesh>(null);
  const light = useRef<THREE.PointLight>(null);
  const shieldMesh = useRef<THREE.Mesh>(null);
  const brass = useBrass();
  const shield = useShield(PEARL);
  const local = useMemo(() => new THREE.Vector3(), []);
  const phase = useMemo(() => agent.id.charCodeAt(1) * 1.7, [agent.id]);
  const down = agent.status === "down";

  useFrame(({ clock }, dt) => {
    const t = clock.elapsedTime + phase;
    const defending = agent.status === "defending";
    if (body.current) {
      body.current.position.y = damp(body.current.position.y, down ? -0.2 : 0.16 + Math.sin(t * 1.1) * 0.05, 3, dt);
      body.current.rotation.y += dt * (defending ? 0.6 : 0.18);
    }
    if (gyroA.current) gyroA.current.rotation.x += dt * (defending ? 1.6 : 0.5);
    if (gyroB.current) gyroB.current.rotation.z -= dt * (defending ? 1.2 : 0.35);
    if (light.current) light.current.intensity = damp(light.current.intensity, down ? 0 : defending ? 4 : 2.2, 3, dt);

    const hit = shieldHits.get(agent.id);
    if (hit && shieldMesh.current) {
      hit.strength = Math.max(0, hit.strength - dt * 1.1);
      local.copy(hit.point);
      shieldMesh.current.worldToLocal(local);
      shield.uniforms.uDir.value.copy(local.normalize());
      shield.uniforms.uHit.value = hit.strength;
      shield.uniforms.uAge.value = clock.elapsedTime - hit.at;
    }
    shield.uniforms.uTime.value = t;
    shield.uniforms.uOpacity.value = damp(shield.uniforms.uOpacity.value, defending ? 1 : 0, 3, dt);
  });

  return (
    <group>
      <group ref={body}>
        <mesh scale={[0.55, 1.85, 0.55]} castShadow>
          <octahedronGeometry args={[0.3, 0]} />
          <meshPhysicalMaterial color={down ? "#5b6170" : "#f3f7ff"} transmission={down ? 0.1 : 1} thickness={0.9} roughness={0.05}
                                ior={1.5} iridescence={1} iridescenceIOR={1.35} iridescenceThicknessRange={[180, 640]} clearcoat={1}
                                clearcoatRoughness={0.04} attenuationColor="#c9dcff" attenuationDistance={1.4} envMapIntensity={1.6}
                                flatShading />
        </mesh>
        {!down && (
          <>
            <mesh>
              <sphereGeometry args={[0.06, 24, 24]} />
              <meshBasicMaterial color={hdr("#dbe8ff", 5)} toneMapped={false} />
            </mesh>
            <Glow color={PEARL} size={0.5} />
          </>
        )}
        <mesh ref={gyroA} material={brass}>
          <torusGeometry args={[0.44, 0.006, 8, 128]} />
        </mesh>
        <mesh ref={gyroB} material={brass} rotation-x={Math.PI / 2}>
          <torusGeometry args={[0.52, 0.005, 8, 128]} />
        </mesh>
      </group>
      {!down && <LightPool color={PEARL} size={1.5} />}
      <mesh ref={shieldMesh} position={[0, 0.16, 0]} material={shield}>
        <sphereGeometry args={[0.8, 64, 48]} />
      </mesh>
      <pointLight ref={light} position={[0, 0.2, 0]} color="#b4cdff" intensity={2.2} distance={3.4} decay={2} />
    </group>
  );
}

// --- Infernal (attacker): obsidian shards around a smoldering ember -----------------------------

const SHARDS = 7;

function Infernal({ agent }: { agent: Agent }) {
  const body = useRef<THREE.Group>(null);
  const core = useRef<THREE.Group>(null);
  const light = useRef<THREE.PointLight>(null);
  const shardRefs = useRef<(THREE.Group | null)[]>([]);
  const edgeMat = useMemo(() => new THREE.LineBasicMaterial({ color: hdr(EMBER, 1.6), toneMapped: false, transparent: true, opacity: 0.7 }), []);
  const phase = useMemo(() => agent.id.charCodeAt(1) * 2.3, [agent.id]);
  const down = agent.status === "down";
  const charging = agent.status === "charging";

  const { shards, edges } = useMemo(() => {
    const rand = rng(agent.id.charCodeAt(1) * 97);
    const golden = Math.PI * (3 - Math.sqrt(5));
    const shards = Array.from({ length: SHARDS }, (_, i) => {
      const y = 1 - (i / (SHARDS - 1)) * 2;
      const r = Math.sqrt(1 - y * y);
      const dir = new THREE.Vector3(Math.cos(golden * i) * r, y * 0.8, Math.sin(golden * i) * r).normalize();
      const quat = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
      const a = rand() * Math.PI * 2, d = 0.25 + rand() * 0.45;
      const rest = new THREE.Vector3(Math.cos(a) * d, -0.17, Math.sin(a) * d);
      const restQuat = new THREE.Quaternion().setFromEuler(new THREE.Euler(Math.PI / 2, rand() * Math.PI * 2, 0, "YXZ"));
      return { dir, quat, rest, restQuat, len: 0.8 + rand() * 0.5 };
    });
    return { shards, edges: new THREE.EdgesGeometry(new THREE.OctahedronGeometry(0.1, 0)) };
  }, [agent.id]);

  useFrame(({ clock }, dt) => {
    const t = clock.elapsedTime + phase;
    if (body.current) {
      body.current.position.y = damp(body.current.position.y, down ? 0 : 0.16 + Math.sin(t * 1.6) * 0.05, 3, dt);
      if (!down) {
        body.current.rotation.y += dt * (charging ? 0.9 : 0.25);
        body.current.rotation.x = Math.sin(t * 0.4) * 0.25;
      } else {
        body.current.rotation.x = damp(body.current.rotation.x, 0, 3, dt);
      }
    }
    const radius = charging ? 0.2 + Math.sin(t * 7) * 0.012 : 0.27;
    shardRefs.current.forEach((g, i) => {
      if (!g) return;
      const s = shards[i];
      if (down) {
        g.position.lerp(s.rest, 1 - Math.exp(-4 * dt));
        g.quaternion.slerp(s.restQuat, 1 - Math.exp(-4 * dt));
      } else {
        const wobble = 1 + Math.sin(t * 2 + i * 1.3) * 0.06;
        g.position.copy(s.dir).multiplyScalar(radius * wobble);
        g.quaternion.copy(s.quat);
      }
    });
    const heat = down ? 0 : (charging ? 1.2 : 0.7) * (0.85 + Math.sin(t * 3.1) * 0.15);
    edgeMat.color.copy(hdr(EMBER, 0.3 + heat * 1.6));
    if (core.current) core.current.scale.setScalar(damp(core.current.scale.x, down ? 0.001 : 0.85 + heat * 0.25, 4, dt));
    if (light.current) light.current.intensity = damp(light.current.intensity, heat * 3.2, 4, dt);
  });

  return (
    <group>
      <group ref={body}>
        <group ref={core}>
          <mesh>
            <sphereGeometry args={[0.075, 24, 24]} />
            <meshBasicMaterial color={hdr("#ffb27a", 6)} toneMapped={false} />
          </mesh>
          <Glow color={EMBER} size={0.6} k={1.6} />
        </group>
        {shards.map((s, i) => (
          <group key={i} ref={(el) => { shardRefs.current[i] = el; }}>
            <group scale={[0.55, 2.1 * s.len, 0.55]}>
              <mesh castShadow>
                <octahedronGeometry args={[0.1, 0]} />
                <meshPhysicalMaterial color={down ? "#1a1b20" : "#08090c"} roughness={0.14} metalness={0.5} clearcoat={1}
                                      clearcoatRoughness={0.06} envMapIntensity={1.5} emissive="#ff5b22"
                                      emissiveIntensity={down ? 0 : 0.03} flatShading />
              </mesh>
              <lineSegments geometry={edges} material={edgeMat} />
            </group>
          </group>
        ))}
      </group>
      {!down && <LightPool color={EMBER} size={1.3} />}
      {down && <Sparkles count={16} scale={[0.9, 1.6, 0.9]} position={[0, 0.5, 0]} size={1.4} speed={0.22} opacity={0.5} color="#9a98a2" />}
      <pointLight ref={light} position={[0, 0.18, 0]} color="#ff8a4c" intensity={2.2} distance={3.2} decay={2} />
    </group>
  );
}

// --- A unit on the field: glides to new positions, breaks apart when it falls, can be selected ------

export function Unit({ agent, selected, onSelect }: { agent: Agent; selected: boolean; onSelect: (a: Agent) => void }) {
  const root = useRef<THREE.Group>(null);
  const marker = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState(false);
  const prevStatus = useRef(agent.status);
  const target = useMemo(() => new THREE.Vector3(...agent.position), [agent.position]);

  useLayoutEffect(() => {
    const obj = root.current;
    // A spawned attacker emerges from the specialist that built it, then glides to its post.
    const parent = agent.spawnFrom ? unitObjects.get(agent.spawnFrom) : undefined;
    if (parent) obj?.position.copy(parent.position);
    else obj?.position.set(...agent.position);
    if (obj && agent.appearsAt) {
      fxApi.shockwave([obj.position.x, 0, obj.position.z], EMBER, 1.2);
      fxApi.impact([obj.position.x, obj.position.y + 0.2, obj.position.z], EMBER, 1.2);
    }
    obj?.scale.setScalar(agent.size ?? 1);
    if (obj) unitObjects.set(agent.id, obj);
    return () => {
      unitObjects.delete(agent.id);
    };
    // only on mount: afterwards the unit glides to new positions
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id]);

  useEffect(() => {
    if (agent.status === "down" && prevStatus.current !== "down" && root.current) {
      const p = root.current.position;
      fxApi.explosion([p.x, p.y + 0.15, p.z]);
    }
    prevStatus.current = agent.status;
  }, [agent.status]);

  useEffect(() => () => {
    document.body.style.cursor = "";
  }, []);

  useFrame((_, dt) => {
    const r = root.current;
    if (!r) return;
    r.position.lerp(target, 1 - Math.exp(-1.8 * dt));
    const size = agent.size ?? 1;
    r.scale.setScalar(damp(r.scale.x, size * (hovered ? 1.05 : 1), 8, dt));
    const m = marker.current;
    if (m) {
      const mat = m.material as THREE.MeshBasicMaterial;
      mat.opacity = damp(mat.opacity, selected ? 0.9 : hovered ? 0.5 : 0, 8, dt);
      m.visible = mat.opacity > 0.01;
    }
  });

  return (
    <group
      ref={root}
      onClick={(e) => {
        e.stopPropagation();
        onSelect(agent);
      }}
      onPointerOver={(e) => {
        e.stopPropagation();
        setHovered(true);
        setTrackHover(agent.id, true);
        document.body.style.cursor = "pointer";
      }}
      onPointerOut={() => {
        setHovered(false);
        setTrackHover(agent.id, false);
        document.body.style.cursor = "";
      }}
    >
      {agent.type === "defender" ? <Sentinel agent={agent} /> : <Infernal agent={agent} />}
      <mesh ref={marker} rotation-x={-Math.PI / 2} position={[0, -0.212, 0]}>
        <ringGeometry args={[0.66, 0.672, 96]} />
        <meshBasicMaterial color={hdr("#f1f5ff", 2)} toneMapped={false} transparent opacity={0} depthWrite={false} />
      </mesh>
    </group>
  );
}
