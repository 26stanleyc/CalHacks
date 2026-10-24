// The arena flattened into the Rainbow Teaming grid: one cell per shelf, coloured by
// catch rate and recoloured during learning, exactly like the 3D ring. A gold halo marks
// the shelves a defender built an attacker for this round (orange: a news scam landed).

import * as THREE from "three";
import { AMOUNTS, SIDES, STYLES, shelfId } from "../data/mock";
import { C, shelfColor } from "../palette";
import { LEARN_START, ROUND_LEN, useArena } from "../store";
import { ACTOR_NAME, shelfName } from "./words";

const ROW_LABEL: Record<string, string> = {
  burst: "Bursts",
  slow_drip: "Slow drip",
  many_accounts: "Many accounts",
  hijack_injection: "Hijack",
};
const AMOUNT_LABEL: Record<string, string> = { under_10: "<$10", "10_to_100": "$10–100", over_100: ">$100" };

const mix = (a: string, b: string, k: number) => `#${new THREE.Color(a).lerp(new THREE.Color(b), k).getHexString()}`;
const step20 = (x: number) => Math.round(Math.min(1, Math.max(0, x)) * 20) / 20;

export function ShelfMap() {
  const replay = useArena((s) => s.replay)!;
  const round = useArena((s) => s.round);
  const selected = useArena((s) => s.selected);
  const select = useArena((s) => s.select);
  // Coarse steps keep React re-renders to a handful per round.
  const learnK = useArena((s) => step20((s.t - LEARN_START - 0.5) / (ROUND_LEN - LEARN_START - 1)));

  const r = replay.rounds[round];
  const before = round > 0 ? replay.rounds[round - 1].shelves : [];
  const aimedBy = new Map<string, string>();
  for (const a of r.actions) if (a.action === "spawn" && a.shelf) aimedBy.set(a.shelf, `${ACTOR_NAME[a.actor]} built an attacker here`);
  const news = new Map(r.news.map((n) => [n.shelf, `New scam from the news: ${n.title}`]));

  return (
    <section className="shelf-map" aria-label="Catch rate on each arena shelf">
      <table>
        <thead>
          <tr>
            <th />
            <th colSpan={3}>Against stores</th>
            <th className="gap" />
            <th colSpan={3}>Against shoppers</th>
          </tr>
          <tr className="amounts">
            <th />
            {AMOUNTS.map((a) => <th key={`m${a}`}>{AMOUNT_LABEL[a]}</th>)}
            <th className="gap" />
            {AMOUNTS.map((a) => <th key={`b${a}`}>{AMOUNT_LABEL[a]}</th>)}
          </tr>
        </thead>
        <tbody>
          {STYLES.map((style) => (
            <tr key={style}>
              <th scope="row">{ROW_LABEL[style]}</th>
              {SIDES.flatMap((side, si) => [
                ...(si === 1 ? [<td key={`${style}-gap`} className="gap" />] : []),
                ...AMOUNTS.map((amount) => {
                  const id = shelfId(style, amount, side);
                  const prev = before.find((s) => s.shelf === id);
                  const next = r.shelves.find((s) => s.shelf === id);
                  const shown = learnK < 0.5 ? prev : next;
                  const rate = shown && !shown.unknown && shown.attempts ? Math.round((shown.caught / shown.attempts) * 100) : null;
                  const mark = news.get(id) ?? aimedBy.get(id);
                  return (
                    <td key={id}>
                      <button
                        className={`cell${selected?.kind === "shelf" && selected.id === id ? " selected" : ""}`}
                        style={{ background: mix(shelfColor(prev), shelfColor(next), learnK) }}
                        onClick={() => select({ kind: "shelf", id })}
                        title={`${shelfName(id)}${rate === null ? "" : `: caught ${rate}%`}${mark ? `. ${mark}.` : ""}`}
                      >
                        {rate !== null && `${rate}%`}
                        {mark && <i className={news.has(id) ? "mark news" : "mark"} aria-hidden="true" />}
                      </button>
                    </td>
                  );
                }),
              ])}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="key">
        <span><i style={{ background: C.shaky }} />under 60%</span>
        <span><i style={{ background: C.wobbly }} />60–80%</span>
        <span><i style={{ background: C.held }} />80%+</span>
        <span><i style={{ background: C.unknown }} />too early</span>
        <span><i className="halo" />attacker built this round</span>
      </p>
    </section>
  );
}
