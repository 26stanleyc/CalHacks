"use client";

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";

import { type Agent, type Engagement, TIMING, type Vec3 } from "./data";
import { addShake, fx, fxApi, hitShield, unitObjects } from "./fx";
import { glowTexture, ringTexture } from "./textures";
import { EMBER, PEARL } from "./Units";

// Combat, kept quiet: ember comets with fading trails, threads of light that draw themselves to
// their target, soft flashes, floor ripples and a little ash. Everything is pooled and driven by
// one per-frame pass; nothing allocates during the fight except a thread's curve.

const hdr = (hex: string, k: number) => new THREE.Color(hex).multiplyScalar(k);
const FLOOR_Y = -0.11;
const TRAIL = 22;
// Pool sizes: embers, threads, flashes, floor ripples, ash motes, lights, sparks.
const P = 36, B = 10, F = 24, W = 12, A = 18, L = 4, SPARKS = 600;
const THREAD_DRAW = 0.24, THREAD_LIFE = 0.85;

interface Shot { at: number; from: string; to: string; weapon: Engagement["weapon"] }
interface Ember { active: boolean; t: number; duration: number; to: string; start: THREE.Vector3; bend: THREE.Vector3;
  trail: Float32Array; line: THREE.Line }
interface Thread { active: boolean; t: number; from: string; to: string; hit: boolean; curve: THREE.QuadraticBezierCurve3 | null;
  mesh: THREE.Mesh | null; material: THREE.ShaderMaterial }
interface Burst { active: boolean; t: number; life: number; size: number; color: THREE.Color }

function aimPoint(id: string, out: THREE.Vector3) {
  const obj = unitObjects.get(id);
  if (obj) obj.getWorldPosition(out);
  return out.setY(out.y + 0.18);
}

function threadMaterial() {
  return new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: { uProgress: { value: 0 }, uFade: { value: 1 }, uColor: { value: hdr(PEARL, 1) } },
    vertexShader: /* glsl */ `
      varying vec2 vUv;
      void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: /* glsl */ `
      varying vec2 vUv;
      uniform float uProgress, uFade; uniform vec3 uColor;
      void main() {
        float x = vUv.x;
        if (x > uProgress) discard;
        float head = exp(-pow((x - uProgress) * 12.0, 2.0));
        float tail = smoothstep(0.0, 0.12, x);
        float a = (0.35 + head * 1.4) * tail * uFade;
        gl_FragColor = vec4(uColor * (1.6 + head * 5.0) * a, a);
      }`,
  });
}

export default function BattleFX({ round, engagements, agents }: { round: number; engagements: Engagement[]; agents: Agent[] }) {
  const glow = useMemo(() => glowTexture(), []);
  const ring = useMemo(() => ringTexture(), []);
  const agentsRef = useRef(agents);
  useEffect(() => {
    agentsRef.current = agents;
  }, [agents]);

  const embers = useMemo<Ember[]>(() => {
    // Trail colors run from hot at the head to nothing at the tail (additive, so dark = invisible).
    const colors = new Float32Array(TRAIL * 3);
    const c = hdr(EMBER, 3);
    for (let i = 0; i < TRAIL; i++) {
      const k = Math.pow(1 - i / (TRAIL - 1), 1.6);
      colors.set([c.r * k, c.g * k, c.b * k], i * 3);
    }
    const material = new THREE.LineBasicMaterial({ vertexColors: true, toneMapped: false, transparent: true,
      blending: THREE.AdditiveBlending, depthWrite: false });
    return Array.from({ length: P }, () => {
      const trail = new Float32Array(TRAIL * 3);
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(trail, 3));
      geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
      const line = new THREE.Line(geometry, material);
      line.frustumCulled = false;
      return { active: false, t: 0, duration: 1, to: "", start: new THREE.Vector3(), bend: new THREE.Vector3(), trail, line };
    });
  }, []);
  const threads = useMemo<Thread[]>(() => Array.from({ length: B }, () => ({
    active: false, t: 0, from: "", to: "", hit: false, curve: null, mesh: null, material: threadMaterial(),
  })), []);
  const flashes = useMemo<Burst[]>(() => Array.from({ length: F }, () => ({ active: false, t: 0, life: 0.4, size: 1, color: new THREE.Color() })), []);
  const ripples = useMemo<Burst[]>(() => Array.from({ length: W }, () => ({ active: false, t: 0, life: 1.2, size: 1, color: new THREE.Color() })), []);
  const ash = useMemo(() => Array.from({ length: A }, () => ({ active: false, t: 0, life: 3, v: new THREE.Vector3() })), []);
  const lights = useMemo(() => Array.from({ length: L }, () => ({ t: 1, life: 0.4, peak: 0 })), []);
  const sparks = useMemo(() => ({
    pos: new Float32Array(SPARKS * 3), vel: new Float32Array(SPARKS * 3), life: new Float32Array(SPARKS),
    base: new Float32Array(SPARKS * 3), col: new Float32Array(SPARKS * 3), next: 0,
  }), []);

  const emberRefs = useRef<(THREE.Group | null)[]>([]);
  const threadHeads = useRef<(THREE.Sprite | null)[]>([]);
  const threadGroup = useRef<THREE.Group>(null);
  const flashRefs = useRef<(THREE.Sprite | null)[]>([]);
  const rippleRefs = useRef<(THREE.Mesh | null)[]>([]);
  const ashRefs = useRef<(THREE.Sprite | null)[]>([]);
  const lightRefs = useRef<(THREE.PointLight | null)[]>([]);
  const sparkPoints = useRef<THREE.Points>(null);

  const schedule = useRef<Shot[]>([]);
  const lastRound = useRef(0);
  const tmp = useMemo(() => ({ a: new THREE.Vector3(), b: new THREE.Vector3(), c: new THREE.Vector3(), d: new THREE.Vector3() }), []);

  // --- spawners --------------------------------------------------------------------------------
  const api = useMemo(() => {
    const free = <T extends { active: boolean; t: number }>(pool: T[]) =>
      pool.find((x) => !x.active) ?? pool.reduce((oldest, x) => (x.t > oldest.t ? x : oldest), pool[0]);
    const spawnSparks = (p: Vec3, color: string, count: number, speed: number) => {
      const c = hdr(color, 3);
      for (let k = 0; k < count; k++) {
        const i = sparks.next;
        sparks.next = (sparks.next + 1) % SPARKS;
        const dir = new THREE.Vector3(Math.random() - 0.5, Math.random() * 0.8 + 0.2, Math.random() - 0.5).normalize();
        const v = speed * (0.3 + Math.random() * 0.8);
        sparks.pos.set(p, i * 3);
        sparks.vel.set([dir.x * v, dir.y * v, dir.z * v], i * 3);
        sparks.life[i] = 0.6 + Math.random() * 0.8;
        sparks.base.set([c.r, c.g, c.b], i * 3);
      }
    };
    const flash = (p: Vec3, color: string, size: number, life = 0.4) => {
      const f = free(flashes);
      Object.assign(f, { active: true, t: 0, life, size });
      f.color.copy(hdr(color, 2.2));
      flashRefs.current[flashes.indexOf(f)]?.position.set(...p);
    };
    const ripple = (p: Vec3, color: string, size = 1, life = 1.2) => {
      const w = free(ripples);
      Object.assign(w, { active: true, t: 0, life, size });
      w.color.copy(hdr(color, 1.6));
      rippleRefs.current[ripples.indexOf(w)]?.position.set(p[0], FLOOR_Y, p[2]);
    };
    const light = (p: Vec3, color: string, peak: number, life: number) => {
      const i = lights.reduce((best, l, k) => (l.t / l.life > lights[best].t / lights[best].life ? k : best), 0);
      Object.assign(lights[i], { t: 0, life, peak });
      const l = lightRefs.current[i];
      if (l) {
        l.position.set(...p);
        l.color.set(color);
      }
    };
    return {
      flash, ripple,
      impact(p: Vec3, color: string, size = 1) {
        flash(p, color, 0.65 * size);
        ripple(p, color, 0.45 * size, 0.9);
        spawnSparks(p, color, Math.round(10 * size), 1.6 * size);
        light(p, color, 4 * size, 0.4);
      },
      explosion(p: Vec3) {
        flash(p, "#ffc89a", 1.8, 0.8);
        ripple(p, EMBER, 1.5, 1.6);
        ripple(p, "#ffd8b0", 0.8, 1.1);
        spawnSparks(p, EMBER, 48, 2.4);
        light(p, EMBER, 14, 1.1);
        for (let k = 0; k < 10; k++) {
          const m = free(ash);
          Object.assign(m, { active: true, t: 0, life: 2.5 + Math.random() * 1.5 });
          m.v.set((Math.random() - 0.5) * 0.25, 0.25 + Math.random() * 0.35, (Math.random() - 0.5) * 0.25);
          ashRefs.current[ash.indexOf(m)]?.position.set(p[0] + (Math.random() - 0.5) * 0.5, p[1], p[2] + (Math.random() - 0.5) * 0.5);
        }
        addShake(0.12);
        fx.flash = Math.min(1, fx.flash + 0.12);
      },
      shockwave(p: Vec3, color: string, size = 1) {
        ripple(p, color, size, 1.6);
      },
    };
  }, [flashes, ripples, ash, lights, sparks]);

  useEffect(() => {
    fxApi.impact = api.impact;
    fxApi.explosion = api.explosion;
    fxApi.shockwave = api.shockwave;
  }, [api]);

  const fire = (shot: Shot) => {
    if (!unitObjects.get(shot.from)) return;
    const muzzle = aimPoint(shot.from, tmp.a);
    if (shot.weapon === "fireball") {
      const e = embers.find((x) => !x.active);
      if (!e) return;
      Object.assign(e, { active: true, t: 0, to: shot.to, duration: 0.9 + Math.random() * 0.35 });
      e.start.copy(muzzle);
      e.bend.set((Math.random() - 0.5) * 1.2, 0.9 + Math.random() * 0.9, (Math.random() - 0.5) * 1.2);
      for (let i = 0; i < TRAIL; i++) e.trail.set([muzzle.x, muzzle.y, muzzle.z], i * 3);
      api.flash(muzzle.toArray() as Vec3, EMBER, 0.45, 0.25);
      return;
    }
    const th = threads.find((x) => !x.active);
    if (!th || !threadGroup.current) return;
    const end = aimPoint(shot.to, tmp.b);
    const mid = tmp.c.copy(muzzle).add(end).multiplyScalar(0.5);
    mid.y += 0.7 + Math.random() * 0.6;
    mid.x += (Math.random() - 0.5) * 0.8;
    mid.z += (Math.random() - 0.5) * 0.8;
    th.curve = new THREE.QuadraticBezierCurve3(muzzle.clone(), mid.clone(), end.clone());
    if (th.mesh) {
      th.mesh.geometry.dispose();
      threadGroup.current.remove(th.mesh);
    }
    th.mesh = new THREE.Mesh(new THREE.TubeGeometry(th.curve, 64, 0.011, 6, false), th.material);
    th.mesh.frustumCulled = false;
    threadGroup.current.add(th.mesh);
    Object.assign(th, { active: true, t: 0, from: shot.from, to: shot.to, hit: false });
    api.flash(muzzle.toArray() as Vec3, PEARL, 0.5, 0.3);
  };

  // --- per frame ------------------------------------------------------------------------------------
  useFrame(({ clock }, dt) => {
    const now = clock.elapsedTime;

    if (round !== lastRound.current) {
      lastRound.current = round;
      schedule.current = [];
      if (round > 0) {
        const span = (TIMING.volleysTo - TIMING.volleysFrom) / 1000;
        for (const e of engagements) {
          for (let v = 0; v < e.volleys; v++) {
            const at = now + TIMING.volleysFrom / 1000 + (span * (v + Math.random() * 0.6)) / e.volleys;
            schedule.current.push({ at, from: e.from, to: e.to, weapon: e.weapon });
          }
        }
        api.ripple([0, 0, 0], "#dfe8ff", 3.8, 2.2);
        addShake(0.03);
      }
    }
    schedule.current = schedule.current.filter((shot) => {
      if (shot.at > now) return true;
      fire(shot);
      return false;
    });

    // Embers: arcing comets with a fading trail; they track their (moving) target.
    embers.forEach((e, i) => {
      const g = emberRefs.current[i];
      if (!g) return;
      g.visible = e.active;
      if (!e.active) return;
      e.t += dt / e.duration;
      const end = aimPoint(e.to, tmp.b);
      const ctrl = tmp.c.copy(e.start).add(end).multiplyScalar(0.5).add(e.bend);
      const t = Math.min(1, e.t);
      const eased = t * t * (3 - 2 * t) * 0.35 + t * 0.65;
      const pos = tmp.d.set(0, 0, 0).addScaledVector(e.start, (1 - eased) ** 2).addScaledVector(ctrl, 2 * (1 - eased) * eased)
        .addScaledVector(end, eased * eased);
      g.children[0].position.copy(pos);
      g.children[1].position.copy(pos);
      e.trail.copyWithin(3, 0, (TRAIL - 1) * 3);
      e.trail.set([pos.x, pos.y, pos.z], 0);
      (e.line.geometry.attributes.position as THREE.BufferAttribute).needsUpdate = true;
      if (e.t >= 1) {
        e.active = false;
        const target = agentsRef.current.find((a) => a.id === e.to);
        const shielded = target?.type === "defender" && target.status === "defending";
        if (shielded) hitShield(e.to, pos, now);
        api.impact(pos.toArray() as Vec3, shielded ? PEARL : EMBER, shielded ? 0.8 : 1);
        addShake(0.025);
      }
    });

    // Threads: draw on, flare at the target, then fade.
    threads.forEach((th, i) => {
      const head = threadHeads.current[i];
      if (!th.active || !th.curve) {
        if (head) head.visible = false;
        if (th.mesh) th.mesh.visible = false;
        return;
      }
      th.t += dt;
      const progress = Math.min(1, th.t / THREAD_DRAW);
      th.material.uniforms.uProgress.value = progress;
      th.material.uniforms.uFade.value = th.t < 0.45 ? 1 : Math.max(0, 1 - (th.t - 0.45) / (THREAD_LIFE - 0.45));
      if (th.mesh) th.mesh.visible = true;
      if (head) {
        head.visible = progress < 1;
        th.curve.getPoint(progress, head.position);
      }
      if (!th.hit && progress >= 1) {
        th.hit = true;
        api.impact(th.curve.v2.toArray() as Vec3, "#e6efff", 1);
        addShake(0.02);
      }
      if (th.t >= THREAD_LIFE) th.active = false;
    });

    flashes.forEach((f, i) => {
      const s = flashRefs.current[i];
      if (!s) return;
      s.visible = f.active;
      if (!f.active) return;
      f.t += dt;
      const k = f.t / f.life;
      s.scale.setScalar(f.size * (0.5 + k * 0.9));
      const m = s.material as THREE.SpriteMaterial;
      m.color.copy(f.color);
      m.opacity = Math.max(0, 1 - k) ** 2;
      if (k >= 1) f.active = false;
    });
    ripples.forEach((w, i) => {
      const mesh = rippleRefs.current[i];
      if (!mesh) return;
      mesh.visible = w.active;
      if (!w.active) return;
      w.t += dt;
      const k = w.t / w.life;
      const eased = 1 - Math.pow(1 - k, 3);
      mesh.scale.setScalar(w.size * (0.15 + eased * 2));
      const m = mesh.material as THREE.MeshBasicMaterial;
      m.color.copy(w.color);
      m.opacity = Math.max(0, 1 - k) ** 1.4 * 0.8;
      if (k >= 1) w.active = false;
    });
    ash.forEach((m, i) => {
      const s = ashRefs.current[i];
      if (!s) return;
      s.visible = m.active;
      if (!m.active) return;
      m.t += dt;
      const k = m.t / m.life;
      s.position.addScaledVector(m.v, dt);
      s.scale.setScalar(0.05 + k * 0.05);
      (s.material as THREE.SpriteMaterial).opacity = Math.sin(Math.min(1, k) * Math.PI) * 0.8;
      if (k >= 1) m.active = false;
    });
    lights.forEach((l, i) => {
      const light = lightRefs.current[i];
      if (!light) return;
      l.t += dt;
      light.intensity = l.peak * Math.max(0, 1 - l.t / l.life) ** 2;
    });

    const pts = sparkPoints.current;
    if (pts) {
      for (let i = 0; i < SPARKS; i++) {
        if (sparks.life[i] <= 0) continue;
        sparks.life[i] -= dt;
        const j = i * 3;
        sparks.vel[j + 1] -= 3.2 * dt;
        for (let a = 0; a < 3; a++) {
          sparks.vel[j + a] *= 1 - 2.2 * dt;
          sparks.pos[j + a] += sparks.vel[j + a] * dt;
        }
        if (sparks.pos[j + 1] < FLOOR_Y) {
          sparks.pos[j + 1] = FLOOR_Y;
          sparks.vel[j + 1] *= -0.25;
        }
        const k = Math.max(0, Math.min(1, sparks.life[i]));
        sparks.col[j] = sparks.base[j] * k;
        sparks.col[j + 1] = sparks.base[j + 1] * k;
        sparks.col[j + 2] = sparks.base[j + 2] * k;
      }
      (pts.geometry.attributes.position as THREE.BufferAttribute).needsUpdate = true;
      (pts.geometry.attributes.color as THREE.BufferAttribute).needsUpdate = true;
    }
  });

  useEffect(() => () => {
    for (const th of threads) th.mesh?.geometry.dispose();
  }, [threads]);

  return (
    <group>
      {embers.map((e, i) => (
        <group key={`e${i}`} ref={(el) => { emberRefs.current[i] = el; }} visible={false}>
          <mesh>
            <sphereGeometry args={[0.045, 16, 16]} />
            <meshBasicMaterial color={hdr("#ffd9b8", 7)} toneMapped={false} />
          </mesh>
          <sprite scale={0.42}>
            <spriteMaterial map={glow} color={hdr(EMBER, 2.4)} toneMapped={false} blending={THREE.AdditiveBlending} depthWrite={false} transparent />
          </sprite>
          <primitive object={e.line} />
        </group>
      ))}

      <group ref={threadGroup} />
      {threads.map((_, i) => (
        <sprite key={`h${i}`} ref={(el) => { threadHeads.current[i] = el; }} scale={0.35} visible={false}>
          <spriteMaterial map={glow} color={hdr("#eef4ff", 3)} toneMapped={false} blending={THREE.AdditiveBlending} depthWrite={false} transparent />
        </sprite>
      ))}

      {flashes.map((_, i) => (
        <sprite key={`f${i}`} ref={(el) => { flashRefs.current[i] = el; }} visible={false}>
          <spriteMaterial map={glow} toneMapped={false} blending={THREE.AdditiveBlending} depthWrite={false} transparent />
        </sprite>
      ))}

      {ripples.map((_, i) => (
        <mesh key={`r${i}`} ref={(el) => { rippleRefs.current[i] = el; }} rotation-x={-Math.PI / 2} visible={false}>
          <planeGeometry args={[1, 1]} />
          <meshBasicMaterial map={ring} toneMapped={false} transparent blending={THREE.AdditiveBlending} depthWrite={false} />
        </mesh>
      ))}

      {ash.map((_, i) => (
        <sprite key={`a${i}`} ref={(el) => { ashRefs.current[i] = el; }} visible={false}>
          <spriteMaterial map={glow} color="#b9b6c2" transparent depthWrite={false} />
        </sprite>
      ))}

      {lights.map((_, i) => (
        <pointLight key={`l${i}`} ref={(el) => { lightRefs.current[i] = el; }} intensity={0} distance={6} decay={2} />
      ))}

      <points ref={sparkPoints} frustumCulled={false}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[sparks.pos, 3]} />
          <bufferAttribute attach="attributes-color" args={[sparks.col, 3]} />
        </bufferGeometry>
        <pointsMaterial size={0.045} map={glow} vertexColors transparent depthWrite={false}
                        blending={THREE.AdditiveBlending} toneMapped={false} sizeAttenuation />
      </points>
    </group>
  );
}
