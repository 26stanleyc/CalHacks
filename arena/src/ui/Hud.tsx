import { useArena, phaseAt, ROUND_LEN, waveStart, THROW_WINDOW, FLIGHT, LEARN_START } from "../store";
import { getRoundModel, tallyAt } from "../world/roundModel";
import { ACTOR_NAME, money, shelfName } from "./words";

function phaseLine(t: number, spawns: number, frozen: boolean): string {
  const p = phaseAt(t);
  if (p.kind === "spawn") return spawns ? "Defenders are building attackers aimed at their weak spots" : "Attackers take their places";
  if (p.kind === "wave") return `Wave ${p.wave} of 3: attackers try to pay their way through the gate`;
  return frozen ? "Learning is frozen, so the defense stays as it was" : "Defenders rewrite their rules from what got through";
}

export function Hud() {
  const replay = useArena((s) => s.replay)!;
  const round = useArena((s) => s.round);
  // Quarter-second steps are plenty for the scoreboard and keep React out of the frame loop.
  const tq = useArena((s) => Math.floor(s.t * 4) / 4);
  const r = replay.rounds[round];
  const model = getRoundModel(replay, round);
  const tally = tallyAt(model, tq);
  const honest = model.coins.filter((c) => !c.attack && c.launch <= tq).length;
  const spawns = r.actions.filter((a) => a.action === "spawn").length;
  const done = tq >= LEARN_START;
  const held = Math.round(r.metrics.shelves_defended * 24);
  const attacks = tally.blocked + tally.through;

  return (
    <>
      <div className="hud-left">
      <header className="round-title">
        <h1>
          Round {r.round} <span>of {replay.rounds.length}</span>
        </h1>
        <p>{phaseLine(tq, spawns, r.metrics.frozen)}</p>
        {r.metrics.frozen && <p className="frozen">Learning frozen</p>}
      </header>
      <section className="scoreboard" aria-label="This round's score">
        <div className="side angels">
          <span className="label">Blocked</span>
          <strong>{tally.blocked}</strong>
        </div>
        <div className="side devils">
          <span className="label">Stolen</span>
          <strong>{money(tally.stolen)}</strong>
        </div>
        <p className="fine">
          {attacks > 0 && <>Caught {Math.round((tally.blocked / attacks) * 100)}% of attack payments<br /></>}
          {tally.falseAlarms} false {tally.falseAlarms === 1 ? "alarm" : "alarms"} in {honest} honest payments
          {done && <><br />{held} of 24 shelves held after learning</>}
        </p>
      </section>
      </div>
      <Feed />
    </>
  );
}

interface FeedItem {
  at: number;
  key: string;
  text: string;
  tone: "angel" | "devil" | "news";
}

function Feed() {
  const replay = useArena((s) => s.replay)!;
  const round = useArena((s) => s.round);
  const tq = useArena((s) => Math.floor(s.t * 4) / 4);
  const r = replay.rounds[round];
  const names = new Map(r.attackers.map((a) => [a.attacker_id, a.name]));

  const items: FeedItem[] = [];
  r.news.forEach((n) =>
    items.push({ at: 0.2, key: n.report_id, tone: "news", text: `New scam in the news: "${n.title}" became ${n.attacker_ids.length} attackers` }),
  );
  r.actions
    .filter((a) => a.action === "spawn")
    .forEach((a, i) =>
      items.push({
        at: 0.4 + i * 0.25,
        key: `spawn-${a.attacker_id}`,
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
          text: `${names.get(x.attacker_id)} got ${x.trigger} and is changing its plan`,
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
          text: `${ACTOR_NAME[b.lane]} added a rule: ${b.rules[b.rules.length - 1]}`,
        }),
      );

  const shown = items.filter((i) => i.at <= tq && tq < ROUND_LEN).sort((a, b) => b.at - a.at).slice(0, 3);
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
