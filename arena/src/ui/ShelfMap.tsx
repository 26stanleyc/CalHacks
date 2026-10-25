// The arena flattened into the Rainbow Teaming grid: one cell per shelf, coloured by its live
// catch rate, exactly like the 3D ring. A gold halo marks
// the shelves a defender built an attacker for this round (orange: a news scam landed).

import { AMOUNTS, SIDES, STYLES, shelfId } from "../data/mock";
import { C, shelfColor } from "../palette";
import { useArena } from "../store";
import { liveShelves } from "../world/shelfLive";
import { ACTOR_NAME, shelfName } from "./words";

const ROW_LABEL: Record<string, string> = {
  burst: "Bursts",
  slow_drip: "Slow drip",
  many_accounts: "Many accounts",
  hijack_injection: "Hijack",
};
const AMOUNT_LABEL: Record<string, string> = { under_10: "<$10", "10_to_100": "$10–100", over_100: ">$100" };


export function ShelfMap() {
  const replay = useArena((s) => s.replay)!;
  const round = useArena((s) => s.round);
  const selected = useArena((s) => s.selected);
  const select = useArena((s) => s.select);
  // Quarter-second steps keep React re-renders down; the numbers come from the same live tally as the tiles.
  const tq = useArena((s) => Math.floor(s.t * 4) / 4);
  const live = liveShelves(replay, round, tq);

  const r = replay.rounds[round];
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
                  const shown = live.get(id);
                  const rate = shown && !shown.unknown && shown.attempts ? Math.round((shown.caught / shown.attempts) * 100) : null;
                  const mark = news.get(id) ?? aimedBy.get(id);
                  return (
                    <td key={id}>
                      <button
                        className={`cell${selected?.kind === "shelf" && selected.id === id ? " selected" : ""}`}
                        style={{ background: shelfColor(shown) }}
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
