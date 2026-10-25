// Head-to-head results as small multiples (one measure each, never a dual axis).
// Source: engine/live_vs_xgboost.py on PaySim (stanley/crs-trial), the tuned TagTeam (engine/tune.py) vs XGBoost
// retrained every round, each run paired (same attackers); fresh seeds 101-103, rounds 2-6, Claude stand-in models;
// mean ± standard deviation across seeds.

import { useState } from "react";

interface Contender {
  key: string;
  name: string;
  ours?: boolean;
  cost: number; // total cost in dollars: missed fraud plus friction on honest payments
  costSd: number;
  stolen: number; // fraud that got through, dollars
  stolenSd: number;
  fp: number; // share of honest payments flagged, percent
  fpSd: number;
}

export const CONTENDERS: Contender[] = [
  { key: "tagteam", name: "TagTeam", ours: true, cost: 9998, costSd: 1618, stolen: 9013, stolenSd: 1414, fp: 1.69, fpSd: 0.5 },
  { key: "xgboost", name: "XGBoost, retrained every round", cost: 13154, costSd: 120, stolen: 12552, stolenSd: 430, fp: 2.55, fpSd: 0.42 },
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
    note: "Missed fraud plus friction for honest payments. Lower is better.",
    value: (c) => c.cost,
    label: (c) => usd(c.cost),
    detail: (c) => `± ${usd(c.costSd)} across seeds`,
  },
  {
    title: "Fraud let through",
    note: "Money that attackers got out, rounds 2 to 6. Lower is better.",
    value: (c) => c.stolen,
    label: (c) => usd(c.stolen),
    detail: (c) => `± ${usd(c.stolenSd)} across seeds`,
  },
  {
    title: "Honest payments flagged",
    note: "Share of honest payments stopped or stepped up. Lower is better.",
    value: (c) => c.fp,
    label: (c) => `${c.fp.toFixed(2)}%`,
    detail: (c) => `± ${c.fpSd.toFixed(2)} pts across seeds`,
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
              <th scope="col">Fraud let through</th>
              <th scope="col">Honest payments flagged</th>
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
                  {usd(c.stolen)} ± {usd(c.stolenSd)}
                </td>
                <td>
                  {c.fp.toFixed(2)}% ± {c.fpSd.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
