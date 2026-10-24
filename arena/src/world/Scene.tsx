import { useMemo } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Html, OrbitControls } from "@react-three/drei";
import type { Lane } from "../data/types";
import { C } from "../palette";
import { useArena } from "../store";
import { Board } from "./Board";
import { Coins } from "./Coins";
import { Angel, Devil, Shopper } from "./creatures";
import { ACTORS, SHOPPER_SPOTS, actorPos } from "./layout";
import { getRoundModel } from "./roundModel";
import { Spawns } from "./Spawns";

function Clock() {
  const tick = useArena((s) => s.tick);
  useFrame((_, dt) => tick(Math.min(dt, 0.1)));
  return null;
}

function HoverLabel() {
  const hover = useArena((s) => s.hover);
  if (!hover) return null;
  return (
    <Html position={[hover.pos[0], hover.pos[1] + 0.9, hover.pos[2]]} center zIndexRange={[30, 0]} style={{ pointerEvents: "none" }}>
      <div className="hover-label">{hover.label}</div>
    </Html>
  );
}

function Cast() {
  const replay = useArena((s) => s.replay)!;
  const roundIndex = useArena((s) => s.round);
  const model = getRoundModel(replay, roundIndex);
  const round = replay.rounds[roundIndex];

  // Event times each creature reacts to: throws for devils and shoppers, zaps for angels.
  const { throws, zaps, shopperThrows } = useMemo(() => {
    const throws = new Map<string, number[]>();
    const zaps = new Map<Lane, number[]>();
    const shopperThrows = SHOPPER_SPOTS.map(() => [] as number[]);
    for (const c of model.coins) {
      if (!c.drawn) continue;
      if (c.attack) {
        const id = c.payment.attacker_id!;
        throws.set(id, [...(throws.get(id) ?? []), c.launch]);
      } else shopperThrows[SHOPPER_SPOTS.indexOf(c.from)]?.push(c.launch);
      if (c.outcome === "blocked" || c.outcome === "false_alarm")
        for (const l of c.zappers) zaps.set(l, [...(zaps.get(l) ?? []), c.intercept]);
    }
    return { throws, zaps, shopperThrows };
  }, [model]);

  return (
    <>
      {ACTORS.map((a) => {
        const p = actorPos(a);
        const learns = !round.metrics.frozen && (a === "orchestrator" || (round.rulebooks.find((r) => r.lane === a)?.added ?? 0) > 0);
        return (
          <group key={a} position={p} rotation-y={Math.atan2(p[0], p[2])}>
            <Angel actor={a} pos={[0, 0, 0]} zaps={a === "orchestrator" ? [] : zaps.get(a) ?? []} learns={learns} />
          </group>
        );
      })}
      {model.devils.map((d) => (
        <Devil
          key={`${roundIndex}-${d.attacker.attacker_id}`}
          id={d.attacker.attacker_id}
          name={d.attacker.name}
          source={d.attacker.source}
          pos={d.pos}
          appear={d.appear}
          leave={d.leave}
          throws={throws.get(d.attacker.attacker_id) ?? []}
          retunes={d.retunes}
          idle={d.idle}
          facing={Math.atan2(-d.pos[0], -d.pos[2])}
        />
      ))}
      {SHOPPER_SPOTS.map((p, i) => (
        <Shopper key={i} pos={p} throws={shopperThrows[i]} />
      ))}
      <Coins model={model} />
      <Spawns key={roundIndex} beams={model.beams} news={model.news} />
    </>
  );
}

export function Scene() {
  const select = useArena((s) => s.select);
  return (
    <Canvas
      shadows="percentage"
      dpr={[1, 2]}
      camera={{ position: [9.5, 15, 14.5], fov: 38, near: 0.5, far: 200 }}
      onPointerMissed={() => select(null)}
    >
      <color attach="background" args={[C.sky]} />
      <fog attach="fog" args={[C.sky, 30, 60]} />
      <hemisphereLight args={["#E8EEFF", "#7FA36A", 1.1]} />
      <directionalLight
        position={[8, 14, 6]}
        intensity={1.6}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-14}
        shadow-camera-right={14}
        shadow-camera-top={14}
        shadow-camera-bottom={-14}
        shadow-bias={-0.0005}
      />
      <Clock />
      <Board />
      <Cast />
      <HoverLabel />
      <OrbitControls
        makeDefault
        target={[0, 0, 1.6]}
        enableDamping
        minDistance={7}
        maxDistance={34}
        minPolarAngle={0.35}
        maxPolarAngle={1.15}
        screenSpacePanning={false}
      />
    </Canvas>
  );
}
