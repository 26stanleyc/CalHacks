// The round scrubber doubles as the arms-race chart: each round's bar is how much a fresh
// attacker could still steal from the frozen defense (exploitability). Lower is better.

import { useArena, ROUND_LEN } from "../store";
import { money } from "./words";

const SPEEDS = [1, 2, 4, 8];

export function Timeline() {
  const replay = useArena((s) => s.replay)!;
  const round = useArena((s) => s.round);
  const progress = useArena((s) => Math.floor((s.t / ROUND_LEN) * 100));
  const playing = useArena((s) => s.playing);
  const speed = useArena((s) => s.speed);
  const { jump, toggle, setSpeed } = useArena.getState();
  const max = Math.max(...replay.rounds.map((r) => r.metrics.exploitability_cents));
  // Consecutive frozen rounds get one label underneath, so the spike they cause explains itself.
  const frozenRuns: [number, number][] = [];
  replay.rounds.forEach((r, i) => {
    if (!r.metrics.frozen) return;
    const last = frozenRuns[frozenRuns.length - 1];
    if (last && last[1] === i - 1) last[1] = i;
    else frozenRuns.push([i, i]);
  });

  return (
    <footer className="timeline">
      <button className="play" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
        {playing ? (
          <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="4" y="3" width="4" height="14" rx="1.5" /><rect x="12" y="3" width="4" height="14" rx="1.5" /></svg>
        ) : (
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 3.5v13a1 1 0 0 0 1.5.86l11-6.5a1 1 0 0 0 0-1.72l-11-6.5A1 1 0 0 0 5 3.5z" /></svg>
        )}
      </button>
      <div className="track">
        <div className="bars" role="group" aria-label="Rounds">
          {replay.rounds.map((r, i) => {
            const h = 18 + (r.metrics.exploitability_cents / max) * 82;
            const level = r.metrics.exploitability_cents / max;
            return (
              <button
                key={r.round}
                className={`bar${i === round ? " current" : ""}${i < round ? " past" : ""}${r.metrics.frozen ? " frozen" : ""}`}
                onClick={() => jump(i)}
                aria-label={`Round ${r.round}: a fresh attacker could still steal ${money(r.metrics.exploitability_cents)}`}
                title={`Round ${r.round}: ${money(r.metrics.exploitability_cents)} still stealable`}
              >
                <span
                  className="fill"
                  style={{
                    height: `${h}%`,
                    background: `color-mix(in oklab, var(--shaky) ${Math.round(level * 100)}%, var(--held))`,
                  }}
                />
                {i === round && <span className="progress" style={{ width: `${progress}%` }} />}
              </button>
            );
          })}
        </div>
        {frozenRuns.length > 0 && (
          <div className="marks" style={{ gridTemplateColumns: `repeat(${replay.rounds.length}, 1fr)` }}>
            {frozenRuns.map(([a, b]) => (
              <span key={a} className="frozen-mark" style={{ gridColumn: `${a + 1} / ${b + 2}` }}>
                Learning frozen
              </span>
            ))}
          </div>
        )}
        <p className="caption">Bar height: how much a fresh attacker could still steal after each round</p>
      </div>
      <div className="speeds" role="group" aria-label="Playback speed">
        {SPEEDS.map((s) => (
          <button key={s} className={s === speed ? "on" : ""} onClick={() => setSpeed(s)} aria-pressed={s === speed}>
            {s}×
          </button>
        ))}
      </div>
    </footer>
  );
}
