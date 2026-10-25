// Step 0 of the demo: the same recorded traffic, decided by a baseline defense instead of
// TagTeam. Attackers and payments are identical; only the decisions differ, so any gap on screen
// is the gap between the two defenses.

import type { Baseline, Payment, Replay, RoundReplay } from "./types";

const RECENT_ROUNDS = 3; // same window as the arena's shelf catch rates

export function toBaselineReplay(replay: Replay, baseline: Baseline): Replay {
  const recent: Record<string, { attempts: number; caught: number }[]> = {};
  const rounds: RoundReplay[] = replay.rounds.map((r) => {
    const shelfOf = new Map(r.attackers.map((a) => [a.attacker_id, a.shelf]));
    const tally: Record<string, { attempts: number; caught: number }> = {};
    let lossMerchant = 0;
    let lossBuyer = 0;
    let honest = 0;
    let flagged = 0;

    const waves = r.waves.map((w) => ({
      ...w,
      payments: w.payments.map((p): Payment => {
        const d = baseline.decisions[p.tx_id] ?? { action: "allow", settled: true };
        if (p.is_fraud) {
          const shelf = p.attacker_id ? shelfOf.get(p.attacker_id) : undefined;
          if (shelf) {
            const t = (tally[shelf] ??= { attempts: 0, caught: 0 });
            t.attempts++;
            if (!d.settled) t.caught++;
            if (d.settled && shelf.endsWith("/buyer")) lossBuyer += p.amount_cents;
            else if (d.settled) lossMerchant += p.amount_cents;
          }
        } else {
          honest++;
          if (d.action !== "allow") flagged++;
        }
        return {
          ...p,
          action: d.action,
          settled: d.settled,
          objected_by: [],
          escalated: false,
          explanation: d.settled
            ? `${baseline.label} allowed it.`
            : `${baseline.label} scored it as fraud and ${d.action === "block" ? "blocked" : "stepped it up"}.`,
        };
      }),
    }));

    const shelves = r.shelves.map((s) => {
      (recent[s.shelf] ??= []).push(tally[s.shelf] ?? { attempts: 0, caught: 0 });
      const last = recent[s.shelf].slice(-RECENT_ROUNDS);
      const attempts = last.reduce((n, x) => n + x.attempts, 0);
      return { ...s, attempts, caught: last.reduce((n, x) => n + x.caught, 0), unknown: attempts < 20 };
    });

    return {
      ...r,
      metrics: {
        ...r.metrics,
        loss_merchant_side_cents: lossMerchant,
        loss_buyer_side_cents: lossBuyer,
        false_positive_rate: honest ? flagged / honest : 0,
        exploitability_cents: -1, // not measured for the baseline
        frozen: false,
      },
      actions: [], // a baseline model doesn't build attackers
      news: [], // or read scam reports
      rulebooks: [],
      waves,
      shelves,
    };
  });
  return { ...replay, defense: baseline.contender, rounds };
}

// Money that got through in each round, split into honest and stolen. Comparable across
// defenses on the same traffic. Net is honest money through minus money stolen: blocking an
// honest shopper costs the payment they couldn't make, so higher is better.
export const flowByRound = (replay: Replay) =>
  replay.rounds.map((r) => {
    let honest = 0;
    let stolen = 0;
    for (const w of r.waves)
      for (const p of w.payments) {
        if (!p.settled) continue;
        if (p.is_fraud) stolen += p.amount_cents;
        else honest += p.amount_cents;
      }
    return { honest, stolen, net: honest - stolen };
  });
