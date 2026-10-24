"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { dollars } from "@/lib/format";

export interface DriftPoint {
  t: number; // round + wave / 3
  round: number;
  wave: number;
  amount: number; // dollars
  changed: boolean;
  strategy: string;
}

function DriftTooltip({ active, payload }: { active?: boolean; payload?: { payload: DriftPoint }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="card !p-2 max-w-64 text-xs shadow-sm">
      <div className="tabular font-semibold">{dollars(p.amount * 100)}</div>
      <div className="text-muted">round {p.round}, wave {p.wave}{p.changed ? " · strategy rewritten" : ""}</div>
      <div className="mt-1 text-ink-2">{p.strategy}</div>
    </div>
  );
}

/** One lineage's playbook amount per wave; ringed points are waves where it rewrote its strategy. */
export default function DriftChart({ points }: { points: DriftPoint[] }) {
  return (
    <div className="h-36">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} allowDecimals={false}
                 tickFormatter={(v: number) => `r${Math.floor(v)}`} tick={{ fill: "var(--muted)", fontSize: 10 }}
                 stroke="var(--axis)" tickLine={false} />
          <YAxis tickFormatter={(v: number) => dollars(v * 100)} tick={{ fill: "var(--muted)", fontSize: 10 }}
                 width={44} stroke="var(--axis)" tickLine={false} axisLine={false} domain={["auto", "auto"]} />
          <Tooltip content={<DriftTooltip />} cursor={{ stroke: "var(--axis)", strokeWidth: 1 }} />
          <Line dataKey="amount" stroke="var(--series-1)" strokeWidth={2} isAnimationActive={false}
                dot={(props: { cx?: number; cy?: number; index?: number; payload?: DriftPoint }) => (
                  <circle key={props.index} cx={props.cx} cy={props.cy} r={props.payload?.changed ? 5 : 0}
                          fill="var(--surface)" stroke="var(--series-2)" strokeWidth={2} />
                )} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
