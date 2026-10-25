// Payments in flight. Every drawn payment is a coin arcing from its thrower to the gate.
// A blocked coin is zapped by the specialists that objected; a stolen coin drops into the
// vault and flashes it red. Honest coins are gold, attack coins crimson.

import { useLayoutEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { C } from "../palette";
import { useArena } from "../store";
import { GATE, actorPos, type Vec3 } from "./layout";
import { INTERCEPT_FRAC, type CoinFlight, type RoundModel } from "./roundModel";

const ARC = 2.2;
const ZAP_POOL = 120;
const FLASH_POOL = 80;
const tmp = new THREE.Object3D();
const a = new THREE.Vector3();
const b = new THREE.Vector3();
const up = new THREE.Vector3(0, 1, 0);

function arcPoint(c: CoinFlight, u: number, out: THREE.Vector3) {
  out.set(
    c.from[0] + (GATE[0] - c.from[0]) * u,
    c.from[1] + 0.35 + (GATE[1] + 0.7 - c.from[1] - 0.35) * u + Math.sin(Math.PI * u) * ARC,
    c.from[2] + (GATE[2] - c.from[2]) * u,
  );
  return out;
}

const hide = (mesh: THREE.InstancedMesh, i: number) => {
  tmp.position.set(0, -50, 0);
  tmp.scale.setScalar(0);
  tmp.updateMatrix();
  mesh.setMatrixAt(i, tmp.matrix);
};

// `soloFrom` is where a single-model defense sits (step 0); it zaps every coin it stops.
export function Coins({ model, soloFrom }: { model: RoundModel; soloFrom?: Vec3 }) {
  const drawn = useMemo(() => model.coins.filter((c) => c.drawn), [model]);
  const coins = useRef<THREE.InstancedMesh>(null);
  const zaps = useRef<THREE.InstancedMesh>(null);
  const flashes = useRef<THREE.InstancedMesh>(null);
  const flashMat = useMemo(() => new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.85, depthWrite: false }), []);

  useLayoutEffect(() => {
    const mesh = coins.current;
    if (!mesh) return;
    const col = new THREE.Color();
    drawn.forEach((c, i) => mesh.setColorAt(i, col.set(c.attack ? C.cursed : C.coin)));
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [drawn]);

  useFrame(({ clock }) => {
    const t = useArena.getState().t;
    const cm = coins.current;
    const zm = zaps.current;
    const fm = flashes.current;
    if (!cm || !zm || !fm) return;
    let z = 0;
    let f = 0;
    const col = new THREE.Color();

    drawn.forEach((c, i) => {
      const stops = c.outcome === "blocked" || c.outcome === "false_alarm";
      const end = stops ? c.intercept + 0.3 : c.land + 0.15;
      if (t < c.launch || t > end) return hide(cm, i);
      const u = Math.min((t - c.launch) / (c.land - c.launch), stops ? INTERCEPT_FRAC : 1);
      arcPoint(c, u, a);
      let s = 1;
      if (stops && t > c.intercept) s = 1 - (t - c.intercept) / 0.3;
      if (!stops && t > c.land) s = 1 - (t - c.land) / 0.15;
      tmp.position.copy(a);
      tmp.rotation.set(Math.PI / 2, clock.elapsedTime * 6 + i, 0);
      tmp.scale.setScalar(Math.max(0.001, s));
      tmp.updateMatrix();
      cm.setMatrixAt(i, tmp.matrix);

      // Zap beams from each objecting angel, just before and after the intercept.
      if (stops && t > c.intercept - 0.12 && t < c.intercept + 0.22) {
        const w = 1 - Math.abs(t - c.intercept) / 0.22;
        const sources = soloFrom ? [soloFrom] : c.zappers.map(actorPos);
        for (const p of sources) {
          if (z >= ZAP_POOL) break;
          b.set(p[0], p[1] + 0.95, p[2]);
          const len = b.distanceTo(a);
          tmp.position.copy(b).add(a).multiplyScalar(0.5);
          tmp.quaternion.setFromUnitVectors(up, a.clone().sub(b).normalize());
          tmp.scale.set(w, len, w);
          tmp.updateMatrix();
          zm.setMatrixAt(z++, tmp.matrix);
        }
      }
    });
    for (let i = drawn.length; i < cm.count; i++) hide(cm, i);
    for (let i = z; i < ZAP_POOL; i++) hide(zm, i);

    // Bursts: blue when a fraud coin is stopped, grey for a false alarm, red when money is stolen.
    for (const c of drawn) {
      if (f >= FLASH_POOL) break;
      const stops = c.outcome === "blocked" || c.outcome === "false_alarm";
      const at = stops ? c.intercept : c.land;
      const k = (t - at) / 0.4;
      if (k < 0 || k > 1) continue;
      if (stops) arcPoint(c, INTERCEPT_FRAC, a);
      else a.set(GATE[0], GATE[1] + 0.5, GATE[2]);
      tmp.position.copy(a);
      tmp.rotation.set(0, 0, 0);
      const big = c.outcome === "stolen" ? 0.4 : c.outcome === "honest" ? 0.25 : 0.45;
      tmp.scale.setScalar(Math.sin(Math.PI * k) * big);
      tmp.updateMatrix();
      fm.setMatrixAt(f, tmp.matrix);
      fm.setColorAt(
        f,
        col.set(c.outcome === "blocked" ? "#9DB8FF" : c.outcome === "false_alarm" ? "#B9B4C2" : c.outcome === "stolen" ? C.imp : C.coin),
      );
      f++;
    }
    for (let i = f; i < FLASH_POOL; i++) hide(fm, i);

    cm.instanceMatrix.needsUpdate = true;
    zm.instanceMatrix.needsUpdate = true;
    fm.instanceMatrix.needsUpdate = true;
    if (fm.instanceColor) fm.instanceColor.needsUpdate = true;
  });

  return (
    <group>
      <instancedMesh ref={coins} args={[undefined, undefined, Math.max(1, drawn.length)]} key={drawn.length} castShadow frustumCulled={false}>
        <cylinderGeometry args={[0.2, 0.2, 0.06, 20]} />
        <meshStandardMaterial roughness={0.3} metalness={0.3} emissive="#3a2a00" />
      </instancedMesh>
      <instancedMesh ref={zaps} args={[undefined, undefined, ZAP_POOL]} frustumCulled={false}>
        <cylinderGeometry args={[0.06, 0.06, 1, 6]} />
        <meshBasicMaterial color="#4F7BFF" />
      </instancedMesh>
      <instancedMesh ref={flashes} args={[undefined, flashMat, FLASH_POOL]} frustumCulled={false}>
        <sphereGeometry args={[1, 16, 16]} />
      </instancedMesh>
    </group>
  );
}
