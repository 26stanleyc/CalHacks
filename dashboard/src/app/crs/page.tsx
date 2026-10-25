import Link from "next/link";
import { Suspense } from "react";

import { dollars } from "@/lib/format";
import { crsChecks, listCrsRuns, type CrsCheckRow } from "@/lib/queries";
import type { ToolCall } from "@/lib/types";

type SearchParams = PageProps<"/crs">["searchParams"];

const ACTION_STYLE: Record<string, { label: string; color: string }> = {
  allow: { label: "✓ allow", color: "var(--good)" },
  step_up: { label: "⚠ step-up", color: "var(--ink-2)" },
  block: { label: "✕ block", color: "var(--critical)" },
};

const TOOL_SHORT: Record<string, string> = {
  crs_data_prefill: "append",
  crs_data_append: "append",
  crs_identity_verification: "identity",
  crs_fraud_finder: "FraudFinder",
};

const TOOL_LABEL: Record<string, string> = {
  crs_data_prefill: "Data Append (reverse)",
  crs_data_append: "Data Append (forward)",
  crs_identity_verification: "TransUnion identity verification",
  crs_fraud_finder: "FraudFinder",
};

// output_summary is the engine's str() of the CRS answer; pull out the fields worth a glance.
function field(summary: string, key: string): string | null {
  return summary.match(new RegExp(`'${key}': '?([^',}]+)`))?.[1] ?? null;
}

function callResult(call: ToolCall): string {
  const s = call.output_summary;
  if (call.tool === "crs_identity_verification") return field(s, "result") ?? "—";
  if (call.tool === "crs_fraud_finder") return `risk ${field(s, "risk_score") ?? "?"} (${field(s, "risk_level") ?? "?"})`;
  return field(s, "status") ?? "—";
}

function Stat({ label, value, note }: { label: string; value: string | number; note?: string }) {
  return (
    <div className="card" data-testid="crs-stat">
      <div className="text-xs text-muted">{label}</div>
      <div className="tabular text-2xl font-semibold">{value}</div>
      {note && <div className="text-xs text-muted">{note}</div>}
    </div>
  );
}

function Check({ r }: { r: CrsCheckRow }) {
  const action = ACTION_STYLE[r.action];
  const asked = r.tool_calls.map((c) => TOOL_SHORT[c.tool] ?? c.tool).join(" → ");
  return (
    <details className="border-b border-line" data-testid="crs-row">
      <summary className="grid cursor-pointer grid-cols-[3rem_1fr_6rem_7rem] items-center gap-2 py-2 text-sm sm:grid-cols-[3rem_1fr_12rem_5rem_6rem_7rem]">
        <span className="tabular text-xs text-muted">r{r.round}</span>
        <span className="truncate text-ink-2">{r.attacker_id ?? r.source}</span>
        <span className="hidden truncate text-xs text-muted sm:inline">{asked || "—"}</span>
        <span className="tabular hidden text-right text-xs text-muted sm:inline">{r.score.toFixed(2)}</span>
        <span className="tabular text-right">{dollars(r.amount_cents)}</span>
        <span className="text-xs font-medium" style={{ color: action?.color }}>{action?.label ?? r.action}</span>
      </summary>
      <div className="mb-3 ml-12 space-y-2 text-xs text-ink-2">
        <div className="text-muted">
          {r.tx_id} · score {r.score.toFixed(2)} in band {r.band[0].toFixed(2)}–{r.band[1].toFixed(2)} ·{" "}
          {r.mocked ? "mocked" : "live sandbox"}
          {r.is_fraud != null && ` · ground truth: ${r.is_fraud ? "fraud" : "honest"}`}
          {r.watch && ` · watch list: ${r.watch}`}
        </div>
        <ol className="space-y-1">
          {r.tool_calls.map((c, i) => (
            <li key={i}>
              {i + 1}. {TOOL_LABEL[c.tool] ?? c.tool}: <span className="text-ink">{callResult(c)}</span>
            </li>
          ))}
        </ol>
        <p className="text-ink">{r.reasons}</p>
      </div>
    </details>
  );
}

async function Checks({ searchParams }: { searchParams: SearchParams }) {
  const { run } = await searchParams;
  const runs = await listCrsRuns();
  const current = runs.find((r) => r.run_id === run) ?? runs.find((r) => r.live > 0) ?? runs[0];
  if (!current) return <p className="card text-sm">No run has CRS checks yet (CRS_ESCALATION is off by default).</p>;
  const rows = await crsChecks(current.run_id);
  const count = (action: string) => rows.filter((r) => r.action === action).length;
  const honest = rows.filter((r) => r.is_fraud === false).length;
  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-2 text-sm" data-testid="run-picker">
        <span className="text-muted">Run</span>
        {runs.map((r) => (
          <Link
            key={r.run_id}
            href={`/crs?run=${encodeURIComponent(r.run_id)}`}
            className="rounded-md border border-line px-2 py-1 hover:bg-surface"
            style={r.run_id === current.run_id ? { borderColor: "var(--series-1)", color: "var(--ink)" } : { color: "var(--ink-2)" }}
          >
            {r.run_id} <span className="text-muted">· {r.checks} checks{r.live ? ` · ${r.live} live` : " · mocked"}</span>
          </Link>
        ))}
      </div>
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="CRS checks" value={rows.length} note={`${rows.filter((r) => !r.mocked).length} live sandbox`} />
        <Stat label="Blocked" value={count("block")} />
        <Stat label="Stepped up" value={count("step_up")} />
        <Stat label="Allowed" value={count("allow")} />
        <Stat label="Honest shoppers checked" value={honest} note="ground_truth.is_fraud = false" />
      </div>
      <section className="card">
        {rows.map((r) => <Check key={r.tx_id} r={r} />)}
      </section>
      <p className="mt-3 text-xs text-muted">
        decisions.payload.crs: payments whose combined score fell in the CRS band (or whose payer is on the CRS watch
        list), with the CRS calls in order and the reasons CRS gave. Which CRS test person played a payer is left out:
        it would name the attacker. Enrollment (every person checked once, and the spending limit CRS sets) is measured
        in evidence/crs/.
      </p>
    </>
  );
}

export default function Page({ searchParams }: PageProps<"/crs">) {
  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">CRS sandbox calls</h1>
      <p className="mb-3 text-sm text-muted">
        Close calls get a second CRS look at the person behind the agent: what was asked, what CRS answered, and the
        decision that followed.
      </p>
      <Suspense fallback={<p className="card text-sm text-muted">Loading CRS checks…</p>}>
        <Checks searchParams={searchParams} />
      </Suspense>
    </div>
  );
}
