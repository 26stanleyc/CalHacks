// Head-to-head results as two small multiples (one measure each, never a dual axis).
// Source: evidence/head-to-head/bitdeer-stacking.md on stanley/core-engine: real Bitdeer
// models, seeds 100–102, 6 rounds each, mean ± standard deviation across seeds.

import { useState } from "react";

interface Contender {
  key: string;
  name: string;
  ours?: boolean;
  cost: number; // total cost in dollars: missed fraud plus friction on honest shoppers
  costSd: number;
  fp: number; // mean share of honest shoppers flagged, percent
  fpSd: number;
  fpMax: number;
}

export const CONTENDERS: Contender[] = [
  { key: "full", name: "TagTeam", ours: true, cost: 58312, costSd: 1267, fp: 1.14, fpSd: 0.08, fpMax: 2.67 },
  { key: "rules_plus_xgboost", name: "Rules + XGBoost, the usual stack", cost: 55522, costSd: 13051, fp: 4.23, fpSd: 1.52, fpMax: 14.0 },
  { key: "xgboost_retrained", name: "XGBoost, retrained every round", cost: 66375, costSd: 19027, fp: 3.47, fpSd: 1.59, fpMax: 13.0 },
  { key: "xgboost_frozen", name: "XGBoost, trained once", cost: 104025, costSd: 32269, fp: 7.25, fpSd: 4.39, fpMax: 16.11 },
  { key: "static_rules", name: "Rules that never change", cost: 111239, costSd: 6249, fp: 0.78, fpSd: 0.06, fpMax: 1.67 },
];

const usd = (n: number) => `$${(n / 1000).toFixed(1)}k`;

interface Panel {
  title: string;
  note: string;
  value: (c: Contender) => number;
  label: (c: Contender) => string;
  detail: (c: Contender) => string;
}

const PANELS: Panel[] = [
  {
    title: "Total cost",
    note: "Missed fraud plus friction for honest shoppers. Lower is better.",
    value: (c) => c.cost,
    label: (c) => usd(c.cost),
    detail: (c) => `± ${usd(c.costSd)} across seeds`,
  },
  {
    title: "Honest shoppers flagged",
    note: "Average share of honest payments stopped or stepped up. Lower is better.",
    value: (c) => c.fp,
    label: (c) => `${c.fp.toFixed(2)}%`,
    detail: (c) => `± ${c.fpSd.toFixed(2)} pts, worst round ${c.fpMax.toFixed(1)}%`,
  },
];

function BarPanel({ panel }: { panel: Panel }) {
  const [active, setActive] = useState<string | null>(null);
  const max = Math.max(...CONTENDERS.map(panel.value));
  return (
    <figure className="bars-panel">
      <figcaption>
        <strong>{panel.title}</strong>
        <span>{panel.note}</span>
      </figcaption>
      <ul>
        {CONTENDERS.map((c) => (
          <li
            key={c.key}
            className={`${c.ours ? "ours" : ""}${active === c.key ? " active" : ""}`}
            tabIndex={0}
            onPointerEnter={() => setActive(c.key)}
            onPointerLeave={() => setActive(null)}
            onFocus={() => setActive(c.key)}
            onBlur={() => setActive(null)}
            aria-label={`${c.name}: ${panel.label(c)}, ${panel.detail(c)}`}
          >
            <span className="name">{c.name}</span>
            <span className="track">
              <span className="bar" style={{ width: `calc((100% - 72px) * ${panel.value(c) / max})` }} />
              <span className="value">{panel.label(c)}</span>
            </span>
            {active === c.key && (
              <span className="tip" role="tooltip">
                <b>{panel.label(c)}</b> {panel.detail(c)}
              </span>
            )}
          </li>
        ))}
      </ul>
    </figure>
  );
}

export function ResultsChart() {
  return (
    <div className="results-chart">
      {PANELS.map((p) => (
        <BarPanel key={p.title} panel={p} />
      ))}
      <details className="numbers">
        <summary>See the numbers</summary>
        <table>
          <thead>
            <tr>
              <th scope="col">Defense</th>
              <th scope="col">Total cost</th>
              <th scope="col">Honest shoppers flagged</th>
              <th scope="col">Worst round</th>
            </tr>
          </thead>
          <tbody>
            {CONTENDERS.map((c) => (
              <tr key={c.key}>
                <th scope="row">{c.name}</th>
                <td>
                  {usd(c.cost)} ± {usd(c.costSd)}
                </td>
                <td>
                  {c.fp.toFixed(2)}% ± {c.fpSd.toFixed(2)}
                </td>
                <td>{c.fpMax.toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
