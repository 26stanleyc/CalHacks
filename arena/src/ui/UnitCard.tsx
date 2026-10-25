// The card that opens when you click a creature, a shelf or the gate.

import type { Actor, Lane } from "../data/types";
import { useArena, waveStart, THROW_WINDOW, FLIGHT } from "../store";
import { getRoundModel, latestAttacker } from "../world/roundModel";
import { RECENT_ROUNDS, liveShelves } from "../world/shelfLive";
import { ACTOR_NAME, SOURCE_LINE, attackerName, modelName, money, paramLines, ruleName, shelfName } from "./words";

function Close() {
  const select = useArena((s) => s.select);
  return (
    <button className="close" onClick={() => select(null)} aria-label="Close">
      <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 5l10 10M15 5L5 15" /></svg>
    </button>
  );
}

function AttackerCard({ id }: { id: string }) {
  const replay = useArena((s) => s.replay)!;
  const round = useArena((s) => s.round);
  const tq = useArena((s) => Math.floor(s.t * 4) / 4);
  const select = useArena((s) => s.select);
  const found = latestAttacker(replay, round, id);
  if (!found) return null;
  const { info: a, ran: active } = found;
  const r = replay.rounds[round];
  const onShelf = r.shelves.some((s) => s.outsider_slot === id || s.attacker_ids.includes(id));
  // A payment's result shows once its coin is zapped or lands, not before.
  const resolvedAt = new Map(
    getRoundModel(replay, round).coins.map((c) => [c.payment.tx_id, c.outcome === "blocked" ? c.intercept : c.land]),
  );
  const resolved = (txId: string) => (resolvedAt.get(txId) ?? 0) <= tq;
  const origin =
    a.source === "spawned"
      ? `Built by the ${ACTOR_NAME[a.parent as Actor]} to beat its own rules`
      : a.source === "outsider"
        ? "A classic fraudster who only sees whether it got through"
        : a.source === "threat_intel"
          ? "Copied from a scam report in the news"
          : "Kept for the final exam; never trained against";

  return (
    <article className="card devil-card">
      <Close />
      <div className="who">
        <span className={`token devil ${a.source}`} aria-hidden="true" />
        <div>
          <p className="kind">{SOURCE_LINE[a.source]} attacker</p>
          <h2>{attackerName(a.name)}</h2>
        </div>
      </div>
      <p className="origin">{origin}</p>
      <dl className="facts">
        <dt>Target</dt>
        <dd>
          <button className="link" onClick={() => select({ kind: "shelf", id: a.shelf })}>
            {shelfName(a.shelf)}
          </button>
        </dd>
        <dt>Runs on</dt>
        <dd>{modelName(a.model)}</dd>
        <dt>Profit so far</dt>
        <dd>{money(a.profit_cents)}</dd>
      </dl>
      <h3>Plan</h3>
      <blockquote>{a.strategy}</blockquote>
      <ul className="chips">
        {paramLines(a.params).map(([k, v]) => (
          <li key={k}>
            <span>{k}</span> {v}
          </li>
        ))}
      </ul>
      {active ? (
        <>
          <h3>This round</h3>
          <ol className="waves">
            {r.waves.map((w) => {
              const mine = w.payments.filter((p) => p.attacker_id === id);
              const retune = w.retunes.find((x) => x.attacker_id === id);
              const shownAt = waveStart(w.wave);
              if (tq < shownAt) return <li key={w.wave} className="pending">Wave {w.wave}: waiting</li>;
              const through = mine.filter((p) => p.settled && resolved(p.tx_id)).length;
              const open = mine.filter((p) => !resolved(p.tx_id)).length;
              return (
                <li key={w.wave}>
                  <span className="wave-name">Wave {w.wave}</span>
                  <span className="pips" aria-label={`${through} of ${mine.length} got through`}>
                    {mine.map((p) => (
                      <i key={p.tx_id} className={!resolved(p.tx_id) ? "open" : p.settled ? "through" : "caught"} />
                    ))}
                  </span>
                  <span className="wave-note">
                    {open ? `${open} in flight` : `${through} of ${mine.length} through`}
                    {retune && tq >= shownAt + THROW_WINDOW + FLIGHT && (retune.strategy_changed ? ", rethinking its plan" : ", fine-tuning numbers")}
                  </span>
                </li>
              );
            })}
          </ol>
        </>
      ) : (
        <p className="muted">
          {onShelf
            ? "Waiting on its shelf. The arena didn't draw it to attack this round."
            : "No longer in the arena. It lost its spot to stronger attackers."}
        </p>
      )}
    </article>
  );
}

function DefenderCard({ actor }: { actor: Actor }) {
  const replay = useArena((s) => s.replay)!;
  const round = useArena((s) => s.round);
  const select = useArena((s) => s.select);
  const r = replay.rounds[round];
  const next = replay.rounds[round + 1]?.actions.find((a) => a.actor === actor);
  const now = r.actions.find((a) => a.actor === actor);
  const info = replay.defenders.find((d) => d.lane === actor);
  const book = r.rulebooks.find((b) => b.lane === actor);
  const payments = r.waves.flatMap((w) => w.payments).filter((p) => actor !== "orchestrator" && p.objected_by.includes(actor as Lane));
  const stopped = payments.filter((p) => p.is_fraud && !p.settled).length;
  const falseAlarms = payments.filter((p) => !p.is_fraud && !p.settled).length;
  const spawned = now?.attacker_id ? r.attackers.find((a) => a.attacker_id === now.attacker_id) : undefined;

  return (
    <article className="card angel-card">
      <Close />
      <div className="who">
        <span className="token angel" aria-hidden="true" />
        <div>
          <p className="kind">{actor === "orchestrator" ? "Runs the team" : "Specialist defender"}</p>
          <h2>{ACTOR_NAME[actor]}</h2>
        </div>
      </div>
      <p className="origin">
        {actor === "orchestrator"
          ? "Weighs every specialist's vote, makes the final call, and builds attackers aimed at the gaps between specialists."
          : `“${info?.question}”`}
      </p>
      <dl className="facts">
        <dt>Runs on</dt>
        <dd>{actor === "orchestrator" ? "GLM-5.3" : modelName(info?.model ?? null)}</dd>
        {actor !== "orchestrator" && (
          <>
            <dt>This round</dt>
            <dd>
              Stopped {stopped} fraud {stopped === 1 ? "payment" : "payments"}, {falseAlarms} false {falseAlarms === 1 ? "alarm" : "alarms"}
            </dd>
          </>
        )}
      </dl>
      {now && now.action === "spawn" && (
        <>
          <h3>This round's move</h3>
          <p>
            Built{" "}
            {spawned ? (
              <button className="link" onClick={() => select({ kind: "attacker", id: spawned.attacker_id })}>
                {attackerName(spawned.name)}
              </button>
            ) : (
              "an attacker"
            )}{" "}
            to test {shelfName(now.shelf!).toLowerCase()}. {now.reason}
          </p>
        </>
      )}
      {next && (
        <>
          <h3>Next move</h3>
          <p>{next.action === "spawn" ? `Build an attacker for ${shelfName(next.shelf!).toLowerCase()}. ${next.reason}` : `Hold off. ${next.reason}`}</p>
        </>
      )}
      {book && (
        <>
          <h3>
            Rulebook <span className="count">{book.rules.length} rules</span>
          </h3>
          <ul className="rules">
            {book.rules.slice(-6).map((rule) => (
              <li key={rule}>{ruleName(rule)}</li>
            ))}
            {book.watch_rules.map((rule) => (
              <li key={rule} className="watch">
                <span className="tag">watch</span> {rule.replace(/^watch: /, "")}
              </li>
            ))}
          </ul>
        </>
      )}
    </article>
  );
}

function ShelfCard({ id }: { id: string }) {
  const replay = useArena((s) => s.replay)!;
  const round = useArena((s) => s.round);
  const select = useArena((s) => s.select);
  const tq = useArena((s) => Math.floor(s.t * 4) / 4);
  // Same live tally as the tile and the shelf map, so the three always agree.
  const s = liveShelves(replay, round, tq).get(id)!;
  const rate = s.attempts ? s.caught / s.attempts : null;
  const name = (aid: string) => attackerName(latestAttacker(replay, round, aid)?.info.name ?? aid);
  // The engine can list the outsider-spot attacker in attacker_ids too; show each attacker once.
  const ids = [...new Set([...(s.outsider_slot ? [s.outsider_slot] : []), ...s.attacker_ids])];

  return (
    <article className="card shelf-card">
      <Close />
      <p className="kind">Arena shelf</p>
      <h2>{shelfName(id)}</h2>
      <div className="meter" aria-label={rate === null ? "No attempts yet" : `Caught ${Math.round(rate * 100)}%`}>
        <span style={{ width: `${Math.round((rate ?? 0) * 100)}%` }} />
      </div>
      <p className="muted">
        {s.attempts === 0
          ? "No attacks here yet."
          : s.unknown
            ? `Only ${s.attempts} recent attempts, so it's too early to trust the catch rate.`
            : `The defense caught ${s.caught} of the last ${s.attempts} attempts (${Math.round(rate! * 100)}%).`}{" "}
        Counts the last {RECENT_ROUNDS} rounds, this one included as it plays.
      </p>
      <h3>Top attackers kept here</h3>
      {ids.length ? (
        <ul className="roster">
          {ids.map((aid) => (
            <li key={aid}>
              <button className="link" onClick={() => select({ kind: "attacker", id: aid })}>
                {name(aid)}
              </button>
              {aid === s.outsider_slot && <span className="tag">outsider spot</span>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Empty. No attacker has tried this kind of attack yet.</p>
      )}
    </article>
  );
}

function GateCard() {
  const replay = useArena((s) => s.replay)!;
  const round = useArena((s) => s.round);
  const m = replay.rounds[round].metrics;
  return (
    <article className="card gate-card">
      <Close />
      <p className="kind">The checkpoint</p>
      <h2>x402 gate</h2>
      <p className="origin">Every payment in the network passes here. The defense decides whether it settles.</p>
      <dl className="facts">
        <dt>Stolen from stores</dt>
        <dd>{money(m.loss_merchant_side_cents)}</dd>
        <dt>Stolen from shoppers</dt>
        <dd>{money(m.loss_buyer_side_cents)}</dd>
        <dt>False alarms</dt>
        <dd>{(m.false_positive_rate * 100).toFixed(1)}% of honest payments</dd>
        <dt>Still stealable</dt>
        <dd>{money(m.exploitability_cents)} by a fresh attacker</dd>
      </dl>
    </article>
  );
}

export function UnitCard() {
  const selected = useArena((s) => s.selected);
  if (!selected) return null;
  switch (selected.kind) {
    case "attacker":
      return <AttackerCard key={selected.id} id={selected.id} />;
    case "defender":
      return <DefenderCard key={selected.id} actor={selected.id as Actor} />;
    case "shelf":
      return <ShelfCard key={selected.id} id={selected.id} />;
    case "gate":
      return <GateCard />;
  }
}
