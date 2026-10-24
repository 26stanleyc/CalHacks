import Link from "next/link";

import type { RunRow } from "@/lib/queries";

/** Pick which logged run the page reads (?run=). Seed runs show until the engine writes real ones. */
export default function RunPicker({ runs, current, path }: { runs: RunRow[]; current: string; path: string }) {
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2 text-sm" data-testid="run-picker">
      <span className="text-muted">Run</span>
      {runs.map((r) => (
        <Link
          key={r.run_id}
          href={`${path}?run=${encodeURIComponent(r.run_id)}`}
          className="rounded-md border border-line px-2 py-1 hover:bg-surface"
          style={r.run_id === current ? { borderColor: "var(--series-1)", color: "var(--ink)" } : { color: "var(--ink-2)" }}
          data-current={r.run_id === current}
        >
          {r.run_id} <span className="text-muted">· {r.defense} · seed {r.seed} · {r.rounds} rounds</span>
        </Link>
      ))}
    </div>
  );
}
