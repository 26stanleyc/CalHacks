"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { dollars, pct, ranges } from "@/lib/format";
import type { RoundMetrics } from "@/lib/types";

interface Series {
  key: string;
  label: string;
  color: string;
  value: (m: RoundMetrics) => number;
  format: (v: number) => string;
}

interface CurveSpec {
  id: string;
  title: string;
  source: string; // which RoundMetrics fields it plots
  series: Series[];
  axis: (v: number) => string;
}

const toDollars = (cents: number) => cents / 100;
const money = (v: number) => dollars(v * 100);

const CURVES: CurveSpec[] = [
  {
    id: "loss",
    title: "Loss per round, by side",
    source: "loss_merchant_side_cents, loss_buyer_side_cents",
    axis: money,
    series: [
      { key: "merchant", label: "Bad buyers vs merchants", color: "var(--series-1)",
        value: (m) => toDollars(m.loss_merchant_side_cents), format: money },
      { key: "buyer", label: "Bad merchants vs buyers", color: "var(--series-2)",
        value: (m) => toDollars(m.loss_buyer_side_cents), format: money },
    ],
  },
  {
    id: "rates",
    title: "False-positive and escalation rate",
    source: "false_positive_rate, escalation_rate",
    axis: (v) => `${v.toFixed(0)}%`,
    series: [
      { key: "fpr", label: "False-positive rate", color: "var(--series-1)",
        value: (m) => m.false_positive_rate * 100, format: (v) => pct(v / 100) },
      { key: "esc", label: "Escalation rate", color: "var(--series-2)",
        value: (m) => m.escalation_rate * 100, format: (v) => pct(v / 100) },
    ],
  },
  {
    id: "exploitability",
    title: "Exploitability (best single-attacker extraction in the frozen exam)",
    source: "exploitability_cents",
    axis: money,
    series: [
      { key: "expl", label: "Exploitability", color: "var(--series-1)",
        value: (m) => toDollars(m.exploitability_cents), format: money },
    ],
  },
];

function CurveTooltip({ active, label, payload, spec, frozen }: {
  active?: boolean;
  label?: number;
  payload?: { dataKey: string; value: number }[];
  spec: CurveSpec;
  frozen: Set<number>;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="card !p-2 text-xs shadow-sm">
      <div className="mb-1 text-muted">
        Round {label}
        {label != null && frozen.has(label) ? " · frozen" : ""}
      </div>
      {spec.series.map((s) => {
        const p = payload.find((x) => x.dataKey === s.key);
        return (
          <div key={s.key} className="flex items-center gap-2">
            <span className="inline-block h-0.5 w-3" style={{ background: s.color }} />
            <span className="tabular font-semibold">{p ? s.format(p.value) : "—"}</span>
            <span className="text-ink-2">{s.label}</span>
          </div>
        );
      })}
    </div>
  );
}

function Curve({ spec, metrics }: { spec: CurveSpec; metrics: RoundMetrics[] }) {
  const data = metrics.map((m) => ({ round: m.round, ...Object.fromEntries(spec.series.map((s) => [s.key, s.value(m)])) }));
  const frozen = new Set(metrics.filter((m) => m.frozen).map((m) => m.round));
  const frozenRanges = ranges(metrics, (m) => m.frozen);
  return (
    <section className="card" data-testid={`curve-${spec.id}`} data-points={data.length}>
      <h3 className="text-sm font-semibold">{spec.title}</h3>
      <p className="mb-2 text-xs text-muted">round_metrics: {spec.source}</p>
      {spec.series.length > 1 && (
        <div className="mb-1 flex flex-wrap gap-4 text-xs text-ink-2">
          {spec.series.map((s) => (
            <span key={s.key} className="flex items-center gap-1.5">
              <span className="inline-block h-0.5 w-4" style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
          {frozenRanges.length > 0 && (
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-3 w-4 rounded-sm" style={{ background: "var(--frozen)" }} />
              Frozen
            </span>
          )}
        </div>
      )}
      <div className="h-52">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            {frozenRanges.map(([a, b]) => (
              <ReferenceArea key={a} x1={a - 0.5} x2={b + 0.5} fill="var(--frozen)" stroke="none" ifOverflow="hidden" />
            ))}
            <XAxis dataKey="round" type="number" domain={["dataMin - 0.5", "dataMax + 0.5"]} allowDecimals={false}
                   ticks={data.map((d) => d.round)} tick={{ fill: "var(--muted)", fontSize: 11 }}
                   stroke="var(--axis)" tickLine={false} />
            <YAxis tickFormatter={spec.axis} tick={{ fill: "var(--muted)", fontSize: 11 }} width={52}
                   stroke="var(--axis)" tickLine={false} axisLine={false} />
            <Tooltip content={<CurveTooltip spec={spec} frozen={frozen} />}
                     cursor={{ stroke: "var(--axis)", strokeWidth: 1 }} />
            {spec.series.map((s) => (
              <Line key={s.key} dataKey={s.key} name={s.label} stroke={s.color} strokeWidth={2}
                    dot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)", fill: s.color }}
                    activeDot={{ r: 5, strokeWidth: 2, stroke: "var(--surface)" }} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer text-muted">Table view</summary>
        <table className="tabular mt-1 w-full text-left" data-testid={`curve-table-${spec.id}`}>
          <thead className="text-muted">
            <tr>
              <th className="font-normal">Round</th>
              {spec.series.map((s) => <th key={s.key} className="font-normal">{s.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.round} data-round={d.round}>
                <td>{d.round}{frozen.has(d.round) ? " (frozen)" : ""}</td>
                {spec.series.map((s) => (
                  <td key={s.key}>{s.format((d as Record<string, number>)[s.key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}

export default function Curves({ metrics }: { metrics: RoundMetrics[] }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {CURVES.map((spec) => <Curve key={spec.id} spec={spec} metrics={metrics} />)}
    </div>
  );
}
