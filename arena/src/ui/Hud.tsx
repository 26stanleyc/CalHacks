import { useEffect, useState } from "react";
import { flowByRound } from "../data/baseline";
import type { DatasetInfo, Replay } from "../data/types";
import { useArena, phaseAt, ROUND_LEN, waveStart, THROW_WINDOW, FLIGHT, LEARN_START, type Stage } from "../store";
import { getRoundModel, tallyAt } from "../world/roundModel";
import { ACTOR_NAME, attackerName, money, ruleName, shelfName } from "./words";

// A moment in a dataset: a UTC date and time when it has real dates ("Jul 28, 08:00"), otherwise hours
// since its first payment as h:mm ("2:40").
function clock(hours: number, start: string | null): string {
  if (start) {
    const at = new Date(Date.parse(start) + Math.round(hours * 60) * 60_000);
    return at.toLocaleString("en-US", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC" });
  }
  const m = Math.round(hours * 60);
  return `hour ${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}
const span = (from: number, to: number, start: string | null) =>
  `${clock(from, start)} to ${clock(to, start)}${start ? " UTC" : ""}`;

// `ds` is set for a dataset run: each round is ds.round_hours of the dataset, three waves to a round.
function phaseLine(stage: Stage, round: number, t: number, spawns: number, frozen: boolean, model: string, ds?: DatasetInfo,
                   pretrained = false): string {
  const p = phaseAt(t);
  const wave =
    p.kind !== "wave"
      ? ""
      : ds
        ? `Wave ${p.wave} of 3: the dataset's payments from ${span(((round * 3 + p.wave - 1) * ds.round_hours) / 3, ((round * 3 + p.wave) * ds.round_hours) / 3, ds.start)}`
        : `Wave ${p.wave} of 3: attackers try to pay their way through the gate`;
  if (stage === "before") {
    // The baseline only trains after round 1's labels arrive, so round 1 is decided untrained.
    // A pretrained baseline (fitted on days before the test window) is already trained in round 1.
    if (round === 0 && p.kind !== "learn" && !pretrained) return `${model} hasn't trained yet, so this round it lets every payment through`;
    if (p.kind === "spawn") return `${model} is guarding the checkpoint alone`;
    if (p.kind === "wave") return wave;
    // Recorded data doesn't adapt, so a dataset run doesn't claim it does.
    if (round === 0 && !pretrained) return `${model} trains once, on this round's fraud`;
    return `${model} stays frozen${ds ? "" : " while the attackers adapt"}`;
  }
  if (p.kind === "spawn") {
    if (ds) return `Next: the dataset's payments from ${span(round * ds.round_hours, (round + 1) * ds.round_hours, ds.start)}`;
    return spawns ? "Defenders are building attackers aimed at their weak spots" : "Attackers take their places";
  }
  if (p.kind === "wave") return wave;
  return frozen ? "Learning is frozen, so the defense stays as it was" : "Defenders rewrite their rules from what got through";
}

// "XGBoost, trained once" reads as "XGBoost" inside a sentence.
const shortName = (label: string) => label.split(",")[0];

// Money that got through in a run up to this round and moment, and honest shoppers flagged.
// Net = honest money through minus money stolen; higher is better.
function runningTotals(replay: Replay, round: number, t: number) {
  const model = getRoundModel(replay, round);
  const past = flowByRound(replay).slice(0, round);
  const now = tallyAt(model, t);
  const stolen = past.reduce((a, f) => a + f.stolen, 0) + now.stolen;
  const honestThrough = past.reduce((a, f) => a + f.honest, 0) + now.honest;
  let flagged = 0;
  replay.rounds.slice(0, round).forEach((r) =>
    r.waves.forEach((w) => w.payments.forEach((p) => (flagged += !p.is_fraud && p.action !== "allow" ? 1 : 0))),
  );
  for (const c of model.coins) if (!c.attack && c.payment.action !== "allow" && c.intercept <= t) flagged++;
  return { stolen, honest: honestThrough, net: honestThrough - stolen, flagged };
}

export function runTotals(replay: Replay) {
  return runningTotals(replay, replay.rounds.length - 1, ROUND_LEN);
}

export function Hud() {
  const replay = useArena((s) => s.replay)!;
  const runs = useArena((s) => s.runs);
  const stage = useArena((s) => s.stage);
  const round = useArena((s) => s.round);
  // Quarter-second steps are plenty for the scoreboard and keep React out of the frame loop.
  const tq = useArena((s) => Math.floor(s.t * 4) / 4);
  const r = replay.rounds[round];
  const model = getRoundModel(replay, round);
  const tally = tallyAt(model, tq);
  const honest = model.coins.filter((c) => !c.attack && c.launch <= tq).length;
  const flaggedNow = model.coins.filter((c) => !c.attack && c.payment.action !== "allow" && c.intercept <= tq).length;
  const spawns = r.actions.filter((a) => a.action === "spawn").length;
  const done = tq >= LEARN_START;
  const held = Math.round(r.metrics.shelves_defended * 24);
  const attacks = tally.blocked + tally.through;
  const label = runs?.baseline?.label ?? "The baseline";
  const before = stage === "before";
  const ours = runningTotals(replay, round, tq);
  const theirs = !before && runs?.before ? runningTotals(runs.before, round, tq) : null;

  return (
    <>
      {before && <div className="threat-tint" aria-hidden="true" />}
      {before && tally.stolen > 0 && <div key={tally.stolen} className="alarm" aria-hidden="true" />}
      <div className="hud-left">
      <header className="round-title">
        {runs?.before && (
          <p className={`stage-badge ${stage}`}>{before ? "Step 0: without TagTeam" : "TagTeam deployed"}</p>
        )}
        <h1>
          Round {r.round} <span>of {replay.rounds.length}</span>
        </h1>
        <p>{phaseLine(stage, round, tq, spawns, r.metrics.frozen, shortName(label), replay.dataset, !!runs?.baseline?.pretrained)}</p>
        {r.metrics.frozen && <p className="frozen">Learning frozen</p>}
      </header>
      <section className={`scoreboard${before ? " breach" : ""}`} aria-label="This round's score">
        <div className="side angels">
          <span className="label">{before ? "Blocked by XGBoost" : "Blocked"}</span>
          <strong>{tally.blocked}</strong>
        </div>
        <div className="side devils">
          <span className="label">Stolen this round</span>
          <strong>{money(tally.stolen)}</strong>
        </div>
        <p className="fine">
          {attacks > 0 && <>Caught {Math.round((tally.blocked / attacks) * 100)}% of attack payments<br /></>}
          {flaggedNow} honest {flaggedNow === 1 ? "shopper" : "shoppers"} flagged out of {honest}
          {done && !runs?.before && <><br />{held} of 24 shelves held after learning</>}
        </p>
        <p className={`run-total${ours.net < 0 ? " negative" : ""}`}>
          <span>Net so far</span>
          <b>{money(ours.net)}</b>
        </p>
        <p className="breakdown">
          {money(ours.honest)} honest payments through, minus {money(ours.stolen)} stolen
        </p>
        {theirs && (
          <p className="versus">
            {shortName(label)} on the same payments: net <b>{money(theirs.net)}</b> ({money(theirs.stolen)} stolen,{" "}
            {theirs.flagged} honest flagged).{" "}
            <span className={ours.net >= theirs.net ? "saved" : "behind"}>
              {ours.net >= theirs.net
                ? `TagTeam is ${money(ours.net - theirs.net)} ahead so far`
                : `TagTeam is ${money(theirs.net - ours.net)} behind so far`}
            </span>
          </p>
        )}
      </section>
      </div>
      <Feed />
    </>
  );
}

// Step 0 flows straight into TagTeam: a passing note carries step 0's result across without
// stopping, and a summary closes the TagTeam run.
export function StageBanner() {
  const runs = useArena((s) => s.runs);
  const stage = useArena((s) => s.stage);
  const deployedAt = useArena((s) => s.deployedAt);
  const atEnd = useArena((s) => !!s.replay && s.round === s.replay.rounds.length - 1 && s.t >= LEARN_START);
  const setStage = useArena((s) => s.setStage);
  const [showDeploy, setShowDeploy] = useState(false);
  useEffect(() => {
    if (deployedAt === null) return;
    setShowDeploy(true);
    const id = setTimeout(() => setShowDeploy(false), 5000);
    return () => clearTimeout(id);
  }, [deployedAt]);
  if (!runs?.before || !runs.baseline || stage === "before") return null;

  const label = runs.baseline.label;
  const them = runTotals(runs.before);
  const us = runTotals(runs.after);
  const worst = flowByRound(runs.before)
    .map((f, i) => ({ net: f.net, round: i + 1 }))
    .reduce((w, f) => (f.net < w.net ? f : w));
  const saved = them.stolen - us.stolen;
  const ratio = them.net > 0 ? us.net / them.net : null;

  return (
    <>
      {showDeploy && (
        <p className="deploy-note" role="status">
          <b>TagTeam deployed.</b> Without it, {shortName(label)} netted {money(them.net)}
          {worst.net < 0 ? ` and went negative in round ${worst.round}` : ""}. Same payments, same attackers.
        </p>
      )}
      {atEnd && (
        <p className="final-note" role="status">
          <b>
            Net {money(us.net)}
            {ratio && ratio >= 1.1 ? `, ${ratio.toFixed(1)}× ${shortName(label)}'s.` : ` against ${money(them.net)}.`}
          </b>{" "}
          On the same payments, {money(us.stolen)} of fraud got through against {money(them.stolen)}
          {them.stolen > 0 && ` (${Math.round((Math.abs(saved) / them.stolen) * 100)}% ${saved >= 0 ? "less" : "more"})`}, and{" "}
          {us.flagged} honest shoppers were flagged against {them.flagged}.{" "}
          <button className="replay" onClick={() => setStage("before")}>
            Replay step 0
          </button>
        </p>
      )}
    </>
  );
}

interface FeedItem {
  at: number;
  key: string;
  text: string;
  tone: "angel" | "devil" | "news";
}

// Triggers read "caught 3 of 4" (mock) or "caught on 3 of 4 attempts; rewrote the strategy" (engine).
function changingPlan(name: string, trigger: string): string {
  const m = trigger.match(/caught (?:on )?(\d+) of (\d+)/);
  return m ? `${name} got caught ${m[1]} of ${m[2]} times and is changing its plan` : `${name} is changing its plan`;
}

function Feed() {
  const replay = useArena((s) => s.replay)!;
  const compare = useArena((s) => !!s.runs?.before);
  const round = useArena((s) => s.round);
  const tq = useArena((s) => Math.floor(s.t * 4) / 4);
  const r = replay.rounds[round];
  const names = new Map(r.attackers.map((a) => [a.attacker_id, attackerName(a.name)]));

  const items: FeedItem[] = [];
  r.news.forEach((n) =>
    items.push({ at: 0.2, key: n.report_id, tone: "news", text: `New scam in the news: "${n.title}" became ${n.attacker_ids.length} attackers` }),
  );
  r.actions
    .filter((a) => a.action === "spawn")
    .forEach((a, i) =>
      items.push({
        at: 0.4 + i * 0.25,
        key: `spawn-${a.actor}`,
        tone: "angel",
        text: `${ACTOR_NAME[a.actor]} built ${names.get(a.attacker_id!) ?? "an attacker"} to test ${shelfName(a.shelf!).toLowerCase()}`,
      }),
    );
  r.waves.forEach((w) =>
    w.retunes
      .filter((x) => x.strategy_changed)
      .slice(0, 1)
      .forEach((x) =>
        items.push({
          at: waveStart(w.wave) + THROW_WINDOW + FLIGHT,
          key: `retune-${w.wave}-${x.attacker_id}`,
          tone: "devil",
          text: changingPlan(names.get(x.attacker_id) ?? "An attacker", x.trigger),
        }),
      ),
  );
  if (!r.metrics.frozen)
    r.rulebooks
      .filter((b) => b.added)
      .forEach((b, i) =>
        items.push({
          at: LEARN_START + 0.5 + i * 0.3,
          key: `rule-${b.lane}`,
          tone: "angel",
          text: `${ACTOR_NAME[b.lane]} added a rule: ${ruleName(b.rules[b.rules.length - 1])}`,
        }),
      );

  // The comparison scoreboard is taller, so the feed shows fewer lines to stay clear of it.
  const shown = items.filter((i) => i.at <= tq && tq < ROUND_LEN).sort((a, b) => b.at - a.at).slice(0, compare ? 2 : 3);
  return (
    <ol className="feed" aria-live="polite">
      {shown.map((i) => (
        <li key={i.key} className={i.tone}>
          {i.text}
        </li>
      ))}
    </ol>
  );
}
