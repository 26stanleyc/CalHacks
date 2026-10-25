"use client";

import { useFrame } from "@react-three/fiber";
import { useMemo } from "react";
import * as THREE from "three";

import { type Agent, type Engagement, MAX_HP } from "./data";
import { unitObjects } from "./fx";

// Screen-space tracking: corner brackets and a compact label that follow each unit, plus lock-on
// lines between attacker and target while a round plays. The DOM is rendered by React once; a
// single per-frame pass inside the canvas moves it, damped so the overlay glides instead of
// jittering with camera shake.

interface Track {
  el: HTMLElement;
  range: HTMLElement | null;
  x: number;
  y: number;
  size: number;
  ready: boolean;
  frame: number;
}

const tracks = new Map<string, Track>();
const locks = new Map<string, { from: string; to: string; line: SVGLineElement; dot: SVGCircleElement | null }>();
const screen = new Map<string, { x: number; y: number; on: boolean }>();

/** Hovering a unit in 3D expands its tag (a class toggle, so no React render). */
export function setTrackHover(id: string, on: boolean) {
  tracks.get(id)?.el.classList.toggle("is-hover", on);
}

/** Runs inside <Canvas>: projects every tracked unit and updates the overlay. */
export function TrackingProjector() {
  const center = useMemo(() => new THREE.Vector3(), []);
  const top = useMemo(() => new THREE.Vector3(), []);

  useFrame(({ camera, size }, dt) => {
    for (const [id, t] of tracks) {
      const obj = unitObjects.get(id);
      if (!obj) continue;
      obj.getWorldPosition(center);
      const range = camera.position.distanceTo(center);
      center.y += 0.15;
      top.copy(center).setY(center.y + 0.7);
      center.project(camera);
      top.project(camera);
      const on = center.z < 1;
      const x = (center.x * 0.5 + 0.5) * size.width;
      const y = (-center.y * 0.5 + 0.5) * size.height;
      const span = Math.abs(y - (-top.y * 0.5 + 0.5) * size.height);
      const target = THREE.MathUtils.clamp(span * 2.2, 44, 180);
      if (!t.ready) {
        Object.assign(t, { x, y, size: target, ready: true });
      } else {
        // Adaptive damping: small jitter (camera shake) is smoothed out, but big moves (the intro
        // crane, orbiting) are followed almost immediately, so brackets never trail their units.
        const gap = Math.hypot(x - t.x, y - t.y);
        const k = 1 - Math.exp(-dt * (16 + gap * 1.2));
        t.x += (x - t.x) * k;
        t.y += (y - t.y) * k;
        t.size += (target - t.size) * k;
      }
      t.el.style.transform = `translate3d(${t.x.toFixed(1)}px, ${t.y.toFixed(1)}px, 0)`;
      t.el.style.setProperty("--s", `${t.size.toFixed(1)}px`);
      t.el.style.visibility = on ? "visible" : "hidden";
      if (t.range && t.frame++ % 6 === 0) t.range.textContent = `${range.toFixed(1)}m`;
      screen.set(id, { x: t.x, y: t.y, on });
    }
    for (const l of locks.values()) {
      const a = screen.get(l.from);
      const b = screen.get(l.to);
      if (!a || !b) continue;
      l.line.setAttribute("x1", a.x.toFixed(1));
      l.line.setAttribute("y1", a.y.toFixed(1));
      l.line.setAttribute("x2", b.x.toFixed(1));
      l.line.setAttribute("y2", b.y.toFixed(1));
      l.dot?.setAttribute("cx", b.x.toFixed(1));
      l.dot?.setAttribute("cy", b.y.toFixed(1));
    }
  });
  return null;
}

export interface Palette { defender: string; attacker: string; catch: string; attack: string }
export const ASTROLABE_PALETTE: Palette = { defender: "#a9c8ff", attacker: "#ff9a66", catch: "#cfdcff", attack: "#ff9a66" };

/** One bracket + label per unit (rendered outside the canvas). */
export function TrackingLayer({ agents, selectedId, engaged, palette = ASTROLABE_PALETTE, subtitle }: {
  agents: Agent[]; selectedId: string | null; engaged: Set<string>; palette?: Palette; subtitle?: (a: Agent) => string | undefined;
}) {
  return (
    <div className="ca-tracks" aria-hidden>
      {agents.map((agent, i) => {
        const down = agent.status === "down";
        const hp = Math.max(0, agent.hp) / MAX_HP;
        const accent = agent.type === "defender" ? palette.defender : palette.attacker;
        const sub = subtitle?.(agent);
        const state = [
          "ca-track",
          agent.id === selectedId ? "is-selected" : "",
          engaged.has(agent.id) ? "is-engaged" : "",
          down ? "is-down" : "",
          agent.type === "defender" ? "is-left" : "", // sentinel tags left, infernal tags right: clusters stay legible
        ].join(" ");
        return (
          <div key={agent.id} className={state} style={{ "--accent": accent } as React.CSSProperties}
               ref={(el) => {
                 if (el) {
                   const existing = tracks.get(agent.id);
                   tracks.set(agent.id, existing && existing.el === el ? existing : {
                     el, range: el.querySelector<HTMLElement>("[data-range]"), x: 0, y: 0, size: 60, ready: false, frame: 0,
                   });
                 } else {
                   tracks.delete(agent.id);
                   screen.delete(agent.id);
                 }
               }}>
            <div className="ca-bracket"><i /><i /><i /><i /></div>
            <div className="ca-tag">
              <div className="ca-tagName">{agent.name}{sub && <span className="ca-tagSub">{sub}</span>}</div>
              <div className="ca-tagBar"><span style={{ transform: `scaleX(${hp})` }} /></div>
              <div className="ca-tagMeta">
                <span>{String(i + 1).padStart(2, "0")}</span>
                <span>{down ? "down" : agent.status}</span>
                <span>{Math.max(0, agent.hp)}</span>
                <span data-range>—</span>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Animated lock-on lines for the round's engagements. */
export function LockLines({ engagements, palette = ASTROLABE_PALETTE }: { engagements: Engagement[]; palette?: Palette }) {
  const unique = engagements.filter((e, i) => engagements.findIndex((x) => x.from === e.from && x.to === e.to) === i);
  return (
    <svg className="ca-locks" aria-hidden>
      {unique.map((e) => {
        const key = `${e.from}>${e.to}`;
        const color = e.weapon === "beam" ? palette.catch : palette.attack;
        return (
          <g key={key} style={{ color }}>
            <line className="ca-lockLine"
                  ref={(line) => {
                    if (line) locks.set(key, { from: e.from, to: e.to, line, dot: line.nextElementSibling as SVGCircleElement });
                    else locks.delete(key);
                  }} />
            <circle className="ca-lockDot" r={4} />
          </g>
        );
      })}
    </svg>
  );
}
