import type { ShelfKey, ShelfSnapshot } from "@/lib/types";

const STYLES: { key: ShelfKey["style"]; label: string }[] = [
  { key: "burst", label: "Burst" },
  { key: "slow_drip", label: "Slow drip" },
  { key: "many_accounts", label: "Many accounts" },
  { key: "hijack_injection", label: "Hijack / injection" },
];
const AMOUNTS: { key: ShelfKey["amount"]; label: string }[] = [
  { key: "under_10", label: "< $10" },
  { key: "10_to_100", label: "$10–100" },
  { key: "over_100", label: "> $100" },
];
const SIDES: { key: ShelfKey["side"]; label: string }[] = [
  { key: "merchant", label: "Attacks on merchants" },
  { key: "buyer", label: "Attacks on buyers" },
];

// Diverging bins around a coin flip: red = attacks get through, blue = caught.
const BINS = [
  { below: 0.2, fill: "var(--div-neg2)", ink: "#fff", label: "< 20%" },
  { below: 0.4, fill: "var(--div-neg1)", ink: "var(--ink)", label: "20–40%" },
  { below: 0.6, fill: "var(--div-mid)", ink: "var(--ink)", label: "40–60%" },
  { below: 0.8, fill: "var(--div-pos1)", ink: "var(--ink)", label: "60–80%" },
  { below: Infinity, fill: "var(--div-pos2)", ink: "#fff", label: "≥ 80%" },
];

function bin(rate: number) {
  return BINS.find((b) => rate < b.below)!;
}

function Cell({ shelf }: { shelf: ShelfSnapshot | undefined }) {
  const filled = shelf && shelf.attacker_ids.length > 0;
  if (!shelf || !filled) {
    return (
      <td className="h-14 rounded-md border border-dashed border-line text-center text-xs text-muted" data-state="empty">
        empty
      </td>
    );
  }
  const rate = shelf.attempts ? shelf.caught / shelf.attempts : null;
  const b = rate == null ? null : bin(rate);
  const title = `${shelf.key.style} / ${shelf.key.amount} / ${shelf.key.side}: ${shelf.caught}/${shelf.attempts} caught; ` +
    `attackers ${shelf.attacker_ids.join(", ")}${shelf.unknown ? " (unknown: <20 recent attempts)" : ""}`;
  return (
    <td className="h-14 rounded-md text-center text-xs" title={title} data-state="filled"
        style={{ background: b?.fill ?? "var(--div-mid)", color: b?.ink ?? "var(--ink)" }}>
      <div className="tabular text-sm font-semibold">{rate == null ? "—" : `${Math.round(rate * 100)}%`}</div>
      <div className="opacity-80">
        {shelf.attacker_ids.length} atk{shelf.unknown ? " · ?" : ""}
      </div>
    </td>
  );
}

/** The 24-shelf attack archive at its latest round, colored by raw catch rate (caught / attempts). */
export default function ArenaGrid({ round, shelves }: { round: number; shelves: ShelfSnapshot[] }) {
  const byId = new Map(shelves.map((s) => [`${s.key.style}/${s.key.amount}/${s.key.side}`, s]));
  return (
    <section className="card" data-testid="arena-grid" data-shelves={shelves.length}>
      <h3 className="text-sm font-semibold">Arena grid, round {round}</h3>
      <p className="mb-3 text-xs text-muted">
        archive_snapshots: catch rate per shelf over the last 3 rounds. “?” marks a shelf with under 20 recent attempts.
      </p>
      <div className="grid gap-4 md:grid-cols-2">
        {SIDES.map((side) => (
          <table key={side.key} className="w-full border-separate border-spacing-1">
            <caption className="mb-1 text-left text-xs text-ink-2">{side.label}</caption>
            <thead>
              <tr>
                <th />
                {AMOUNTS.map((a) => <th key={a.key} className="text-xs font-normal text-muted">{a.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {STYLES.map((st) => (
                <tr key={st.key}>
                  <th className="pr-1 text-left text-xs font-normal text-ink-2">{st.label}</th>
                  {AMOUNTS.map((a) => <Cell key={a.key} shelf={byId.get(`${st.key}/${a.key}/${side.key}`)} />)}
                </tr>
              ))}
            </tbody>
          </table>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-ink-2">
        <span>Catch rate:</span>
        {BINS.map((b) => (
          <span key={b.label} className="flex items-center gap-1">
            <span className="inline-block h-3 w-4 rounded-sm" style={{ background: b.fill }} />
            {b.label}
          </span>
        ))}
      </div>
    </section>
  );
}
