// The round scrubber doubles as a chart. With a baseline (step 0) loaded, the bar runs straight
// through both stages: the baseline's rounds first, then TagTeam's, each bar that round's net
// (honest payments through minus money stolen, drawn from a zero line; higher is better). TagTeam's
// bars carry a red outline with the baseline's net on the same payments. On a real dataset the bars are the
// share of each round's fraud stopped instead, on a 0-100% scale. Without a baseline, each
// bar is how much a fresh attacker could still steal from the frozen defense (exploitability;
// lower is better).

import { flowByRound } from "../data/baseline";
import type { Replay } from "../data/types";
import { useArena, ROUND_LEN, type Stage } from "../store";
import { money } from "./words";

const SPEEDS = [1, 2, 4, 8];

// Share of each round's fraud payments that didn't get through (0 when a round has none).
const stoppedShare = (replay: Replay) =>
  replay.rounds.map((r) => {
    const fraud = r.waves.flatMap((w) => w.payments).filter((p) => p.is_fraud);
    return fraud.length ? fraud.filter((p) => !p.settled).length / fraud.length : 0;
  });

interface Segment {
  stage: Stage;
  index: number; // round index within its stage
  round: number;
  value: number;
  ghost: number | null;
  frozen: boolean;
}

export function Timeline() {
  const replay = useArena((s) => s.replay)!;
  const round = useArena((s) => s.round);
  const progress = useArena((s) => Math.floor((s.t / ROUND_LEN) * 100));
  const playing = useArena((s) => s.playing);
  const speed = useArena((s) => s.speed);
  const runs = useArena((s) => s.runs);
  const stage = useArena((s) => s.stage);
  const { jump, toggle, setSpeed, setStage } = useArena.getState();
  const compare = !!runs?.before;
  const label = runs?.baseline?.label ?? "";

  // On a real dataset honest money dwarfs fraud, so net bars look the same for both defenses; there each
  // bar is the share of that round's fraud payments stopped instead (higher is better).
  const caught = !!runs?.after.dataset;
  const perRound = (r: Replay) => (caught ? stoppedShare(r) : flowByRound(r).map((f) => f.net));
  let segments: Segment[];
  if (compare) {
    const before = perRound(runs!.before!);
    const after = perRound(runs!.after);
    segments = [
      ...runs!.before!.rounds.map((r, i) => ({ stage: "before" as Stage, index: i, round: r.round, value: before[i], ghost: null, frozen: false })),
      ...runs!.after.rounds.map((r, i) => ({ stage: "after" as Stage, index: i, round: r.round, value: after[i], ghost: before[i], frozen: false })),
    ];
  } else {
    segments = replay.rounds.map((r, i) => ({
      stage, index: i, round: r.round, value: r.metrics.exploitability_cents, ghost: null, frozen: r.metrics.frozen,
    }));
  }
  const current = segments.findIndex((s) => s.stage === stage && s.index === round);
  // Attack money ramps up over the run, so a steady net under heavier attack means the defense is
  // keeping up. Say where it peaks rather than assuming it rises every round.
  const attempted = compare ? flowByRound(runs!.after).map((f) => f.attempted) : [];
  // A dataset's fraud doesn't adapt or grow on purpose, so the note is only for simulated runs.
  const peak = attempted.reduce((best, v, i) => (v > attempted[best] ? i : best), 0);
  const growing = !replay.dataset && attempted.length > 1 && attempted[peak] > attempted[0] * 1.2;
  const beforeCount = compare ? runs!.before!.rounds.length : 0;

  // Net can go negative, so in comparison mode bars hang from a zero line spanning every value.
  const values = segments.flatMap((s) => (s.ghost === null ? [s.value] : [s.value, s.ghost]));
  const lo = compare ? Math.min(0, ...values) : 0;
  const span = Math.max(...values, 1) - lo;
  const zero = ((0 - lo) / span) * 100;
  const netBox = (v: number) => {
    const h = Math.max((Math.abs(v) / span) * 100, 3);
    return { bottom: `${v >= 0 ? zero : zero - h}%`, height: `${h}%` };
  };
  const go = (s: Segment) => {
    if (s.stage !== stage) setStage(s.stage, s.stage === "after");
    jump(s.index);
  };

  // Consecutive frozen rounds get one label underneath, so the spike they cause explains itself.
  const frozenRuns: [number, number][] = [];
  segments.forEach((s, i) => {
    if (!s.frozen) return;
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
        <div className={`bars${compare ? " net" : ""}`} role="group" aria-label="Rounds">
          {segments.map((s, i) => {
            const who = compare ? (s.stage === "before" ? `${label}, round ${s.round}` : `TagTeam, round ${s.round}`) : `Round ${s.round}`;
            const what = caught
              ? `${Math.round(s.value * 100)}% of phishing stopped`
              : compare ? `net ${money(s.value)}` : `${money(s.value)} still stealable`;
            const extra =
              s.ghost === null ? "" : `; ${label} on the same payments: ${caught ? `${Math.round(s.ghost * 100)}% stopped` : `net ${money(s.ghost)}`}`;
            return (
              <button
                key={`${s.stage}-${s.index}`}
                className={`bar${i === current ? " current" : ""}${i < current ? " past" : ""}${s.frozen ? " frozen" : ""}`}
                onClick={() => go(s)}
                aria-label={`${who}: ${what}${extra}`}
                title={`${who}: ${what}${extra.replace("; ", "\n")}`}
              >
                {compare && <span className="zero" style={{ bottom: `${zero}%` }} />}
                {s.ghost !== null && <span className={`ghost${s.ghost < 0 ? " loss" : ""}`} style={netBox(s.ghost)} />}
                <span
                  className="fill"
                  style={
                    compare
                      ? { ...netBox(s.value), background: s.value < 0 ? "var(--shaky)" : s.stage === "before" ? "var(--model)" : "var(--seraph)" }
                      : {
                          height: `${18 + (s.value / span) * 82}%`,
                          background: `color-mix(in oklab, var(--shaky) ${Math.round((s.value / span) * 100)}%, var(--held))`,
                        }
                  }
                />
                {i === current && <span className="progress" style={{ width: `${progress}%` }} />}
              </button>
            );
          })}
        </div>
        {(compare || frozenRuns.length > 0) && (
          <div className="marks" style={{ gridTemplateColumns: `repeat(${segments.length}, 1fr)` }}>
            {compare && (
              <>
                <span className="group-mark before" style={{ gridColumn: `1 / ${beforeCount + 1}` }}>
                  {label.split(",")[0]} only
                </span>
                <span className="group-mark after" style={{ gridColumn: `${beforeCount + 1} / ${segments.length + 1}` }}>
                  TagTeam
                </span>
              </>
            )}
            {frozenRuns.map(([a, b]) => (
              <span key={a} className="frozen-mark" style={{ gridColumn: `${a + 1} / ${b + 2}` }}>
                Learning frozen
              </span>
            ))}
          </div>
        )}
        {compare ? (
          <p className="key">
            <span>
              {caught ? "Share of each round's phishing payments stopped." : "Net each round: honest payments through minus stolen."}{" "}
              <b>Higher is better</b>
            </span>
            <span><i className="tagteam" /> TagTeam</span>
            <span><i className="model" /> {label.split(",")[0]}</span>
            <span><i className="ghost-key" /> {label.split(",")[0]} on the same payments</span>
            {!caught && <span><i className="negative" /> More stolen than paid</span>}
            {growing && (
              <span className="growth">
                Attacks ramp up: {money(attempted[0])} tried in round 1, peaking at {money(attempted[peak])} in round {peak + 1}.
                A steady net under heavier attack means the defense is keeping up.
              </span>
            )}
          </p>
        ) : (
          <p className="caption">Bar height: how much a fresh attacker could still steal after each round</p>
        )}
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
