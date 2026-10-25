"use client";

import { Environment, Lightformer, MeshReflectorMaterial } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";

import { glowTexture } from "./textures";

// "Astrolabe": a polished black-stone dais engraved like an astrolabe, under a slowly turning brass
// armillary, lit by a single soft shaft of light. Midnight blue, pearl and ember; nothing neon.

const TAU = Math.PI * 2;
export const DAIS_RADIUS = 5.4;

function rng(seed: number) {
  return () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
}

const hdr = (hex: string, k: number) => new THREE.Color(hex).multiplyScalar(k);

/** Shared brass for the armillary and the sentinels' gyroscopes. */
export function useBrass() {
  return useMemo(
    () => new THREE.MeshStandardMaterial({ color: "#c9a56b", metalness: 1, roughness: 0.26, envMapIntensity: 1.3 }),
    [],
  );
}

// --- Sky: midnight gradient, a cool glow behind the sentinels and a faint ember one behind the infernals

function Sky() {
  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        side: THREE.BackSide,
        depthWrite: false,
        vertexShader: /* glsl */ `
          varying vec3 vDir;
          void main() { vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
        fragmentShader: /* glsl */ `
          varying vec3 vDir;
          float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
          void main() {
            float h = vDir.y;
            float side = smoothstep(-0.8, 0.8, vDir.x);
            vec3 zenith = vec3(0.006, 0.009, 0.018);
            vec3 horizon = mix(vec3(0.018, 0.03, 0.06), vec3(0.05, 0.028, 0.022), side);
            vec3 col = mix(horizon, zenith, smoothstep(-0.05, 0.55, h));
            col += mix(vec3(0.02, 0.04, 0.09), vec3(0.09, 0.035, 0.015), side) * pow(1.0 - abs(h), 14.0);
            col += (hash(gl_FragCoord.xy) - 0.5) / 255.0; // dither: no banding in the gradient
            gl_FragColor = vec4(col, 1.0);
          }`,
      }),
    [],
  );
  return (
    <mesh material={material} scale={110} renderOrder={-1}>
      <sphereGeometry args={[1, 48, 24]} />
    </mesh>
  );
}

// --- Floor engraving: concentric rings, degree ticks and faint spokes, fading into the dark -----

function Engraving() {
  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        uniforms: { uTime: { value: 0 } },
        vertexShader: /* glsl */ `
          varying vec2 vP;
          void main() { vP = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
        fragmentShader: /* glsl */ `
          varying vec2 vP;
          uniform float uTime;
          float line(float d, float w) { return 1.0 - smoothstep(0.0, w, abs(d)); }
          void main() {
            float r = length(vP);
            float a = atan(vP.y, vP.x);
            float fade = smoothstep(16.0, 6.5, r) * smoothstep(5.7, 6.1, r);
            float rings = line(r - 6.6, 0.012) + line(r - 8.4, 0.01) * 0.7 + line(r - 11.5, 0.01) * 0.45;
            float tick = step(fract(a / (3.14159265 / 36.0) + 0.5), 0.06) * step(6.6, r) * step(r, 7.05);
            float major = step(fract(a / (3.14159265 / 6.0) + 0.5), 0.012) * step(6.6, r) * step(r, 7.6);
            float spokes = step(fract(a / (3.14159265 / 6.0) + 0.5), 0.004) * smoothstep(13.0, 8.0, r) * step(7.6, r) * 0.35;
            float sweep = pow(max(0.0, cos(a - uTime * 0.12)), 40.0) * line(r - 6.6, 0.05) * 1.5;
            float v = (rings + tick * 0.8 + major + spokes) * fade + sweep * fade;
            vec3 col = mix(vec3(0.62, 0.74, 0.95), vec3(1.0, 0.7, 0.5), smoothstep(-6.0, 6.0, vP.x));
            gl_FragColor = vec4(col * v * 0.55, 1.0);
          }`,
      }),
    [],
  );
  useFrame(({ clock }) => {
    material.uniforms.uTime.value = clock.elapsedTime;
  });
  return (
    <mesh rotation-x={-Math.PI / 2} position={[0, -0.405, 0]} material={material}>
      <planeGeometry args={[34, 34]} />
    </mesh>
  );
}

// --- The dais: polished stone with an inlaid astrolabe face, edged by a hairline of light -------

function Dais() {
  const face = useMemo(
    () =>
      new THREE.ShaderMaterial({
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        uniforms: { uTime: { value: 0 } },
        vertexShader: /* glsl */ `
          varying vec2 vP;
          void main() { vP = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
        fragmentShader: /* glsl */ `
          varying vec2 vP;
          uniform float uTime;
          float line(float d, float w) { return 1.0 - smoothstep(0.0, w, abs(d)); }
          void main() {
            float r = length(vP);
            float a = atan(vP.y, vP.x);
            float rings = 0.0;
            for (int i = 1; i <= 5; i++) rings += line(r - float(i), 0.008) * (i == 5 ? 1.2 : 0.55);
            vec2 g = abs(fract(vP + 0.5) - 0.5);
            float dot_ = (1.0 - smoothstep(0.018, 0.04, length(g))) * step(r, 4.6);
            float cross_ = (line(g.x, 0.004) * step(g.y, 0.07) + line(g.y, 0.004) * step(g.x, 0.07)) * step(r, 4.6) * 0.6;
            float ticks = step(fract(a / (3.14159265 / 60.0) + 0.5), 0.08) * step(4.86, r) * step(r, 5.0);
            float axis = line(vP.x, 0.006) * step(r, 4.9) * 0.25;
            float breathe = 0.8 + 0.2 * sin(uTime * 0.6 - r * 0.9);
            float v = (rings + dot_ * 0.9 + cross_ + ticks * 0.8 + axis) * breathe;
            vec3 col = mix(vec3(0.66, 0.78, 1.0), vec3(1.0, 0.72, 0.52), smoothstep(-3.5, 3.5, vP.x));
            gl_FragColor = vec4(col * v * 0.42, 1.0);
          }`,
      }),
    [],
  );
  useFrame(({ clock }) => {
    face.uniforms.uTime.value = clock.elapsedTime;
  });
  return (
    <group>
      <mesh position={[0, -0.27, 0]} receiveShadow castShadow>
        <cylinderGeometry args={[DAIS_RADIUS, DAIS_RADIUS + 0.12, 0.3, 128]} />
        <meshPhysicalMaterial color="#05060a" roughness={0.55} metalness={0.2} clearcoat={0.5} clearcoatRoughness={0.45}
                              envMapIntensity={0.18} />
      </mesh>
      <mesh rotation-x={-Math.PI / 2} position={[0, -0.118, 0]} material={face}>
        <circleGeometry args={[DAIS_RADIUS - 0.05, 128]} />
      </mesh>
      <mesh rotation-x={Math.PI / 2} position={[0, -0.12, 0]}>
        <torusGeometry args={[DAIS_RADIUS, 0.01, 8, 256]} />
        <meshBasicMaterial color={hdr("#e8eefc", 2.4)} toneMapped={false} />
      </mesh>
    </group>
  );
}

// --- The armillary: three brass rings turning slowly overhead, with a few beads ------------------

function Armillary() {
  const brass = useBrass();
  const rings = useRef<(THREE.Group | null)[]>([]);
  const specs = useMemo(() => [
    { r: 6.4, tube: 0.035, tilt: [0.32, 0, 0.12] as const, speed: 0.05 },
    { r: 7.3, tube: 0.026, tilt: [-0.22, 0, -0.34] as const, speed: -0.035 },
    { r: 8.2, tube: 0.02, tilt: [0.08, 0, 0.42] as const, speed: 0.022 },
  ], []);
  useFrame((_, dt) => {
    rings.current.forEach((g, i) => {
      if (g) g.rotation.y += specs[i].speed * dt;
    });
  });
  return (
    <group position={[0, 5.6, 0]}>
      {specs.map((s, i) => (
        <group key={i} rotation={[s.tilt[0], s.tilt[1], s.tilt[2]]}>
          <group ref={(el) => { rings.current[i] = el; }}>
            <mesh rotation-x={Math.PI / 2} material={brass} castShadow>
              <torusGeometry args={[s.r, s.tube, 12, 256]} />
            </mesh>
            {[0, 1, 2].map((k) => {
              const a = (k / 3) * TAU + i;
              return (
                <mesh key={k} position={[Math.cos(a) * s.r, 0, Math.sin(a) * s.r]} material={brass}>
                  <sphereGeometry args={[s.tube * 2.6, 16, 16]} />
                </mesh>
              );
            })}
          </group>
        </group>
      ))}
      <mesh material={brass}>
        <sphereGeometry args={[0.09, 24, 24]} />
      </mesh>
    </group>
  );
}

// --- Light shaft through the armillary, with dust drifting in it ----------------------------------

function LightShaft() {
  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        transparent: true,
        depthWrite: false,
        side: THREE.DoubleSide,
        blending: THREE.AdditiveBlending,
        uniforms: { uTime: { value: 0 } },
        vertexShader: /* glsl */ `
          varying vec2 vUv; varying vec3 vN; varying vec3 vV;
          void main() {
            vUv = uv;
            vec4 mv = modelViewMatrix * vec4(position, 1.0);
            vN = normalize(normalMatrix * normal); vV = normalize(-mv.xyz);
            gl_Position = projectionMatrix * mv;
          }`,
        fragmentShader: /* glsl */ `
          varying vec2 vUv; varying vec3 vN; varying vec3 vV;
          uniform float uTime;
          void main() {
            float facing = pow(abs(dot(vN, vV)), 1.6);
            float fall = smoothstep(0.08, 0.5, vUv.y) * smoothstep(1.0, 0.6, vUv.y);
            float drift = 0.85 + 0.15 * sin(vUv.x * 18.0 + uTime * 0.3) * sin(vUv.y * 6.0 - uTime * 0.2);
            float a = facing * fall * drift * 0.03;
            gl_FragColor = vec4(vec3(1.0, 0.95, 0.86) * a, a);
          }`,
      }),
    [],
  );
  useFrame(({ clock }) => {
    material.uniforms.uTime.value = clock.elapsedTime;
  });
  return (
    <mesh position={[0, 5.4, 0]} material={material}>
      <cylinderGeometry args={[0.9, DAIS_RADIUS - 0.4, 11, 64, 1, true]} />
    </mesh>
  );
}

function Dust({ count = 420 }: { count?: number }) {
  const points = useRef<THREE.Points>(null);
  const { positions, seeds } = useMemo(() => {
    const rand = rng(9);
    const positions = new Float32Array(count * 3);
    const seeds = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      const a = rand() * TAU;
      const r = Math.sqrt(rand()) * 5.2;
      positions.set([Math.cos(a) * r, rand() * 9, Math.sin(a) * r], i * 3);
      seeds[i] = rand() * 100;
    }
    return { positions, seeds };
  }, [count]);
  useFrame(({ clock }, dt) => {
    const p = points.current;
    if (!p) return;
    const attr = p.geometry.attributes.position as THREE.BufferAttribute;
    const t = clock.elapsedTime;
    for (let i = 0; i < count; i++) {
      const s = seeds[i];
      let y = attr.getY(i) + (0.05 + (s % 1) * 0.06) * dt;
      if (y > 9) y = 0;
      attr.setXYZ(i, attr.getX(i) + Math.sin(t * 0.21 + s) * 0.0016, y, attr.getZ(i) + Math.cos(t * 0.17 + s) * 0.0016);
    }
    attr.needsUpdate = true;
  });
  return (
    <points ref={points}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial size={0.035} map={glowTexture()} color={hdr("#fff3dc", 1.6)} transparent opacity={0.7} depthWrite={false}
                      blending={THREE.AdditiveBlending} toneMapped={false} sizeAttenuation />
    </points>
  );
}

// --- Everything together ----------------------------------------------------------------------------

export default function Battlefield() {
  return (
    <>
      <color attach="background" args={["#03050a"]} />
      <fogExp2 attach="fog" args={["#04060b", 0.04]} />
      <Sky />

      {/* Studio light panels, baked into an environment map for reflections on glass, brass and stone. */}
      <Environment resolution={256} frames={1} environmentIntensity={0.55}>
        <Lightformer form="rect" intensity={1.4} color="#fff4e6" position={[0, 9, 0]} rotation-x={Math.PI / 2} scale={[6, 6, 1]} />
        <Lightformer form="rect" intensity={1.4} color="#9fc2ff" position={[-9, 3, 2]} rotation-y={Math.PI / 2} scale={[4, 12, 1]} />
        <Lightformer form="rect" intensity={1.1} color="#ffb48a" position={[9, 3, -2]} rotation-y={-Math.PI / 2} scale={[4, 12, 1]} />
        <Lightformer form="ring" intensity={0.8} color="#ffffff" position={[0, 4, -10]} scale={6} />
      </Environment>

      <ambientLight intensity={0.06} color="#9bb4ff" />
      <spotLight position={[0, 15, 1.5]} angle={0.42} penumbra={1} intensity={2.1} decay={0} color="#fff1df" castShadow
                 shadow-mapSize={[2048, 2048]} shadow-bias={-0.0002} shadow-radius={6} />
      <directionalLight position={[-9, 5, -8]} intensity={0.55} color="#8fb0ff" />
      <directionalLight position={[10, 3, -6]} intensity={0.45} color="#ff9a62" />

      <mesh rotation-x={-Math.PI / 2} position={[0, -0.42, 0]}>
        <planeGeometry args={[200, 200]} />
        <MeshReflectorMaterial blur={[500, 140]} resolution={512} mixBlur={1} mixStrength={1.2} mixContrast={1} roughness={1}
                               depthScale={0.9} minDepthThreshold={0.25} maxDepthThreshold={1.4} color="#030408" metalness={0.4}
                               mirror={0.45} />
      </mesh>

      <Engraving />
      <Dais />
      <Armillary />
      <LightShaft />
      <Dust />
    </>
  );
}
