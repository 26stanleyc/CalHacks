import { Suspense } from "react";

import ArenaGrid from "@/components/ArenaGrid";
import Curves from "@/components/Curves";
import EnginePanel from "@/components/EnginePanel";
import RunPicker from "@/components/RunPicker";
import { arenaSnapshot, listRuns, resolveRun, roundMetrics } from "@/lib/queries";

type SearchParams = PageProps<"/">["searchParams"];

async function RunOverview({ searchParams }: { searchParams: SearchParams }) {
  const { run } = await searchParams;
  const current = await resolveRun(typeof run === "string" ? run : undefined);
  if (!current) {
    return <p className="card text-sm text-ink-2">No runs with round metrics yet. Seed some with <code>uv run python data/seed.py</code>.</p>;
  }
  const [runs, metrics, arena] = await Promise.all([listRuns(), roundMetrics(current.run_id), arenaSnapshot(current.run_id)]);
  return (
    <>
      <RunPicker runs={runs} current={current.run_id} path="/" />
      <h2 className="mb-2 text-sm font-semibold text-ink-2">Per-round curves · {current.run_id}</h2>
      <Curves metrics={metrics} />
      <div className="mt-4">
        {arena ? <ArenaGrid round={arena.round} shelves={arena.shelves} /> : <p className="card text-sm">No archive snapshots for this run.</p>}
      </div>
    </>
  );
}

export default function Page({ searchParams }: PageProps<"/">) {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Overview</h1>
      <Suspense fallback={<section className="card text-sm text-muted">Asking the engine…</section>}>
        <EnginePanel />
      </Suspense>
      <Suspense fallback={<section className="card text-sm text-muted">Loading the run…</section>}>
        <RunOverview searchParams={searchParams} />
      </Suspense>
    </div>
  );
}
