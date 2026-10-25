"use client";

import { Canvas } from "@react-three/fiber";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import "./arena.css";
import BattleFX from "./BattleFX";
import Battlefield from "./Battlefield";
import { type Agent, INITIAL_AGENTS, MAX_HP, ROUND_SCRIPTS, TIMING, type Vec3 } from "./data";
import { fx } from "./fx";
import ImmuneWorld, { BIO, IMMUNE_COLORS, vesselPoint } from "./immune/ImmuneWorld";
import { CameraRig, cameraFor, PostFX, type SceneId, ShakeRig, SHOTS } from "./Stage";
import { ASTROLABE_PALETTE, LockLines, type Palette, TrackingLayer, TrackingProjector } from "./Tracking";
import { Unit } from "./Units";

interface LogEvent {
  id: number;
  at: string;
  text: string;
  color: string;
}

interface Card {
  key: number;
  kicker: string;
  title: string;
  stay?: boolean;
  hint?: string;
}

interface Theme {
  cool: string; // defense accent
  hot: string; // attacker accent
  palette: Palette;
  brandSub: string;
  intro: { kicker: string; title: string };
  victory: string;
  teams: [string, string];
  spawnLine: (parent: string, spawned: string) => string;
  catchLine: (from: string, to: string, move: string) => string;
  contained: (name: string) => string;
  legend?: [string, string][];
}

const THEMES: Record<SceneId, Theme> = {
  immune: {
    cool: IMMUNE_COLORS.defense, hot: IMMUNE_COLORS.attack,
    palette: { defender: IMMUNE_COLORS.defense, attacker: IMMUNE_COLORS.attack, catch: "#efe6ff", attack: IMMUNE_COLORS.attack },
    brandSub: "x402 facilitator · seen as an immune system",
    intro: { kicker: "Self-red-teaming defense · as an immune system", title: "Immune Response" },
    victory: "Contained. Immune memory formed.",
    teams: ["Immune system", "Pathogens"],
    spawnLine: (p, s) => `${p} presents ${s}: a vaccine built against its own rules`,
    catchLine: (f, t, m) => `${f} tags ${t} · ${m}`,
    contained: (n) => `${n} engulfed`,
    legend: [
      ["Red cells", "Legitimate payments, flowing past"],
      ["Self-marker", "The mandate each payment carries (the pearl glint on every red cell)"],
      ["Pathogens", "The attacker personas. The Hijacker is a red cell taken over from inside, with the wrong marker"],
      ["White cells", "The five specialists; the large helper T cell is the orchestrator"],
      ["Antibodies", "Rules, released to tag a threat for removal"],
      ["Vaccine", "Self-red-teaming: Graph builds a weakened attacker against its own rules, so the system learns safely"],
      ["Memory cells", "The archive of every attack it has beaten, lining the vessel wall"],
      ["Autoimmunity", "False positives: attacking healthy cells is what the defense must never do"],
    ],
  },
  astrolabe: {
    cool: "#a9c8ff", hot: "#ff9a66", palette: ASTROLABE_PALETTE,
    brandSub: "Sector 7 · x402 facilitator",
    intro: { kicker: "Self-red-teaming defense · x402 facilitator", title: "Cyber Arena" },
    victory: "Contained. The facilitator holds.",
    teams: ["Defense", "Attackers"],
    spawnLine: (p, s) => `${p} spawns ${s} against its own rules`,
    catchLine: (f, t, m) => `${f} catches ${t} · ${m}`,
    contained: (n) => `${n} contained`,
  },
};

function clock(start: number) {
  const s = Math.floor((performance.now() - start) / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** Running timecode (mm:ss:frames at 24 fps). */
function Timecode() {
  const el = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const start = performance.now();
    let raf = 0;
    const tick = () => {
      const f = Math.floor(((performance.now() - start) / 1000) * 24) % 24;
      if (el.current) el.current.textContent = `${clock(start)}:${String(f).padStart(2, "0")}`;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
  return <span ref={el} className="ca-mono">00:00:00</span>;
}

/** Screen flash for explosions, driven by the shared fx bus. */
function FlashOverlay() {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      if (el.current) el.current.style.opacity = String(Math.min(0.6, fx.flash * 0.45 + fx.lightning * 0.05));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
  return <div ref={el} className="ca-flash" />;
}

function Team({ label, agents, color, right }: { label: string; agents: Agent[]; color: string; right?: boolean }) {
  const hp = agents.reduce((sum, a) => sum + Math.max(0, a.hp), 0) / (agents.length * MAX_HP);
  const alive = agents.filter((a) => a.status !== "down").length;
  return (
    <div className={`ca-team ${right ? "is-right" : ""}`}>
      <div className="ca-teamRow">
        <span style={{ color }}>{label}</span>
        <span className="ca-mono">{alive}/{agents.length} · {Math.round(hp * 100)}</span>
      </div>
      <div className="ca-track2"><span style={{ background: color, transform: `scaleX(${hp})` }} /></div>
    </div>
  );
}

function Inspector({ agent, accent, bio, onClose }: { agent: Agent; accent: string; bio?: { kind: string; note: string }; onClose: () => void }) {
  const terminal = useRef<HTMLDivElement>(null);
  useEffect(() => {
    terminal.current?.scrollTo({ top: terminal.current.scrollHeight, behavior: "smooth" });
  }, [agent.log.length]);
  return (
    <aside className="ca-inspector" data-testid="arena-inspector" style={{ "--accent": accent } as React.CSSProperties}>
      <div className="ca-inspHead">
        <div>
          <span className="ca-chip">{agent.type}{bio ? ` · ${bio.kind}` : ""}</span>
          <h2 className="ca-inspName">{agent.name}</h2>
        </div>
        <button className="ca-close" onClick={onClose} aria-label="Close inspector">×</button>
      </div>
      <div className="ca-inspBody">
        <div>
          <div className="ca-label">Classification Blueprint</div>
          <div>{agent.role}</div>
        </div>
        {bio && (
          <div>
            <div className="ca-label">In the body</div>
            <div className="ca-strategy"><span style={{ color: "var(--ink)", textTransform: "capitalize" }}>{bio.kind}.</span> {bio.note}</div>
          </div>
        )}
        <div className="ca-stats">
          <div><div className="ca-label">Integrity</div><div className="ca-statValue" style={{ color: accent }}>{Math.max(0, agent.hp)}</div></div>
          <div><div className="ca-label">State</div><div className="ca-statValue">{agent.status}</div></div>
          <div><div className="ca-label">Grid</div><div className="ca-statValue">{agent.position[0]},{agent.position[2]}</div></div>
        </div>
        <div>
          <div className="ca-label">Active Running Strategy</div>
          <div className="ca-strategy">{agent.strategy}</div>
        </div>
        <div>
          <div className="ca-label">Runtime Execution Terminal Logs</div>
          <div className="ca-terminal" ref={terminal}>
            {agent.log.map((entry, idx) => <div key={idx}>{entry}</div>)}
          </div>
        </div>
      </div>
    </aside>
  );
}

function Legend({ rows, open, onToggle }: { rows: [string, string][]; open: boolean; onToggle: () => void }) {
  return (
    <aside className={`ca-legend ${open ? "is-open" : ""}`}>
      <button className="ca-legendToggle" onClick={onToggle} aria-expanded={open}>
        <span>How to read this</span><span className="ca-kbd">K</span>
      </button>
      {open && (
        <dl>
          {rows.map(([term, def]) => <div key={term}><dt>{term}</dt><dd>{def}</dd></div>)}
        </dl>
      )}
    </aside>
  );
}

export default function CyberArena({ scene = "immune" }: { scene?: SceneId }) {
  const theme = THEMES[scene];
  const shot = SHOTS[scene];
  const COOL = theme.cool;
  const HOT = theme.hot;
  const introCard: Card = { key: 0, kicker: theme.intro.kicker, title: theme.intro.title, stay: true, hint: "Press space to engage" };

  const [agents, setAgents] = useState<Agent[]>(INITIAL_AGENTS);
  const [currentRound, setCurrentRound] = useState(0);
  const [battling, setBattling] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [card, setCard] = useState<Card>(introCard);
  const [log, setLog] = useState<LogEvent[]>([]);
  const [resetKey, setResetKey] = useState(0);
  const [legendOpen, setLegendOpen] = useState(true);
  // ?fx=0 turns post-processing off (slow GPUs, debugging). Only affects the canvas, so no hydration mismatch.
  const [postFx] = useState(() => typeof window === "undefined" || new URLSearchParams(window.location.search).get("fx") !== "0");
  const timers = useRef<number[]>([]);
  const startedAt = useRef(0);
  const nextEvent = useRef(0);

  useEffect(() => {
    startedAt.current = performance.now();
    const pending = timers.current;
    return () => pending.forEach(clearTimeout);
  }, []);

  const later = (ms: number, fn: () => void) => timers.current.push(window.setTimeout(fn, ms));
  const announce = useCallback((text: string, color: string) => {
    setLog((prev) => [...prev.slice(-4), { id: nextEvent.current++, at: clock(startedAt.current), text, color }]);
  }, []);

  const advanceRound = () => {
    if (battling || currentRound >= ROUND_SCRIPTS.length) return;
    const script = ROUND_SCRIPTS[currentRound];
    const r = currentRound + 1;
    const cast = (id: string) => INITIAL_AGENTS.find((a) => a.id === id);
    const name = (id: string) => cast(id)?.name ?? id;
    const [, phase] = script.title.split(" · ");
    setCurrentRound(r);
    setBattling(true);
    setLegendOpen(false);
    setCard({ key: r, kicker: `Round ${String(r).padStart(2, "0")} / ${String(ROUND_SCRIPTS.length).padStart(2, "0")}${phase ? ` · ${phase}` : ""}`,
              title: script.description });
    announce(script.title, "#e2e8f0");
    for (const spawned of INITIAL_AGENTS.filter((a) => a.appearsAt === r && a.spawnFrom)) {
      announce(theme.spawnLine(name(spawned.spawnFrom!), spawned.name), HOT);
    }

    // Moves and stance changes land now; eliminations wait for the killing blows.
    setAgents((prev) => prev.map((agent) => {
      const update = script.updates[agent.id];
      if (!update || update.status === "down") return agent;
      return { ...agent, ...update, log: [...agent.log, `[r${r}] Stance: ${update.status || agent.status}.`] };
    }));
    // Each engagement reads as a line in the feed, spread over the firing window.
    script.engagements.forEach((e, i) => {
      const from = cast(e.from);
      const line = from?.type === "defender" ? theme.catchLine(name(e.from), name(e.to), from.move) : `${name(e.from)} → ${name(e.to)} · ${from?.move}`;
      later(TIMING.volleysFrom + i * 320, () => announce(line, from?.type === "defender" ? COOL : HOT));
    });
    later(TIMING.damageAt, () => {
      setAgents((prev) => prev.map((agent) => {
        const dmg = script.damage[agent.id];
        if (!dmg) return agent;
        const hp = Math.max(0, agent.hp - dmg);
        const note = agent.type === "defender" ? `Integrity −${dmg} (${hp}/${MAX_HP}).` : `Caught: −${dmg} (${hp}/${MAX_HP}).`;
        return { ...agent, hp, log: [...agent.log, `[r${r}] ${note}`] };
      }));
    });
    // Eliminations land one after another, not all at once.
    Object.entries(script.updates).filter(([, u]) => u.status === "down").forEach(([id, update], k) => {
      later(TIMING.downAt + k * TIMING.downStagger, () => {
        setAgents((prev) => prev.map((agent) => agent.id !== id ? agent
          : { ...agent, ...update, hp: 0, log: [...agent.log, `[r${r}] Contained. Shelf marked defended.`] }));
        announce(theme.contained(name(id)), HOT);
      });
    });
    later(TIMING.roundLength, () => {
      setBattling(false);
      if (r === ROUND_SCRIPTS.length) {
        setCard({ key: 99, kicker: "Engagement complete", title: theme.victory, stay: true, hint: "Press R to replay" });
        announce("All attackers contained", "#34d399");
      }
    });
  };

  const resetSim = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    setAgents(INITIAL_AGENTS);
    setCurrentRound(0);
    setBattling(false);
    setSelectedId(null);
    setLog([]);
    setCard({ ...introCard, key: Date.now() });
    setResetKey((k) => k + 1);
  };

  // Keyboard: space/enter engages, R resets, K toggles the legend, Esc closes the inspector. The ref
  // keeps the listener pointed at this render's handlers without re-subscribing.
  const keys = useRef({ advanceRound, resetSim });
  useEffect(() => {
    keys.current = { advanceRound, resetSim };
  });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLElement && e.target.closest("input, textarea")) return;
      if (e.code === "Space" || e.code === "Enter") {
        e.preventDefault();
        keys.current.advanceRound();
      } else if (e.code === "KeyR" && !e.metaKey && !e.ctrlKey) keys.current.resetSim();
      else if (e.code === "KeyK" && !e.metaKey && !e.ctrlKey) setLegendOpen((o) => !o);
      else if (e.code === "Escape") setSelectedId(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const selected = agents.find((a) => a.id === selectedId) ?? null;
  const script = ROUND_SCRIPTS[currentRound - 1];
  // Spawned attackers only exist from the round they're built in.
  const present = agents.filter((a) => (a.appearsAt ?? 0) <= currentRound);
  const defenders = present.filter((a) => a.type === "defender");
  const attackers = present.filter((a) => a.type === "attacker");
  const done = currentRound >= ROUND_SCRIPTS.length;

  // Where the camera looks: midway between the lines, or the middle of the current fight.
  const focus = useMemo<Vec3>(() => {
    const at = (a: Agent): Vec3 => scene === "immune" ? vesselPoint(a) : [a.position[0], 0.4, a.position[2]];
    const rest: Vec3 = scene === "immune" ? [-0.4, 0, -1] : [0.8, 0.4, 0];
    if (!battling || !script) return rest;
    const ids = new Set(script.engagements.flatMap((e) => [e.from, e.to]));
    const pts = agents.filter((a) => ids.has(a.id)).map(at);
    const avg = pts.reduce((s, p) => [s[0] + p[0], s[1] + p[1], s[2] + p[2]], [0, 0, 0]).map((v) => v / Math.max(1, pts.length));
    return scene === "immune" ? [avg[0] * 0.6 + rest[0] * 0.4, avg[1] * 0.4, -1] : [avg[0], 0.4, avg[2]];
  }, [battling, script, agents, scene]);

  const engaged = useMemo(
    () => new Set(battling && script ? script.engagements.flatMap((e) => [e.from, e.to]) : []),
    [battling, script],
  );
  const showCard = !(card.stay && (battling || (currentRound > 0 && card.key === 0)));
  const select = (a: Agent) => setSelectedId(a.id);
  const subtitle = scene === "immune" ? (a: Agent) => BIO[a.id]?.kind : undefined;

  return (
    <div className={`ca-root ca-${scene}`} data-testid="cyber-arena">
      <div className="ca-canvas">
        <Canvas shadows dpr={[1, 2]} flat camera={cameraFor(scene)} gl={{ antialias: false, powerPreference: "high-performance" }}
                onPointerMissed={() => setSelectedId(null)}>
          <Suspense fallback={null}>
            <CameraRig shot={shot} focus={focus} battling={battling} />
            <TrackingProjector />
            {scene === "immune" ? (
              <group key={resetKey}>
                <ImmuneWorld agents={present} battling={battling} script={script} selectedId={selectedId} onSelect={select} />
              </group>
            ) : (
              <ShakeRig>
                <Battlefield />
                <group key={resetKey}>
                  {present.map((agent) => (
                    <Unit key={agent.id} agent={agent} selected={agent.id === selectedId} onSelect={select} />
                  ))}
                  <BattleFX round={currentRound} engagements={script?.engagements ?? []} agents={agents} />
                </group>
              </ShakeRig>
            )}
            {postFx && <PostFX shot={shot} />}
          </Suspense>
        </Canvas>
      </div>

      {battling && script && <LockLines key={`locks-${currentRound}`} engagements={script.engagements} palette={theme.palette} />}
      <TrackingLayer agents={present} selectedId={selectedId} engaged={engaged} palette={theme.palette} subtitle={subtitle} />
      <FlashOverlay />

      <div className="ca-frame" aria-hidden><i /><i /><i /><i /></div>

      <header className="ca-top">
        <div><span className="ca-brand">Cyber Arena</span><span className="ca-sub">{theme.brandSub}</span></div>
        <span className="ca-rec">Live <Timecode /></span>
      </header>

      <div className="ca-score">
        <Team label={theme.teams[0]} agents={defenders} color={COOL} />
        <div className="ca-round ca-mono">R <b>{currentRound}</b> / {ROUND_SCRIPTS.length}</div>
        <Team label={theme.teams[1]} agents={attackers} color={HOT} right />
      </div>

      {theme.legend && <Legend rows={theme.legend} open={legendOpen} onToggle={() => setLegendOpen((o) => !o)} />}

      {showCard && (
        <div key={`card-${card.key}`} className={`ca-title ${card.stay ? "is-stay" : ""}`}>
          <div className="ca-titleKicker ca-mono">{card.kicker}</div>
          <div className="ca-titleMain">{card.title}</div>
          <div className="ca-titleRule" />
          {card.hint && <div className="ca-titleHint">{card.hint}</div>}
        </div>
      )}

      <section className="ca-console">
        <div className="ca-kicker">Agent execution visualizer</div>
        <h1 className="ca-consoleTitle">{currentRound === 0 ? "Standing by" : script?.title}</h1>
        <div className="ca-consoleText" data-testid="arena-status">{currentRound === 0 ? "Ready to Deploy" : script?.description}</div>
        <div className="ca-steps">
          {ROUND_SCRIPTS.map((_, i) => (
            <span key={`${i}-${resetKey}`} className={i < currentRound - (battling ? 1 : 0) ? "is-done" : battling && i === currentRound - 1 ? "is-live" : ""} />
          ))}
        </div>
        <div className="ca-actions">
          <button className="ca-btn ca-primary" onClick={advanceRound} disabled={battling || done}>
            {done ? "Contained" : battling ? "Engaging…" : `Engage round ${currentRound + 1}`}
            {!battling && !done && <span className="ca-kbd">␣</span>}
          </button>
          <button className="ca-btn ca-ghost" onClick={resetSim}>Reset<span className="ca-kbd">R</span></button>
        </div>
      </section>

      <div className="ca-log ca-mono" aria-live="polite">
        {log.length === 0 && <div className="ca-logLine"><time>--:--</time><i style={{ color: "var(--faint)" }} /><span>Awaiting orders</span></div>}
        {log.map((e) => (
          <div key={e.id} className="ca-logLine">
            <time>{e.at}</time>
            <i style={{ color: e.color }} />
            <span style={{ color: e.color === "#e2e8f0" ? "var(--ink)" : undefined }}>{e.text}</span>
          </div>
        ))}
      </div>

      {!selected && <div className="ca-hint">Drag to orbit · scroll to zoom · click a cell</div>}

      {selected && <Inspector agent={selected} accent={selected.type === "defender" ? COOL : HOT}
                              bio={scene === "immune" ? BIO[selected.id] : undefined} onClose={() => setSelectedId(null)} />}
    </div>
  );
}
