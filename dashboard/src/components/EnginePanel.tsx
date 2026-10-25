import { runRound, toggleFreeze } from "@/app/actions";
import { ENGINE_URL, engineMetrics } from "@/lib/engine";
import { dollars, pct } from "@/lib/format";

function Stat({ id, label, value }: { id: string; label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-muted">{label}</div>
      <div className="tabular text-lg font-semibold" data-testid={`engine-${id}`}>{value}</div>
    </div>
  );
}

/** Live numbers and controls from the engine API (GET /metrics, POST /freeze, POST /run_round). */
export default async function EnginePanel() {
  const m = await engineMetrics();
  if (!m) {
    return (
      <section className="card text-sm" data-testid="engine-offline">
        <h2 className="font-semibold">Engine</h2>
        <p className="mt-1 text-ink-2">
          The engine API at {ENGINE_URL} isn&apos;t answering. Start it with{" "}
          <code>uv run uvicorn contracts.api:create_stub_app --factory --port 8000</code>.
        </p>
      </section>
    );
  }
  return (
    <section className="card" data-testid="engine-panel">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">Engine (live)</h2>
          <p className="text-xs text-muted">
            GET /metrics · run <span data-testid="engine-run">{m.run_id}</span>, round{" "}
            <span data-testid="engine-round">{m.round}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            data-testid="engine-frozen"
            data-frozen={String(m.frozen)}
            className="rounded-full border border-line px-2.5 py-1 text-xs"
            style={{ color: m.frozen ? "var(--frozen-ink)" : "var(--ink-2)" }}
          >
            {m.frozen ? "❄ Learning frozen" : "● Learning on"}
          </span>
          <form action={toggleFreeze}>
            <input type="hidden" name="on" value={String(!m.frozen)} />
            <button className="rounded-md border border-line px-3 py-1.5 text-sm hover:bg-page" data-testid="freeze-button">
              {m.frozen ? "Unfreeze" : "Freeze"}
            </button>
          </form>
          <form action={runRound}>
            <button className="rounded-md bg-ink px-3 py-1.5 text-sm text-surface hover:opacity-90" data-testid="run-round-button">
              Run round
            </button>
          </form>
        </div>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-7">
        <Stat id="loss-merchant" label="Loss, merchant side" value={dollars(m.loss_merchant_side_cents)} />
        <Stat id="loss-buyer" label="Loss, buyer side" value={dollars(m.loss_buyer_side_cents)} />
        <Stat id="fpr" label="False-positive rate" value={pct(m.false_positive_rate)} />
        <Stat id="escalation" label="Escalation rate" value={pct(m.escalation_rate)} />
        <Stat id="exploitability" label="Exploitability" value={dollars(m.exploitability_cents)} />
        <Stat id="shelves-defended" label="Shelves defended" value={pct(m.shelves_defended, 0)} />
        <Stat id="archive-coverage" label="Archive coverage" value={pct(m.archive_coverage, 0)} />
      </div>
    </section>
  );
}
