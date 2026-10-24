import Link from "next/link";
import { Suspense } from "react";

import RunPicker from "@/components/RunPicker";
import { dollars } from "@/lib/format";
import { listRuns, resolveRun, roundMetrics, transactionStream, type StreamRow } from "@/lib/queries";

type SearchParams = PageProps<"/stream">["searchParams"];

const EXAM_WAVE = 3; // bench/config.py EXAM_WAVE: frozen exam traffic, proposed in CONTRACT_NOTES.md

const ACTION_STYLE: Record<string, { label: string; color: string }> = {
  allow: { label: "✓ allow", color: "var(--good)" },
  step_up: { label: "⚠ step-up", color: "var(--ink-2)" },
  block: { label: "✕ block", color: "var(--critical)" },
};

function Row({ r }: { r: StreamRow }) {
  const action = r.action ? ACTION_STYLE[r.action] : null;
  const injection = (r.injection_score ?? 0) >= 0.6;
  return (
    <details className="border-b border-line" data-testid="tx-row" data-escalated={r.escalated}>
      <summary className="grid cursor-pointer grid-cols-[4rem_1fr_6rem_6rem_7rem] items-center gap-2 py-2 text-sm sm:grid-cols-[4rem_1fr_8rem_6rem_7rem_9rem]">
        <span className="tabular text-xs text-muted" title={r.wave === EXAM_WAVE ? "frozen exploitability exam" : `wave ${r.wave}`}>
          r{r.round}·{r.wave === EXAM_WAVE ? "exam" : `w${r.wave}`}
        </span>
        <span className="truncate">
          <span className="text-ink-2">{r.source === "attacker" ? r.attacker_id : r.agent_id}</span>
          <span className="text-muted"> → {r.merchant_id}</span>
          {injection && <span className="ml-2 rounded bg-page px-1.5 text-xs" style={{ color: "var(--critical)" }}>injection</span>}
        </span>
        <span className="hidden text-xs text-muted sm:inline">{r.source}{r.is_fraud ? " · fraud" : ""}</span>
        <span className="tabular text-right">{dollars(r.amount_cents)}</span>
        <span className="text-xs font-medium" style={{ color: action?.color }} data-testid="tx-action">
          {action?.label ?? "—"}
        </span>
        <span className="text-xs text-ink-2">{r.escalated ? "🔎 escalated" : ""}</span>
      </summary>
      <div className="mb-3 ml-16 space-y-2 text-xs text-ink-2">
        <div className="text-muted">{r.tx_id} · score {r.weighted_score?.toFixed(2) ?? "—"}{r.vetoed_by ? ` · veto ${r.vetoed_by}` : ""}</div>
        {r.explanation && <div>{r.explanation}</div>}
        {r.memo && <blockquote className="border-l-2 border-line pl-2 text-muted">memo (untrusted): {r.memo}</blockquote>}
        {r.tool_calls && (
          <div className="card !p-3" data-testid="case-file">
            <div className="mb-1 font-semibold text-ink">Investigator case file → {r.investigator_decision}</div>
            <ol className="list-decimal space-y-1 pl-4">
              {r.tool_calls.map((c, i) => (
                <li key={i}>
                  <code>{c.tool}</code>({Object.values(c.input).join(", ")}) → {c.output_summary}
                </li>
              ))}
            </ol>
            <div className="mt-1">{r.investigator_reasons}</div>
          </div>
        )}
      </div>
    </details>
  );
}

async function Stream({ searchParams }: { searchParams: SearchParams }) {
  const { run, round } = await searchParams;
  const current = await resolveRun(typeof run === "string" ? run : undefined);
  if (!current) return <p className="card text-sm">No runs yet.</p>;
  const roundNo = typeof round === "string" && /^\d+$/.test(round) ? Number(round) : null;
  const [runs, rows, metrics] = await Promise.all([
    listRuns(), transactionStream(current.run_id, roundNo), roundMetrics(current.run_id),
  ]);
  const base = `/stream?run=${encodeURIComponent(current.run_id)}`;
  return (
    <>
      <RunPicker runs={runs} current={current.run_id} path="/stream" />
      <div className="mb-3 flex flex-wrap gap-1 text-xs">
        <Link href={base} className="rounded border border-line px-2 py-0.5" data-current={roundNo == null}>latest</Link>
        {metrics.map((m) => (
          <Link key={m.round} href={`${base}&round=${m.round}`} className="rounded border border-line px-2 py-0.5"
                style={roundNo === m.round ? { borderColor: "var(--series-1)" } : undefined}>
            r{m.round}{m.frozen ? "❄" : ""}
          </Link>
        ))}
      </div>
      <section className="card !p-0 px-3">
        <div className="px-3" data-testid="tx-stream">
          {rows.length ? rows.map((r) => <Row key={r.tx_id} r={r} />) : <p className="py-3 text-sm">No transactions.</p>}
        </div>
      </section>
      <p className="mt-2 text-xs text-muted">transactions ⋈ decisions, ground_truth, features, escalations · newest 100 · click a row for its case file</p>
    </>
  );
}

export default function Page({ searchParams }: PageProps<"/stream">) {
  return (
    <div>
      <h1 className="mb-3 text-xl font-semibold">Transaction stream</h1>
      <Suspense fallback={<p className="card text-sm text-muted">Loading transactions…</p>}>
        <Stream searchParams={searchParams} />
      </Suspense>
    </div>
  );
}
