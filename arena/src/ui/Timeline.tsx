// The round scrubber doubles as a chart. With a baseline (step 0) loaded, the bar runs straight
// through both stages: the baseline's rounds first, then TagTeam's, each bar that round's money
// lost (fraud that got through plus honest payments stopped; from zero, lower is better). TagTeam's
// bars carry an outline with the baseline's loss on the same round. A bar far taller than the rest
// (the baseline's untrained first round) is cut at the top and marked, so it doesn't flatten every
// other round; its real value is in its label. Without a baseline, each bar is how much a fresh
// attacker could still steal from the frozen defense (exploitability; lower is better).

import { useEffect, useRef } from "react";
import { flowByRound } from "../data/baseline";
import { useArena, ROUND_LEN, type Stage } from "../store";
import { money, sameWhat } from "./words";

const SPEEDS = [1, 2, 4, 8];

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
  // The bar's height changes with width (the key wraps); the panels above it read --timeline-h.
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = ref.current!;
    const ro = new ResizeObserver(() => el.parentElement!.style.setProperty("--timeline-h", `${el.offsetHeight}px`));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const label = runs?.baseline?.label ?? "";

  let segments: Segment[];
  if (compare) {
    const before = flowByRound(runs!.before!).map((f) => f.lost);
    const after = flowByRound(runs!.after).map((f) => f.lost);
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
  const peak = attempted.reduce((best, v, i) => (v > attempted[best] ? i : best), 0);
  const growing = attempted.length > 1 && attempted[peak] > attempted[0] * 1.2;
  const beforeCount = compare ? runs!.before!.rounds.length : 0;

  // Losses start at zero. An isolated outlier (more than 1.5x the next tallest bar, like XGBoost
  // untrained in round 1) is cut and marked at the next tallest bar's height instead of flattening
  // every other bar; nothing else is cut.
  const values = segments.flatMap((s) => (s.ghost === null ? [s.value] : [s.value, s.ghost]));
  const [top = 1, next = 1] = [...new Set(values)].sort((a, b) => b - a);
  const span = Math.max(compare && top > 1.5 * next ? next : top, 1);
  const clipped = (v: number) => compare && v > span;
  // Totals per half, for the group labels: what each defense lost over the whole run.
  const lostBefore = segments.filter((s) => s.stage === "before").reduce((a, s) => a + s.value, 0);
  const lostAfter = segments.filter((s) => s.stage === "after").reduce((a, s) => a + s.value, 0);
  const lessPct = lostBefore > 0 ? Math.round((1 - lostAfter / lostBefore) * 100) : 0;
  const k = (cents: number) => `$${(cents / 100_000).toFixed(1)}k`;
  const lossBox = (v: number) => ({ bottom: "0%", height: `${Math.max((Math.min(v, span) / span) * 100, 3)}%` });
  // The TagTeam half shows the difference itself: how much less TagTeam lost than XGBoost that round,
  // up from a zero line (TagTeam lost less) or down in red (it lost more). Same outlier rule as above.
  const saved = (s: Segment) => (s.ghost ?? 0) - s.value;
  const savings = segments.filter((s) => s.stage === "after").map((s) => Math.abs(saved(s)));
  const [sTop = 1, sNext = 1] = [...new Set(savings)].sort((a, b) => b - a);
  const sSpan = Math.max(sTop > 1.5 * sNext ? sNext : sTop, 1);
  const ZERO = 30; // % from the bottom: room below the line for rounds TagTeam lost more
  const saveBox = (v: number) => {
    const h = Math.max((Math.min(Math.abs(v), sSpan) / sSpan) * (v >= 0 ? 100 - ZERO : ZERO), 2);
    return v >= 0 ? { bottom: `${ZERO}%`, height: `${h}%` } : { bottom: `${ZERO - h}%`, height: `${h}%` };
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
    <footer className="timeline" ref={ref}>
      <button className="play" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
        {playing ? (
          <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="4" y="3" width="4" height="14" rx="1.5" /><rect x="12" y="3" width="4" height="14" rx="1.5" /></svg>
        ) : (
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 3.5v13a1 1 0 0 0 1.5.86l11-6.5a1 1 0 0 0 0-1.72l-11-6.5A1 1 0 0 0 5 3.5z" /></svg>
        )}
      </button>
      <div className={`track${segments.length > 24 ? " dense" : ""}`}>
        <div className={`bars${compare ? " net" : ""}`} role="group" aria-label="Rounds">
          {segments.map((s, i) => {
            const who = compare ? (s.stage === "before" ? `${label}, round ${s.round}` : `TagTeam, round ${s.round}`) : `Round ${s.round}`;
            const what = compare ? `${money(s.value)} lost` : `${money(s.value)} still stealable`;
            const extra = s.ghost !== null
              ? `; ${label} ${sameWhat(runs?.baseline?.live)}: ${money(s.ghost)} lost; TagTeam ${saved(s) >= 0 ? `lost ${money(saved(s))} less` : `lost ${money(-saved(s))} more`}`
              : "";
            const diff = compare && s.stage === "after";
            return (
              <button
                key={`${s.stage}-${s.index}`}
                className={`bar${i === current ? " current" : ""}${i < current ? " past" : ""}${s.frozen ? " frozen" : ""}`}
                onClick={(e) => {
                  go(s);
                  if (e.detail > 0) e.currentTarget.blur(); // after a mouse click, Space goes back to play/pause
                }}
                aria-label={`${who}: ${what}${extra}`}
                title={`${who}: ${what}${extra.replace("; ", "\n")}`}
              >
                {diff && <span className="zero" style={{ bottom: `${ZERO}%` }} />}
                {!diff && clipped(s.value) && <span className="cut-label">{k(s.value)}</span>}
                {diff && Math.abs(saved(s)) > sSpan && <span className="cut-label">{k(saved(s))}</span>}
                <span
                  className={`fill${(diff ? Math.abs(saved(s)) > sSpan : clipped(s.value)) ? " clipped" : ""}`}
                  style={
                    diff
                      ? { ...saveBox(saved(s)), background: saved(s) >= 0 ? "var(--seraph)" : "var(--shaky)" }
                      : compare
                      ? { ...lossBox(s.value), background: "var(--model)" }
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
                  {label.split(",")[0]} only · lost {k(lostBefore)}
                </span>
                <span className="group-mark after" style={{ gridColumn: `${beforeCount + 1} / ${segments.length + 1}` }}>
                  TagTeam · lost {k(lostAfter)}{lessPct > 0 ? `, ${lessPct}% less` : ""}
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
              Left: money {label.split(",")[0]} lost each round. Right: how much less TagTeam lost than {label.split(",")[0]}{" "}
              {sameWhat(runs?.baseline?.live)}, round by round.
            </span>
            <span><i className="model" /> {label.split(",")[0]} lost</span>
            <span><i className="tagteam" /> TagTeam lost less</span>
            <span><i className="negative" /> TagTeam lost more</span>
            <span><i className="cut-key" /> Cut: taller than shown (amount on the bar)</span>
            {growing && (
              <span className="growth">
                Attacks ramp up: {money(attempted[0])} tried in round 1, peaking at {money(attempted[peak])} in round {peak + 1}.
              </span>
            )}
          </p>
        ) : (
          <p className="caption">Bar height: how much a fresh attacker could still steal after each round</p>
        )}
      </div>
      <div className="speeds" role="group" aria-label="Playback speed">
        {SPEEDS.map((s) => (
          <button
            key={s}
            className={s === speed ? "on" : ""}
            onClick={(e) => {
              setSpeed(s);
              if (e.detail > 0) e.currentTarget.blur();
            }}
            aria-pressed={s === speed}
          >
            {s}×
          </button>
        ))}
      </div>
    </footer>
  );
}
