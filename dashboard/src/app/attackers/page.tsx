import { Suspense } from "react";

import DriftChart, { type DriftPoint } from "@/components/DriftChart";
import RunPicker from "@/components/RunPicker";
import { listRuns, outsiderDrift, resolveRun, type DriftRow } from "@/lib/queries";

type SearchParams = PageProps<"/attackers">["searchParams"];

async function Drift({ searchParams }: { searchParams: SearchParams }) {
  const { run } = await searchParams;
  const current = await resolveRun(typeof run === "string" ? run : undefined);
  if (!current) return <p className="card text-sm">No runs yet.</p>;
  const [runs, rows] = await Promise.all([listRuns(), outsiderDrift(current.run_id)]);
  const lineages = new Map<string, DriftRow[]>();
  for (const r of rows) lineages.set(r.attacker_id, [...(lineages.get(r.attacker_id) ?? []), r]);
  return (
    <>
      <RunPicker runs={runs} current={current.run_id} path="/attackers" />
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {[...lineages.entries()].map(([id, waves]) => {
          const points: DriftPoint[] = waves.map((w) => ({
            t: w.round + w.wave / 3, round: w.round, wave: w.wave, amount: (w.amount_cents ?? 0) / 100,
            changed: w.strategy_changed, strategy: w.strategy,
          }));
          const rewrites = waves.filter((w) => w.strategy_changed);
          return (
            <section key={id} className="card" data-testid="drift-card">
              <h3 className="text-sm font-semibold">{waves[0].name}</h3>
              <p className="mb-1 text-xs text-muted">{id} · playbook amount per wave · {rewrites.length} strategy rewrites</p>
              <DriftChart points={points} />
              <p className="mt-1 text-xs text-ink-2" title={waves[waves.length - 1].strategy}>
                Now: {waves[waves.length - 1].strategy}
              </p>
            </section>
          );
        })}
      </div>
      <p className="mt-3 text-xs text-muted">
        playbook_waves for the outsider lineages (attackers.source = outsider). Ringed points: the lineage rewrote its
        strategy after being caught on most of the previous wave.
      </p>
    </>
  );
}

export default function Page({ searchParams }: PageProps<"/attackers">) {
  return (
    <div>
      <h1 className="mb-3 text-xl font-semibold">Attacker drift</h1>
      <Suspense fallback={<p className="card text-sm text-muted">Loading lineages…</p>}>
        <Drift searchParams={searchParams} />
      </Suspense>
    </div>
  );
}
